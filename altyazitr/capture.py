"""Görüntü yakalama.

* WindowCapturer: Windows Graphics Capture (windows-capture paketi). Belirli bir pencereyi
  üstü kapalı olsa bile yakalar; kendi overlay pencerelerimizi görmez.
* RegionCapturer: mss ile ekranın seçilen bölgesini yakalar (varsayılan yöntem).

Pencere modunda bölge, pencerenin *istemci alanına* göre oransal (0..1) tutulur; böylece pencere
boyutu değişse de bölge doğru kalır.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

import numpy as np

from altyazitr import winutil

log = logging.getLogger(__name__)

Region = tuple[float, float, float, float]


def _crop_box(region: Region, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    cw, ch = max(1, x1 - x0), max(1, y1 - y0)
    rx, ry, rw, rh = region
    a = x0 + int(round(rx * cw))
    b = y0 + int(round(ry * ch))
    c = x0 + int(round((rx + rw) * cw))
    d = y0 + int(round((ry + rh) * ch))
    a, c = max(x0, min(a, x1 - 1)), max(x0 + 1, min(c, x1))
    b, d = max(y0, min(b, y1 - 1)), max(y0 + 1, min(d, y1))
    return a, b, max(a + 1, c), max(b + 1, d)


class CaptureError(RuntimeError):
    pass


class WindowCapturer:
    """Windows Graphics Capture ile tek pencere yakalama."""

    def __init__(self, hwnd: int, region: Region, fps: int = 10):
        self.hwnd = int(hwnd)
        self.region: Region = tuple(region)  # type: ignore[assignment]
        self.fps = max(1, fps)
        self.closed = False
        self.frame_count = 0
        self._lock = threading.Lock()
        self._roi: np.ndarray | None = None
        self._full: np.ndarray | None = None
        self._want_full = False
        self._full_event = threading.Event()
        self._client_box: tuple[int, int, int, int] | None = None
        self._client_box_frame = -999
        self._frame_size = (0, 0)
        self._control = None

    # --- yaşam döngüsü -------------------------------------------------
    def start(self) -> None:
        from windows_capture import WindowsCapture

        if not winutil.is_window(self.hwnd):
            raise CaptureError("Hedef pencere bulunamadı.")
        # İstenen en düşük kare aralığı: kontrol hızının ~2 katı (gereksiz kopyadan kaçın)
        interval = max(16, int(1000 / (self.fps * 2)))
        try:
            cap = WindowsCapture(cursor_capture=False, draw_border=False,
                                 minimum_update_interval=interval, window_hwnd=self.hwnd)
        except Exception as e:  # bazı sistemlerde kenarlıksız yakalama desteklenmez
            log.warning("Kenarlıksız yakalama başlatılamadı (%s), varsayılan ayarla deneniyor", e)
            cap = WindowsCapture(cursor_capture=False, minimum_update_interval=interval, window_hwnd=self.hwnd)

        @cap.event
        def on_frame_arrived(frame, capture_control):  # noqa: ARG001 - ad kütüphane için önemli
            try:
                self._on_frame(frame.frame_buffer)
            except Exception:
                log.exception("Kare işlenemedi")

        @cap.event
        def on_closed():
            log.info("Yakalama oturumu kapandı (pencere kapanmış olabilir)")
            self.closed = True

        self._control = cap.start_free_threaded()
        log.info("Pencere yakalama başladı: hwnd=%s '%s'", self.hwnd, winutil.window_title(self.hwnd))

    def stop(self) -> None:
        ctl, self._control = self._control, None
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:
                log.debug("Yakalama durdurulurken hata", exc_info=True)

    # --- kare işleme (yakalama iş parçacığında çalışır) --------------
    def _compute_client_box(self, w: int, h: int) -> tuple[int, int, int, int]:
        bounds = winutil.window_bounds(self.hwnd)
        client = winutil.client_rect_screen(self.hwnd)
        if not bounds or not client:
            return 0, 0, w, h
        bw, bh = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if bw <= 0 or bh <= 0:
            return 0, 0, w, h
        sx, sy = w / bw, h / bh
        x0 = int(round((client[0] - bounds[0]) * sx))
        y0 = int(round((client[1] - bounds[1]) * sy))
        x1 = int(round((client[2] - bounds[0]) * sx))
        y1 = int(round((client[3] - bounds[1]) * sy))
        x0, y0 = max(0, min(x0, w - 1)), max(0, min(y0, h - 1))
        x1, y1 = max(x0 + 1, min(x1, w)), max(y0 + 1, min(y1, h))
        if (x1 - x0) < w * 0.3 or (y1 - y0) < h * 0.3:  # anlamsız sonuç: tüm kareyi kullan
            return 0, 0, w, h
        return x0, y0, x1, y1

    def _on_frame(self, buf: np.ndarray) -> None:
        h, w = buf.shape[:2]
        self.frame_count += 1
        if (w, h) != self._frame_size or self.frame_count - self._client_box_frame > 30:
            self._client_box = self._compute_client_box(w, h)
            self._client_box_frame = self.frame_count
            self._frame_size = (w, h)
        cx0, cy0, cx1, cy1 = self._client_box  # type: ignore[misc]
        a, b, c, d = _crop_box(self.region, cx0, cy0, cx1, cy1)
        roi = np.ascontiguousarray(buf[b:d, a:c, :3])
        full = None
        if self._want_full:
            full = np.ascontiguousarray(buf[cy0:cy1, cx0:cx1, :3])
        with self._lock:
            self._roi = roi
            if full is not None:
                self._full = full
                self._want_full = False
                self._full_event.set()

    # --- dış arayüz ----------------------------------------------------
    def set_region(self, region: Region) -> None:
        self.region = tuple(region)  # type: ignore[assignment]

    def grab_roi(self) -> np.ndarray | None:
        with self._lock:
            return self._roi

    def grab_full(self, timeout: float = 2.0) -> np.ndarray | None:
        """İstemci alanının tam görüntüsü (bölge seçimi için)."""
        self._full_event.clear()
        self._want_full = True
        if not self._full_event.wait(timeout):
            self._want_full = False
            return None
        with self._lock:
            return self._full

    @property
    def alive(self) -> bool:
        return not self.closed and winutil.is_window(self.hwnd)


class RegionCapturer:
    """mss ile ekranın sabit bir bölgesini yakalar (fiziksel piksel). Kendi ekran altyazımız
    WDA_EXCLUDEFROMCAPTURE ile işaretli olduğundan bu yakalamaya girmez (test edildi)."""

    def __init__(self, rect: Sequence[int]):
        self.rect = tuple(int(v) for v in rect)
        self._local = threading.local()

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = _new_mss()
            self._local.sct = sct
        return sct

    def start(self) -> None:
        log.info("Ekran bölgesi yakalanıyor: %s", self.rect)

    def stop(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._local.sct = None

    def set_rect(self, rect: Sequence[int]) -> None:
        self.rect = tuple(int(v) for v in rect)

    def grab_roi(self) -> np.ndarray | None:
        x, y, w, h = self.rect
        if w < 4 or h < 4:
            return None
        shot = self._sct().grab({"left": x, "top": y, "width": w, "height": h})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

    @property
    def alive(self) -> bool:
        return True


def _new_mss():
    import mss

    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


_preview_local = threading.local()


def grab_screen_rect(rect: Sequence[int]) -> np.ndarray | None:
    """Önizleme için tek seferlik bölge görüntüsü (çağıran iş parçacığına ait mss örneğiyle)."""
    if len(rect) != 4 or rect[2] < 4 or rect[3] < 4:
        return None
    sct = getattr(_preview_local, "sct", None)
    if sct is None:
        sct = _preview_local.sct = _new_mss()
    x, y, w, h = (int(v) for v in rect)
    try:
        return np.ascontiguousarray(np.asarray(sct.grab({"left": x, "top": y, "width": w, "height": h}))[:, :, :3])
    except Exception:
        log.debug("Önizleme görüntüsü alınamadı", exc_info=True)
        return None


def monitor_fraction(rect: Sequence[int]) -> tuple[float, float, float, float] | None:
    """Bölgenin, içinde bulunduğu monitöre göre oransal konumu (OBS maskesi için)."""
    if len(rect) != 4:
        return None
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    with _new_mss() as sct:
        for m in sct.monitors[1:]:
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                return ((x - m["left"]) / m["width"], (y - m["top"]) / m["height"], w / m["width"], h / m["height"])
    return None


def snapshot_window(hwnd: int, timeout: float = 3.0) -> np.ndarray | None:
    """Bölge seçimi için pencerenin istemci alanının tek karesini alır."""
    cap = WindowCapturer(hwnd, (0, 0, 1, 1), fps=30)
    cap.start()
    try:
        return cap.grab_full(timeout)
    finally:
        cap.stop()
