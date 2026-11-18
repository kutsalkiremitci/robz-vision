"""Altyazı çizimi (QPainter). Ekran altyazısı ve arayüzdeki canlı önizleme aynı kodu kullanır;
böylece önizlemede ne görüyorsanız ekranda da o çıkar. Hangi görünümün çizileceğini çağıran seçer:
ekrandaki kutu Settings.screen_style ile çizilir; OBS sayfası Settings.style'ı CSS ile uygular.

Akış (Settings.flow): son çeviriler alt alta durur. En yeni satır en altta ve tam görünür, üsttekiler
giderek soluklaşır (STACK_ALPHA; OBS sayfası da aynı değerleri kullanır).

Vurgulu kelimeler (satırın `spans` aralıkları) görünümün vurgu rengiyle doldurulur; kenar çizgisi ve gölge bütün
satır için bir kez çizilir, böylece renkli parça da aynı kenarı ve gölgeyi taşır."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from altyazitr.config import FlowCfg, StyleCfg

_WEIGHTS = (100, 200, 300, 400, 500, 600, 700, 800, 900)

# Akışta satırların görünürlüğü: en yeni (altta) 1, üstündekiler giderek soluk (web/overlay.html: DIM)
STACK_ALPHA = (1.0, 0.65, 0.45, 0.32)
MAX_STACK = 6  # elle düzenlenmiş ayar dosyasında bile yığın bundan uzun olmaz
BLOCK_GAP = 0.22  # iki satırın arka planları arasındaki boşluk (yazı boyutuna oranla; sayfada .22em)


def stack_limit(flow: FlowCfg) -> int:
    """Aynı anda en fazla kaç satır görünür: tek satır modunda 1, akışta flow.max_lines (1..MAX_STACK)."""
    if flow.mode == "single":
        return 1
    try:
        n = int(flow.max_lines)
    except (TypeError, ValueError):
        n = FlowCfg().max_lines
    return max(1, min(MAX_STACK, n))


def stack_alpha(k: int) -> float:
    """Alttan k. satırın görünürlüğü (0 = en yeni)."""
    return STACK_ALPHA[min(max(0, k), len(STACK_ALPHA) - 1)]


def make_font(st: StyleCfg, pixel_size: float) -> QFont:
    font = QFont(st.font_family)
    font.setPixelSize(max(6, int(round(pixel_size))))
    w = min(_WEIGHTS, key=lambda v: abs(v - int(st.font_weight)))
    font.setWeight(QFont.Weight(w))
    return font


def _wrap_words(fm: QFontMetricsF, text: str, maxw: float) -> list[list[tuple[int, int]]]:
    """Metni `maxw` genişliğe sarar. Her satır, kelimelerinin metindeki yerleridir [(başlangıç, bitiş)]; satırın
    yazısı bu kelimelerin tek boşlukla birleşimidir (bkz. wrap_text)."""
    out: list[list[tuple[int, int]]] = []
    pos = 0
    for para in text.splitlines(keepends=True):
        cur: list[tuple[int, int]] = []
        cur_txt = ""
        for m in re.finditer(r"\S+", para):
            word = m.group()
            t = f"{cur_txt} {word}" if cur else word
            if cur and fm.horizontalAdvance(t) > maxw:
                out.append(cur)
                cur, cur_txt = [], word
            else:
                cur_txt = t
            cur.append((pos + m.start(), pos + m.end()))
        if cur:
            out.append(cur)
        pos += len(para)
    return out


def wrap_text(fm: QFontMetricsF, text: str, maxw: float) -> list[str]:
    return [" ".join(text[s:e] for s, e in words) for words in _wrap_words(fm, text, maxw)]


# Satır parçalarının türü: yazı rengi, konuşmacı rengi, vurgu rengi
_TEXT, _SPEAKER, _HL = 0, 1, 2


def _line_runs(words: list[tuple[int, int]], kinds: Sequence[int]) -> list[tuple[int, int, int]]:
    """Kaydırılmış bir satırın renk parçaları [(satırdaki başlangıç, bitiş, tür)]. words: satırın kelimelerinin
    metindeki yerleri, kinds: metnin her karakterinin türü. Satır, kelimelerin tek boşlukla birleşimidir; aradaki
    boşluk iki yanı aynı türdeyse o türe katılır (parça sayısı az kalsın)."""
    runs: list[list[int]] = []

    def add(kind: int) -> None:
        if runs and runs[-1][2] == kind:
            runs[-1][1] += 1
        else:
            start = runs[-1][1] if runs else 0
            runs.append([start, start + 1, kind])

    for i, (s, e) in enumerate(words):
        if i:
            add(runs[-1][2] if runs[-1][2] == kinds[s] else _TEXT)
        for c in range(s, e):
            add(kinds[c])
    return [(s, e, k) for s, e, k in runs]


@dataclass
class _Block:
    """Tek altyazı satırının (konuşmacı + çeviri + isteğe bağlı orijinal) yerleşimi. runs: her satırın renk
    parçaları (bkz. _line_runs)."""
    lines: list[str]
    orig_lines: list[str]
    total_h: float
    widest: float
    runs: list[list[tuple[int, int, int]]]


class _Painter:
    """Bir görünüm ve ölçek için ortak yazı tipi/kalem hesapları; satırları yerleştirir ve çizer."""

    def __init__(self, W: float, H: float, st: StyleCfg, scale: float, ref_height: float | None, in_box: bool):
        self.W, self.st = W, st
        self.k = ((ref_height or H) / 1080.0) * max(0.1, scale)
        self.font = make_font(st, st.font_size * self.k)
        self.fm = QFontMetricsF(self.font)
        px = self.font.pixelSize()
        self.maxw = (W - px * 1.4) if in_box else (W * st.max_width_percent / 100.0 - px)
        self.lh = self.fm.lineSpacing()
        self.ofont = QFont(self.font)
        self.ofont.setPixelSize(max(6, int(px * st.original_size_percent / 100)))
        self.ofm = QFontMetricsF(self.ofont)
        self.olh = self.ofm.lineSpacing()
        self.padx, self.pady = px * 0.6, px * 0.18
        self.gap = px * BLOCK_GAP
        self.outline_px = st.outline_width * self.k * 2
        self.shadow_off = max(1.0, self.outline_px * 0.4 + px * 0.03)

    def layout(self, text: str, speaker: str | None, original: str,
               spans: Sequence[Sequence[int]] = ()) -> _Block | None:
        if not text:
            return None
        prefix = f"{speaker}: " if speaker else ""
        full = prefix + text
        words = _wrap_words(self.fm, full, self.maxw)
        if not words:
            return None
        lines = [" ".join(full[s:e] for s, e in ws) for ws in words]
        kinds = [_SPEAKER] * len(prefix) + [_TEXT] * len(text)
        if self.st.highlight:  # vurgu kapalıysa vurgulu kelimeler yazı renginde kalır
            for sp in spans or ():
                try:
                    s, e = max(0, int(sp[0])), min(len(text), int(sp[1]))
                except (TypeError, ValueError, IndexError):
                    continue
                if s < e:
                    kinds[len(prefix) + s:len(prefix) + e] = [_HL] * (e - s)
        orig_lines = wrap_text(self.ofm, original, self.maxw) if (self.st.show_original and original) else []
        gap = self.lh * 0.12 if orig_lines else 0.0
        total_h = self.lh * len(lines) + self.olh * len(orig_lines) + gap
        widest = max([self.fm.horizontalAdvance(t) for t in lines]
                     + [self.ofm.horizontalAdvance(t) for t in orig_lines])
        return _Block(lines, orig_lines, total_h, widest, [_line_runs(ws, kinds) for ws in words])

    def margin(self) -> float:
        """Arka planın ve kenar/gölgenin yazı kutusundan taşabileceği en büyük pay."""
        return self.pady + self.outline_px + self.shadow_off * 1.3 + 4

    # --- çizim -----------------------------------------------------------------------------
    def draw(self, p: QPainter, b: _Block, top: float, alpha: float = 1.0) -> None:
        """Satırı yazı üstü `top` olacak şekilde çizer. alpha < 1 ise satır bir katmana çizilip
        bütün olarak soldurulur (kenar ve dolgu üst üste binip koyulaşmasın)."""
        if alpha <= 0.001:
            return
        if alpha >= 0.999:
            self._draw(p, b, top)
            return
        m = self.margin()
        y0 = top - m
        h = b.total_h + 2 * m
        t = p.worldTransform()
        dev = p.device()
        dpr = (dev.devicePixelRatioF() if dev is not None else 1.0) * max(abs(t.m11()), abs(t.m22()), 1e-3)
        img = QImage(max(1, math.ceil(self.W * dpr)), max(1, math.ceil(h * dpr)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(0)
        q = QPainter(img)
        q.translate(0, -y0)
        self._draw(q, b, top)
        q.end()
        p.save()
        p.setOpacity(p.opacity() * alpha)
        p.drawImage(QPointF(0, y0), img)
        p.restore()

    def _draw(self, p: QPainter, b: _Block, top: float) -> None:
        st, W = self.st, self.W
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bg = QColor(st.bg_color)
        bg.setAlphaF(max(0.0, min(1.0, st.bg_opacity)))
        if bg.alpha() > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            r = self.font.pixelSize() * 0.35
            p.drawRoundedRect(QRectF((W - b.widest) / 2 - self.padx, top - self.pady,
                                     b.widest + 2 * self.padx, b.total_h + 2 * self.pady), r, r)

        outline_px, shadow_off = self.outline_px, self.shadow_off
        pen = QPen(QColor(st.outline_color), outline_px, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        shadow_c = QColor(0, 0, 0, 150)

        def draw_line(txt: str, f: QFont, fmx: QFontMetricsF, y: float,
                      runs: Sequence[tuple[int, int, QColor]]) -> None:
            """Satırı çizer: gölge ve kenar bütün satıra bir kez, dolgu renk parçalarına (konuşmacı, vurgu) göre."""
            x0 = (W - fmx.horizontalAdvance(txt)) / 2
            base = QPointF(x0, y + fmx.ascent())
            path = QPainterPath()
            path.addText(base, f, txt)
            if st.shadow:
                p.fillPath(path.translated(shadow_off, shadow_off * 1.3), shadow_c)
                if outline_px > 0:
                    p.strokePath(path.translated(shadow_off, shadow_off * 1.3),
                                 QPen(shadow_c, outline_px, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                      Qt.PenJoinStyle.RoundJoin))
            if outline_px > 0:
                p.strokePath(path, pen)
            for s, e, color in runs:
                if s == 0 and e >= len(txt):
                    p.fillPath(path, color)
                    continue
                part = QPainterPath()
                part.addText(QPointF(x0 + fmx.horizontalAdvance(txt[:s]), base.y()), f, txt[s:e])
                p.fillPath(part, color)

        colors = {_TEXT: QColor(st.text_color), _SPEAKER: QColor(st.speaker_color),
                  _HL: QColor(st.highlight_color)}
        y = top
        for line, runs in zip(b.lines, b.runs):
            draw_line(line, self.font, self.fm, y, [(s, e, colors[k]) for s, e, k in runs])
            y += self.lh
        if b.orig_lines:
            y += self.lh * 0.12
            oc = QColor(255, 255, 255, 210)
            for line in b.orig_lines:
                draw_line(line, self.ofont, self.ofm, y, [(0, len(line), oc)])
                y += self.olh


def _field(line: Mapping | Sequence, key: str, idx: int, default=None):
    if isinstance(line, Mapping):
        return line.get(key, default)
    return line[idx] if len(line) > idx else default


def paint_subtitle(p: QPainter, W: float, H: float, st: StyleCfg, text: str, speaker: str | None = None,
                   original: str = "", scale: float = 1.0, *, ref_height: float | None = None,
                   in_box: bool = False, older: Sequence[Mapping | Sequence] = (), progress: float = 1.0,
                   spans: Sequence[Sequence[int]] = ()) -> int:
    """Altyazıyı çizer; çizilen satır (altyazı) sayısını döndürür.

    * Varsayılan (OBS gibi tam kare): W×H alanın altına, style.bottom_percent / max_width_percent ile.
    * in_box=True (ekran altyazısı kutusu): W×H kutunun içine, alta yaslı ve kutu genişliğine sarılı;
      yazı boyutu ref_height (ekran yüksekliği) / 1080 oranında ölçeklenir.
    * older (akış): en yeninin (text/speaker/original) üstünde duran eski satırlar, eskiden yeniye;
      her biri {"text", "speaker", "original", "spans"} sözlüğü ya da (text, speaker, original, spans) demeti.
      Üstteki satırlar STACK_ALPHA ile soluklaşır; sığmayan en eskiler çizilmez (en yeni her zaman çizilir).
    * progress (0..1): en yeni satırın yığına eklenme geçişi. En yeni belirir, eskiler yukarı kayar
      ve bir kademe soluklaşır; 1 = geçiş bitti.
    * spans: en yeni satırın vurgulu parçaları [(başlangıç, bitiş)], `text` içinde; style.highlight_color ile
      çizilir (style.highlight kapalıysa yazı renginde).
    """
    if not text:
        return 0
    pt = _Painter(W, H, st, scale, ref_height, in_box)
    newest = pt.layout(text, speaker, original, spans)
    if newest is None:
        return 0
    blocks = [newest]  # alttan üste
    for ln in reversed(list(older)):
        b = pt.layout(_field(ln, "text", 0, "") or "", _field(ln, "speaker", 1), _field(ln, "original", 2, "") or "",
                      _field(ln, "spans", 3, ()) or ())
        if b is not None:
            blocks.append(b)

    bottom = (H - pt.pady - 2) if in_box else (H - H * st.bottom_percent / 100.0)
    placed: list[tuple[int, _Block, float]] = []
    y_bottom = bottom
    for k, b in enumerate(blocks):
        top = y_bottom - b.total_h
        if k and top - pt.pady < 0:  # kutuya (kareye) sığmayan eski satırlar çizilmez
            break
        placed.append((k, b, top))
        y_bottom = top - 2 * pt.pady - pt.gap

    prog = max(0.0, min(1.0, progress))
    # Geçişte yığın bütün olarak bir satır yukarı kayar: yeni satır alttan belirerek gelir, eskiler onun
    # yerinden kalkar (üst üste binmez). Kutunun altından taşan kısım pencere sınırında kırpılır.
    shift = (1.0 - prog) * (newest.total_h + 2 * pt.pady + pt.gap)
    for k, b, top in reversed(placed):  # eskiden yeniye: en yeni en üstte kalsın
        if k == 0:
            pt.draw(p, b, top + shift, prog)
        else:
            a = stack_alpha(k - 1) + (stack_alpha(k) - stack_alpha(k - 1)) * prog
            pt.draw(p, b, top + shift, a)
    return len(placed)
