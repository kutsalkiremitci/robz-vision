"""Altyazı çizimi (QPainter). Ekran altyazısı ve arayüzdeki canlı önizleme aynı kodu kullanır;
böylece önizlemede ne görüyorsanız ekranda da o çıkar. OBS sayfası aynı ayarları CSS ile uygular."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from altyazitr.config import StyleCfg

_WEIGHTS = (100, 200, 300, 400, 500, 600, 700, 800, 900)


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


def paint_subtitle(p: QPainter, W: float, H: float, st: StyleCfg, text: str, speaker: str | None = None,
                   original: str = "", scale: float = 1.0, *, ref_height: float | None = None,
                   in_box: bool = False) -> None:
    """Altyazıyı çizer.

    * Varsayılan (OBS gibi tam kare): W×H alanın altına, style.bottom_percent / max_width_percent ile.
    * in_box=True (ekran altyazısı kutusu): W×H kutunun içine, alta yaslı ve kutu genişliğine sarılı;
      yazı boyutu ref_height (ekran yüksekliği) / 1080 oranında ölçeklenir.
    """
    if not text:
        return
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    k = ((ref_height or H) / 1080.0) * max(0.1, scale)
    font = make_font(st, st.font_size * k)
    fm = QFontMetricsF(font)
    if in_box:
        maxw = W - font.pixelSize() * 1.4
    else:
        maxw = W * st.max_width_percent / 100.0 - font.pixelSize()
    prefix = f"{speaker}: " if speaker else ""
    lines = wrap_text(fm, prefix + text, maxw)
    if not lines:
        return
    lh = fm.lineSpacing()
    orig_lines: list[str] = []
    ofont = QFont(font)
    ofm, olh = fm, 0.0
    if st.show_original and original:
        ofont.setPixelSize(max(6, int(font.pixelSize() * st.original_size_percent / 100)))
        ofm = QFontMetricsF(ofont)
        orig_lines = wrap_text(ofm, original, maxw)
        olh = ofm.lineSpacing()
    gap = lh * 0.12 if orig_lines else 0.0
    total_h = lh * len(lines) + olh * len(orig_lines) + gap
    widest = max([fm.horizontalAdvance(t) for t in lines] + [ofm.horizontalAdvance(t) for t in orig_lines])
    padx, pady = font.pixelSize() * 0.6, font.pixelSize() * 0.18
    bottom = (H - pady - 2) if in_box else (H - H * st.bottom_percent / 100.0)
    top = bottom - total_h

    bg = QColor(st.bg_color)
    bg.setAlphaF(max(0.0, min(1.0, st.bg_opacity)))
    if bg.alpha() > 0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        r = font.pixelSize() * 0.35
        p.drawRoundedRect(QRectF((W - widest) / 2 - padx, top - pady, widest + 2 * padx, total_h + 2 * pady), r, r)

    outline_px = st.outline_width * k * 2
    pen = QPen(QColor(st.outline_color), outline_px, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    shadow_c = QColor(0, 0, 0, 150)
    shadow_off = max(1.0, outline_px * 0.4 + font.pixelSize() * 0.03)

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
    for i, line in enumerate(lines):
        sl = len(prefix) if (i == 0 and prefix and line.startswith(prefix.strip())) else 0
        draw_line(line, font, fm, y, text_color, min(sl, len(line)))
        y += lh
    if orig_lines:
        y += gap
        oc = QColor(255, 255, 255, 210)
        for line in orig_lines:
            draw_line(line, ofont, ofm, y, oc)
            y += olh
