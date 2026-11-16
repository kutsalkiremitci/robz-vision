"""Global kısayollar: Win32 RegisterHotKey + Qt yerel olay filtresi.

Klavye kancası (hook) kullanmaz; yönetici izni gerektirmez, hile koruma yazılımlarınca
keylogger gibi algılanmaz. Oyun penceresi odaktayken de çalışır.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from altyazitr import APP_NAME

log = logging.getLogger(__name__)

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_u32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_VK: dict[str, int] = {
    **{chr(c): c for c in range(0x41, 0x5B)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
    "SPACE": 0x20, "INS": 0x2D, "INSERT": 0x2D, "DEL": 0x2E, "DELETE": 0x2E, "HOME": 0x24,
    "END": 0x23, "PGUP": 0x21, "PAGEUP": 0x21, "PGDOWN": 0x22, "PAGEDOWN": 0x22, "LEFT": 0x25,
    "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "PAUSE": 0x13, "SCROLLLOCK": 0x91, "TAB": 0x09,
    "BACKSPACE": 0x08, "RETURN": 0x0D, "ENTER": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, "`": 0xC0, "=": 0xBB, "+": 0xBB,
}
_MODS = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT,
         "META": MOD_WIN, "WIN": MOD_WIN}


def parse_hotkey(seq: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+T' → (modifiers, vk). Geçersizse None."""
    s = (seq or "").strip()
    if not s:
        return None
    if s.endswith("++"):
        parts, key = s[:-2].split("+"), "+"
    else:
        tokens = s.split("+")
        parts, key = tokens[:-1], tokens[-1]
    mods = 0
    for p in parts:
        m = _MODS.get(p.strip().upper())
        if m is None:
            return None
        mods |= m
    vk = _VK.get(key.strip().upper())
    if vk is None:
        return None
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: HotkeyManager):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt adı
        try:
            if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.hWnd == self.owner.hwnd:
                    name = self.owner.names.get(int(msg.wParam))
                    if name:
                        self.owner.triggered.emit(name)
                        return True, 0
        except Exception:
            log.debug("kısayol filtresi hatası", exc_info=True)
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self._sink = QWidget()  # gizli yerel pencere: WM_HOTKEY buraya gelir
        self._sink.setWindowTitle(f"{APP_NAME} kısayol alıcısı")
        self.hwnd = int(self._sink.winId())
        self.names: dict[int, str] = {}
        self._next_id = 1
        self._filter = _Filter(self)
        QApplication.instance().installNativeEventFilter(self._filter)

    def register(self, name: str, seq: str) -> str | None:
        """Kısayolu kaydeder. Hata varsa Türkçe hata metni döner, başarılıysa None."""
        parsed = parse_hotkey(seq)
        if parsed is None:
            return f"Geçersiz kısayol: {seq}"
        mods, vk = parsed
        hid = self._next_id
        self._next_id += 1
        if not _u32.RegisterHotKey(self.hwnd, hid, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            if err == 1409:
                return f"{seq} başka bir uygulama tarafından kullanılıyor"
            return f"{seq} kaydedilemedi (hata {err})"
        self.names[hid] = name
        log.info("Kısayol kaydedildi: %s = %s", name, seq)
        return None

    def unregister_all(self) -> None:
        for hid in list(self.names):
            _u32.UnregisterHotKey(self.hwnd, hid)
        self.names.clear()
