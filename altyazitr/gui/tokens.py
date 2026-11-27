"""Tasarım sistemi token'ları — "Gölge".

Tüm renk, yazı, boşluk, köşe ve hareket değerlerinin TEK kaynağı. Arayüz kodunda doğrudan HEX
yazmayın; buradan alın. Ayrıntılar ve kullanım kuralları: DESIGN.md

Televizyon uygulaması gibi koyu arayüz: yüzeyler renksiz, neredeyse siyahtır. Vurgular Vice City renklerinden gelir:
neon pembe birincil eylem ve marka, neon camgöbeği odak ve seçim, turkuaz başarı, gün batımı turuncusu uyarı.
Parıltı kullanılmaz; gün batımı geçişi yalnız canlı bandın alt çizgisindedir.
"""

from __future__ import annotations

import ctypes


class C:
    """Renkler. Renk yalnızca anlam taşır: pembe = birincil eylem/marka, turkuaz = başarılı/tamam, camgöbeği =
    odak/seçim/bağlantı, turuncu = uyarı, yeşil = çeviri çalışıyor. Yüzeyler sessiz kalır."""

    # yüzeyler (koyudan açığa) — renksiz, neredeyse siyah
    BG = "#050507"          # zemin
    PANEL = "#0A0A0D"       # kenar menüsü, canlı bant, salt okunur alanlar
    RAISED = "#111115"      # giriş alanları, karolar, açılır menüler
    HOVER = "#18181E"       # üzerine gelme
    PRESSED = "#0D0D10"     # basılı
    LINE = "#15151A"        # ince ayırıcı çizgi
    LINE_STRONG = "#24242C"  # giriş ve karo kenarlığı
    # yazı (kontrast RAISED üzerinde: TEXT_2 ≥ 7:1, TEXT_3 ≥ 4.5:1)
    TEXT = "#EEEEF2"
    TEXT_2 = "#A2A2AD"
    TEXT_3 = "#82828D"      # yer tutucu / ipucu
    TEXT_DISABLED = "#4A4A54"  # devre dışı denetimler
    INVERSE = "#050507"     # pembe/camgöbeği/turkuaz üstündeki yazı
    # Vice City vurguları (logodaki neon renkler)
    PINK = "#FF4D94"        # neon pembe — birincil eylem, marka, seçili menü çizgisi, sıradaki adım
    PINK_HOVER = "#FF6AA6"
    PINK_PRESSED = "#E23E80"
    CYAN = "#3FD8EE"        # neon camgöbeği — klavye odağı, seçim, bağlantılar
    TURQUOISE = "#2BC4AE"   # turkuaz — başarı, tamamlanan adım
    LIVE = "#3DDC84"        # yeşil — "Çalışıyor" noktası (çeviri sürüyor)
    ORANGE = "#FF8A5B"      # gün batımı turuncusu — uyarı
    DANGER = "#FF5A5F"      # durdur / hata
    # yarı saydam katmanlar (yalnızca QSS içinde; QColor bu biçimi okumaz → qcolor() kullanın)
    CYAN_SOFT = "rgba(63, 216, 238, 0.12)"
    # üzerine gelme: düğme, menü satırı ve karoların zemini az opak pembe, kenarı biraz daha belirgin
    PINK_SOFT = "rgba(255, 77, 148, 0.13)"
    PINK_LINE = "rgba(255, 77, 148, 0.45)"
    TURQ_SOFT = "rgba(43, 196, 174, 0.14)"
    ORANGE_SOFT = "rgba(255, 138, 91, 0.14)"
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
    PAGE_TITLE = 28     # sayfa başlığı (Bahnschrift SemiBold SemiCondensed)
    PAGE_SUB = 13       # başlığın altındaki tek satırlık açıklama
    SECTION = 16        # bölüm başlığı (Bahnschrift SemiBold), cümle düzeni
    BODY_SIZE = 13      # gövde
    SMALL = 12          # yardımcı metin
    BUTTON = 14         # düğme (Bahnschrift)
    STATUS = 22         # canlı banttaki durum ("Çalışıyor")
    BODY_PT = 10        # uygulama yazı tipi (pt) — Segoe UI Variable Text 10 pt ≈ 13 px


SPACE = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 32}
RADIUS = {"s": 5, "m": 7, "l": 12}   # girişler/düğmeler 5–7, karolar ve açılır yüzeyler 12
ICON = {"s": 16, "m": 20, "l": 24}
MOTION = {"fast": 120, "base": 180}  # ms; yalnızca kullanıcı eylemine yanıt olarak

# Gün batımı geçişi (Vice City): YALNIZCA canlı bandın alt çizgisinde.
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
