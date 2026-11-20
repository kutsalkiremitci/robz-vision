"""Geliştiricinin kanal bağlantıları: Kick'te takip et ve YouTube.

Robz Vision'ı Kick yayıncısı Robz so good geliştiriyor. Bağlantı yalnız uygulamanın kendi penceresinde durur: kenar
çubuğunun altında küçük bir satır (main_window) ve Ayarlar → Uygulama'daki "Geliştirici" satırı (settings_page).
Yerinde ve sessizdir: açılır pencere, bildirim, zorla gösterim yok; tıklanınca varsayılan tarayıcıda kanal açılır.

Yayın çıktısına (OBS tarayıcı kaynağı, web/overlay.html) ve ekrandaki altyazı kutusuna (overlay.py) hiçbir şey
girmez: Robz Vision'ı kullanan başka yayıncıların yayınına reklam düşmesin (tests/test_developer_link.py denetler).

Kick logosu (`data/kick.svg`, Kick'in yeşil "K" işareti, #53FC18) Kick'in marka yönergelerine uygun olarak yalnız
Kick kanalına bağlantı vermek için, değiştirilmeden (oran, renk) kullanılır. Kick adı ve logosu Kick'e aittir; Robz
Vision Kick'le bağlantılı bir ürün değildir. Kick yeşili arayüzde yalnız bu logoda görünür.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from altyazitr import APP_NAME
from altyazitr.paths import resource_dir

log = logging.getLogger(__name__)

DEVELOPER = "Robz so good"
KICK_URL = "https://kick.com/robzsogood"
YOUTUBE_URL = "https://www.youtube.com/@robzsogood"
KICK_GREEN = "#53FC18"  # Kick'in marka yeşili: yalnız logoda (data/kick.svg), arayüzün başka yerinde kullanılmaz

FOLLOW_TEXT = f"{DEVELOPER}'u Kick'te takip et"                 # ekran okuyucu adı
FOLLOW_TIP = f"{APP_NAME}'ı geliştiren {DEVELOPER}'u Kick'te takip et"
KICK_DESC = "kick.com/robzsogood tarayıcıda açılır"
YOUTUBE_TIP = f"{DEVELOPER}'un YouTube kanalı"

_PIXMAPS: dict[tuple[int, float, float], QPixmap] = {}
ICON_SCALE = 0.875  # düğmede ve kenar çubuğunda: 16 px kutuda 14 px "K"


def kick_logo_path() -> Path:
    """Paketteki Kick logosu (PyInstaller paketinde de `altyazitr/data/` altında)."""
    return resource_dir() / "data" / "kick.svg"


def youtube_logo_path() -> Path:
    """Paketteki YouTube işareti (kırmızı oynat düğmesi; yalnız geliştiricinin kanalına giden bağlantıda)."""
    return resource_dir() / "data" / "youtube.svg"


def kick_pixmap(size: int, dpr: float = 2.0, scale: float = 1.0) -> QPixmap:
    """Kick logosu: `size` × `size` px kutuda, oranı korunarak ortalanmış; ekranın piksel yoğunluğunda keskin.
    `scale` < 1 logoyu kutunun içinde küçültür (dolu "K", çizgi ikonların yanında aynı boyda görünsün diye).
    Dosya okunamazsa boş QPixmap döner (bağlantı yazısıyla yine çalışır)."""
    return _logo_pixmap(kick_logo_path(), size, dpr, scale)


def youtube_pixmap(size: int, dpr: float = 2.0, scale: float = 1.0) -> QPixmap:
    """YouTube işareti, kick_pixmap ile aynı kuralla."""
    return _logo_pixmap(youtube_logo_path(), size, dpr, scale)


def _logo_pixmap(path: Path, size: int, dpr: float = 2.0, scale: float = 1.0) -> QPixmap:
    size, dpr, scale = max(1, int(size)), max(1.0, float(dpr)), min(1.0, max(0.1, float(scale)))
    key = (path.name, size, dpr, scale)
    pm = _PIXMAPS.get(key)
    if pm is not None:
        return pm
    pm = QPixmap()
    try:
        renderer = QSvgRenderer(str(path))
        if renderer.isValid():
            px = max(1, round(size * dpr))
            pm = QPixmap(px, px)
            pm.fill(Qt.GlobalColor.transparent)
            renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            inner = px * scale
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(p, QRectF((px - inner) / 2, (px - inner) / 2, inner, inner))
            p.end()
            pm.setDevicePixelRatio(dpr)
        else:
            log.warning("Logo okunamadı: %s", path)
    except Exception:
        log.debug("Logo çizilemedi: %s", path, exc_info=True)
        pm = QPixmap()
    _PIXMAPS[key] = pm
    return pm


def kick_icon() -> QIcon:
    """Düğmeler için Kick logosu (birkaç boyutta; Qt ekran yoğunluğuna uygun olanı seçer)."""
    return _logo_icon(kick_pixmap)


def youtube_icon() -> QIcon:
    """Düğmeler için YouTube işareti (Ayarlar → Geliştirici)."""
    return _logo_icon(youtube_pixmap)


def _logo_icon(make) -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32):
        pm = make(s, 1.0, ICON_SCALE)
        if not pm.isNull():
            ic.addPixmap(pm)
    return ic


def open_kick() -> bool:
    """Geliştiricinin Kick kanalını varsayılan tarayıcıda açar."""
    return bool(QDesktopServices.openUrl(QUrl(KICK_URL)))


def open_youtube() -> bool:
    """Geliştiricinin YouTube kanalını varsayılan tarayıcıda açar."""
    return bool(QDesktopServices.openUrl(QUrl(YOUTUBE_URL)))
