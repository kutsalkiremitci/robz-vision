"""Altyazı çizimi (QPainter). Ekran altyazısı ve arayüzdeki canlı önizleme aynı kodu kullanır;
böylece önizlemede ne görüyorsanız ekranda da o çıkar. Hangi görünümün çizileceğini çağıran seçer:
ekrandaki kutu Settings.screen_style ile çizilir; OBS sayfası Settings.style'ı CSS ile uygular.

Akış (Settings.flow): son çeviriler alt alta durur. En yeni satır en altta ve tam görünür, üsttekiler
giderek soluklaşır (STACK_ALPHA; OBS sayfası da aynı değerleri kullanır)."""

from __future__ import annotations

import math
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


def wrap_text(fm: QFontMetricsF, text: str, maxw: float) -> list[str]:
    out: list[str] = []
    for para in text.splitlines() or [text]:
        cur = ""
        for word in para.split():
            t = f"{cur} {word}".strip()
            if cur and fm.horizontalAdvance(t) > maxw:
                out.append(cur)
                cur = word
            else:
                cur = t
        if cur:
            out.append(cur)
    return out


@dataclass
class _Block:
    """Tek altyazı satırının (konuşmacı + çeviri + isteğe bağlı orijinal) yerleşimi."""
    prefix: str
    lines: list[str]
    orig_lines: list[str]
    total_h: float
    widest: float


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

    def layout(self, text: str, speaker: str | None, original: str) -> _Block | None:
        if not text:
            return None
        prefix = f"{speaker}: " if speaker else ""
        lines = wrap_text(self.fm, prefix + text, self.maxw)
        if not lines:
            return None
        orig_lines = wrap_text(self.ofm, original, self.maxw) if (self.st.show_original and original) else []
        gap = self.lh * 0.12 if orig_lines else 0.0
        total_h = self.lh * len(lines) + self.olh * len(orig_lines) + gap
        widest = max([self.fm.horizontalAdvance(t) for t in lines]
                     + [self.ofm.horizontalAdvance(t) for t in orig_lines])
        return _Block(prefix, lines, orig_lines, total_h, widest)

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

        def draw_line(txt: str, f: QFont, fmx: QFontMetricsF, y: float, color: QColor, speaker_len: int = 0) -> None:
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
            if speaker_len:
                sp_txt = txt[:speaker_len]
                sp_path = QPainterPath()
                sp_path.addText(base, f, sp_txt)
                rest_path = QPainterPath()
                rest_path.addText(QPointF(x0 + fmx.horizontalAdvance(sp_txt), base.y()), f, txt[speaker_len:])
                p.fillPath(sp_path, QColor(st.speaker_color))
                p.fillPath(rest_path, color)
            else:
                p.fillPath(path, color)

        y = top
        text_color = QColor(st.text_color)
        prefix = b.prefix
        for i, line in enumerate(b.lines):
            sl = len(prefix) if (i == 0 and prefix and line.startswith(prefix.strip())) else 0
            draw_line(line, self.font, self.fm, y, text_color, min(sl, len(line)))
            y += self.lh
        if b.orig_lines:
            y += self.lh * 0.12
            oc = QColor(255, 255, 255, 210)
            for line in b.orig_lines:
                draw_line(line, self.ofont, self.ofm, y, oc)
                y += self.olh


def _field(line: Mapping | Sequence, key: str, idx: int, default=None):
    if isinstance(line, Mapping):
        return line.get(key, default)
    return line[idx] if len(line) > idx else default


def paint_subtitle(p: QPainter, W: float, H: float, st: StyleCfg, text: str, speaker: str | None = None,
                   original: str = "", scale: float = 1.0, *, ref_height: float | None = None,
                   in_box: bool = False, older: Sequence[Mapping | Sequence] = (), progress: float = 1.0) -> int:
    """Altyazıyı çizer; çizilen satır (altyazı) sayısını döndürür.

    * Varsayılan (OBS gibi tam kare): W×H alanın altına, style.bottom_percent / max_width_percent ile.
    * in_box=True (ekran altyazısı kutusu): W×H kutunun içine, alta yaslı ve kutu genişliğine sarılı;
      yazı boyutu ref_height (ekran yüksekliği) / 1080 oranında ölçeklenir.
    * older (akış): en yeninin (text/speaker/original) üstünde duran eski satırlar, eskiden yeniye;
      her biri {"text", "speaker", "original"} sözlüğü ya da (text, speaker, original) demeti. Üstteki
      satırlar STACK_ALPHA ile soluklaşır; sığmayan en eskiler çizilmez (en yeni her zaman çizilir).
    * progress (0..1): en yeni satırın yığına eklenme geçişi. En yeni belirir, eskiler yukarı kayar
      ve bir kademe soluklaşır; 1 = geçiş bitti.
    """
    if not text:
        return 0
    pt = _Painter(W, H, st, scale, ref_height, in_box)
    newest = pt.layout(text, speaker, original)
    if newest is None:
        return 0
    blocks = [newest]  # alttan üste
    for ln in reversed(list(older)):
        b = pt.layout(_field(ln, "text", 0, "") or "", _field(ln, "speaker", 1), _field(ln, "original", 2, "") or "")
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
