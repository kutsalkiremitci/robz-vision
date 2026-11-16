"""Win32 yardımcıları (yalnızca ctypes, ek bağımlılık yok): pencere listesi,
istemci alanı koordinatları, tek örnek kilidi."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)

GW_OWNER = 4
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    pid: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return f"{self.title}  —  {self.exe}"


def _exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def _is_cloaked(hwnd: int) -> bool:
    val = wintypes.DWORD(0)
    res = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return res == 0 and val.value != 0


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Pencerenin görünen sınırları (gölgesiz), ekran koordinatlarında: (sol, üst, sağ, alt)."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    return r.left, r.top, r.right, r.bottom


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    """İstemci alanı (başlık çubuğu/kenarlık hariç) ekran koordinatlarında: (sol, üst, sağ, alt)."""
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    p = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(p)):
        return None
    return p.x, p.y, p.x + r.right, p.y + r.bottom


def is_window(hwnd: int | None) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def list_windows() -> list[WindowInfo]:
    """Görünür, sahipsiz, gizlenmemiş (cloaked olmayan), başlıklı üst düzey pencereler."""
    own_pid = os.getpid()
    out: list[WindowInfo] = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER) or _is_cloaked(hwnd):
            return True
        title = window_title(hwnd)
        if not title.strip():
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        b = window_bounds(hwnd)
        w, h = (b[2] - b[0], b[3] - b[1]) if b else (0, 0)
        if w < 120 or h < 80:
            return True
        out.append(WindowInfo(int(hwnd), title, _exe_name(pid.value), pid.value, w, h))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(exe: str = "", title: str = "") -> WindowInfo | None:
    """Kaydedilen pencereyi yeniden bulur.

    Sıra: exe + tam başlık → exe + benzer başlık (başlık oyunda değişebilir) → exe'nin *tek* penceresi →
    (exe yoksa) başlık içerir eşleşmesi. Aynı programın birden çok penceresi varsa ve hiçbiri benzemiyorsa
    yanlış pencereyi okumamak için None döner (pencere açılana kadar beklenir)."""
    from rapidfuzz import fuzz

    wins = list_windows()
    exe_l = exe.lower().strip()
    if exe_l:
        same_exe = [w for w in wins if w.exe.lower() == exe_l]
        if title:
            for w in same_exe:
                if w.title == title:
                    return w
            scored = [(fuzz.ratio(w.title.lower(), title.lower()), w) for w in same_exe]
            scored = [sw for sw in scored if sw[0] >= 60]
            if scored:
                return max(scored, key=lambda sw: sw[0])[1]
        if len(same_exe) == 1:
            return same_exe[0]
        return None
    if title:
        t = title.lower()
        cands = [w for w in wins if t in w.title.lower()]
        if cands:
            return max(cands, key=lambda w: w.width * w.height)
    return None


_mutex_handle = None


def acquire_single_instance(name: str) -> bool:
    """Uygulamanın ikinci kez açılmasını engeller. True: ilk örnek."""
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
