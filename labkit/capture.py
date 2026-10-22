"""OS-level window capture via PrintWindow(PW_RENDERFULLCONTENT): works while the window is
covered by others (DWM renders it), and before the in-game harness is up. Prefer the in-game
screenshot (Lab.screenshot) when the harness is alive; this is the fallback."""

import ctypes
from ctypes import wintypes

from . import win32

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = win32.user32

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def capture_window(hwnd):
    """Returns (width, height, buffer) of the window's client area; buffer is top-down BGRX."""
    width, height = win32.client_size(hwnd)
    window_dc = user32.GetDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise ctypes.WinError(ctypes.get_last_error())
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        return width, height, buffer.raw
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, window_dc)
