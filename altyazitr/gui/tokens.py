"""Tasarım sistemi token'ları — "Ocean Drive gecesi".

Tüm renk, yazı, boşluk, köşe ve hareket değerlerinin TEK kaynağı. Arayüz kodunda doğrudan HEX
yazmayın; buradan alın. Ayrıntılar ve kullanım kuralları: DESIGN.md

Yüzeyler logodaki mor-lacivert geceye göre ayarlanmıştır; vurgular logodaki neon pembe, camgöbeği
ve gün batımı turuncusundan gelir.
"""

from __future__ import annotations

import ctypes


class C:
    """Renkler. Renk yalnızca anlam taşır: pembe = birincil eylem/marka, turkuaz = canlı/başarılı,
    camgöbeği = odak/seçim/bağlantı, mercan = uyarı. Yüzeyler sessiz kalır."""

    # yüzeyler (koyudan açığa) — hafif mor tonlu gece laciverti
    BG = "#0C0B1D"          # gece yarısı — uygulama zemini
    PANEL = "#13122A"       # lacivert asfalt — kenar çubuğu, canlı başlık bandı
    RAISED = "#1C1A38"      # okyanus gecesi — giriş alanları, açılır menüler, önizleme çerçevesi
    HOVER = "#262448"       # üzerine gelme
    PRESSED = "#17152F"     # basılı
    LINE = "#23203F"        # ince ayırıcı çizgi
    LINE_STRONG = "#3B3762"  # giriş kenarlığı
    # yazı (kontrast: TEXT_2 ≥ 6.5:1, TEXT_3 ≥ 4.5:1 RAISED üzerinde)
    TEXT = "#F5F3FB"
    TEXT_2 = "#AEAAC6"
    TEXT_3 = "#8A86A6"      # yer tutucu / ipucu
    TEXT_DISABLED = "#5C5878"  # devre dışı denetimler
    INVERSE = "#0C0B1D"     # pembe/turkuaz/camgöbeği üstündeki yazı
    # vurgular (logodaki neon renkler)
    PINK = "#FF4D94"        # gün batımı pembesi — birincil eylem, marka
    PINK_HOVER = "#FF6EA9"
    PINK_PRESSED = "#E63D82"
    ORANGE = "#FF8A5B"      # gün batımı turuncusu — uyarı; gün batımı geçişinin ikinci durağı
    CYAN = "#3FD8EE"        # neon camgöbeği — klavye odağı, seçim, bağlantılar
    TURQUOISE = "#2BC4AE"   # tropik turkuaz — canlı/çalışıyor, başarı
    LIVE = "#3DDC84"        # yeşil — kenar çubuğunda "Çalışıyor" noktası (çeviri sürüyor)
    DANGER = "#FF5A5F"      # durdur / hata
    # yarı saydam katmanlar (yalnızca QSS içinde; QColor bu biçimi okumaz → qcolor() kullanın)
    CYAN_SOFT = "rgba(63, 216, 238, 0.14)"
    TURQ_SOFT = "rgba(43, 196, 174, 0.16)"
    ORANGE_SOFT = "rgba(255, 138, 91, 0.16)"
    DANGER_SOFT = "rgba(255, 90, 95, 0.14)"


class F:
    """Yazı tipleri (Windows'ta hazır gelir; paketlenmez).

    * Bahnschrift — DIN çizgisinde, Florida yol tabelalarını çağrıştıran ekran yazısı: başlıklar,
      düğmeler, sayılar. Genişlik varyantları ayrı aile adıyla gelir.
    * Segoe UI Variable — gövde metni (Windows 11); yoksa Segoe UI.
    """

    DISPLAY = "Bahnschrift"
    DISPLAY_SEMIBOLD = "Bahnschrift SemiBold"
    DISPLAY_CONDENSED = "Bahnschrift SemiBold SemiCondensed"
    BODY = "Segoe UI Variable Text"
    BODY_FALLBACK = "Segoe UI"
    BODY_STRONG = "Segoe UI Variable Display Semibold"  # vurgulu gövde (son çevirilerde konuşmacı adı)

    # tip ölçeği (piksel)
    PAGE_TITLE = 26     # sayfa başlığı (Bahnschrift SemiBold SemiCondensed)
    SECTION = 16        # bölüm başlığı (Bahnschrift SemiBold), cümle düzeni
    BODY_SIZE = 13      # gövde
    SMALL = 12          # yardımcı metin
    BUTTON = 14         # düğme (Bahnschrift)
    STATUS = 22         # canlı başlıktaki durum ("Çalışıyor")
    BODY_PT = 10        # uygulama yazı tipi (pt) — Segoe UI Variable Text 10 pt ≈ 13 px


SPACE = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 32}
RADIUS = {"s": 4, "m": 6, "l": 10}   # girişler/küçük düğmeler 4–6, açılır yüzeyler 10; kart ızgarası yok
ICON = {"s": 16, "m": 20, "l": 24}
MOTION = {"fast": 120, "base": 180}  # ms; yalnızca kullanıcı eylemine yanıt olarak

# Gün batımı geçişi: YALNIZCA canlı başlık bandının alt çizgisinde kullanılır (marka işareti hazır logodur).
SUNSET_STOPS = ((0.0, C.PINK), (1.0, C.ORANGE))


def rgba(hex_color: str, alpha: float) -> str:
    """Token rengini QSS için yarı saydam yazar: rgba('#FF4D94', .2) → 'rgba(255, 77, 148, 0.2)'."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def qcolor(token: str, alpha: float | None = None):
    """Token'ı QColor'a çevirir; 'rgba(...)' biçimini de okur (QPainter ile çizim için)."""
    from PySide6.QtGui import QColor

    t = token.strip()
    if t.startswith("rgba("):
        parts = [p.strip() for p in t[5:-1].split(",")]
        col = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
        a = float(parts[3])
        col.setAlphaF(a if a <= 1 else a / 255)
    else:
        col = QColor(t)
    if alpha is not None:
        col.setAlphaF(alpha)
    return col


def reduced_motion() -> bool:
    """Windows 'Animasyonları göster' kapalıysa True (Ayarlar → Erişilebilirlik → Görsel efektler)."""
    try:
        val = ctypes.c_bool(True)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(val), 0):
            return not val.value
    except Exception:
        pass
    return False


def motion_ms(kind: str = "base") -> int:
    return 0 if reduced_motion() else MOTION.get(kind, 180)
