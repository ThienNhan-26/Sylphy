"""Small Windows desktop avatar surface and its Runtime-facing state controller."""

from dataclasses import dataclass
from enum import Enum
import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Protocol

logger = logging.getLogger(__name__)


class PresentationState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    CURIOUS = "CURIOUS"
    OBSERVING = "OBSERVING"
    UNAVAILABLE = "UNAVAILABLE"


def normalize_presentation_state(state: PresentationState | str) -> PresentationState | None:
    """Accept only the bounded activity states understood by the avatar surface."""
    try:
        selected = state if isinstance(state, PresentationState) else PresentationState(state)
    except (TypeError, ValueError):
        return None
    return None if selected is PresentationState.UNAVAILABLE else selected


@dataclass(frozen=True)
class AvatarConfig:
    enabled: bool = False
    asset_path: Path = Path("assets/avatar/sylphy.png")
    allow_external_asset: bool = False
    width: int = 240
    height: int = 320
    always_on_top: bool = False
    start_visible: bool = True
    project_root: Path = Path(".")
    config_error: str | None = None


@dataclass(frozen=True)
class AvatarStatus:
    enabled: bool
    running: bool
    available: bool
    visible: bool
    state: PresentationState
    always_on_top: bool
    error: str | None = None


@dataclass(frozen=True)
class SurfaceStatus:
    running: bool
    available: bool
    visible: bool
    always_on_top: bool
    error: str | None = None


class AvatarSurface(Protocol):
    def start(self, config: AvatarConfig) -> None: ...
    def show(self) -> bool: ...
    def hide(self) -> bool: ...
    def set_state(self, state: PresentationState) -> bool: ...
    def set_always_on_top(self, enabled: bool) -> bool: ...
    def stop(self) -> None: ...
    def status(self) -> SurfaceStatus: ...


class AvatarController:
    """Owns presentation state and isolates all surface failures from Runtime."""

    def __init__(self, config: AvatarConfig, surface: AvatarSurface | None = None):
        self.config = config
        self._surface = surface or WindowsAvatarSurface()
        self._lock = Lock()
        self._started = False
        self._available = False
        self._state = PresentationState.IDLE
        self._error = config.config_error

    def start(self) -> None:
        with self._lock:
            if not self.config.enabled or self._started:
                return
            try:
                self._surface.start(self.config)
                status = self._surface.status()
                self._started = status.running
                self._available = status.available
                self._error = status.error or self.config.config_error
                if self._error:
                    logger.warning("Avatar status: %s", self._error)
                if self._available:
                    self._surface.set_state(self._state)
            except Exception as exc:
                self._started = False
                self._available = False
                self._state = PresentationState.UNAVAILABLE
                self._error = f"Avatar surface failed ({type(exc).__name__})."
                logger.exception("Avatar could not start; continuing without the desktop surface")
                try:
                    self._surface.stop()
                except Exception:
                    logger.exception("Could not clean up a failed avatar surface")

    def show(self) -> bool:
        return self._surface_call("show")

    def hide(self) -> bool:
        return self._surface_call("hide")

    def set_state(self, state: PresentationState | str, reason: str | None = None) -> bool:
        del reason  # Reasons are intentionally not persisted or shown in the avatar window.
        selected = normalize_presentation_state(state)
        if selected is None:
            logger.warning("Ignoring unsupported avatar presentation state")
            return False
        with self._lock:
            if not self.config.enabled or not self._available or not self._started:
                self._state = selected
                return False
            try:
                if not self._surface.set_state(selected):
                    return False
                self._state = selected
                return True
            except Exception as exc:
                self._mark_unavailable(exc)
                return False

    def set_always_on_top(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            return False
        return self._surface_call("set_always_on_top", enabled)

    def stop(self) -> None:
        with self._lock:
            try:
                self._surface.stop()
            except Exception as exc:
                self._mark_unavailable(exc)
            self._started = False
            self._available = False
            self._state = PresentationState.UNAVAILABLE

    def status(self) -> AvatarStatus:
        with self._lock:
            try:
                surface = self._surface.status()
                error = surface.error or self._error
                available = self._available and surface.available and surface.running
                return AvatarStatus(
                    self.config.enabled, self._started and surface.running,
                    available, surface.visible,
                    self._state if available else PresentationState.UNAVAILABLE,
                    surface.always_on_top, error,
                )
            except Exception as exc:
                return AvatarStatus(self.config.enabled, False, False, False,
                                    PresentationState.UNAVAILABLE, False,
                                    f"Avatar status unavailable ({type(exc).__name__}).")

    def _surface_call(self, method: str, *args) -> bool:
        with self._lock:
            if not self.config.enabled or not self._started or not self._available:
                return False
            try:
                return bool(getattr(self._surface, method)(*args))
            except Exception as exc:
                self._mark_unavailable(exc)
                return False

    def _mark_unavailable(self, exc: Exception) -> None:
        self._started = False
        self._available = False
        self._state = PresentationState.UNAVAILABLE
        self._error = f"Avatar surface failed ({type(exc).__name__})."
        logger.exception("Avatar surface operation failed; Runtime remains available")
        try:
            self._surface.stop()
        except Exception:
            logger.exception("Could not clean up an unavailable avatar surface")


class WindowsAvatarSurface:
    """A small native Win32 window hosted on its own message-loop thread."""

    _WM_APP_SHOW = 0x8001
    _WM_APP_HIDE = 0x8002
    _WM_APP_STATE = 0x8003
    _WM_APP_TOPMOST = 0x8004
    _WM_QUIT = 0x0012
    _SW_HIDE = 0
    _SW_SHOWNOACTIVATE = 4
    _SW_MINIMIZE = 6
    _WS_OVERLAPPED = 0x00000000
    _WS_CAPTION = 0x00C00000
    _WS_SYSMENU = 0x00080000
    _WS_MINIMIZEBOX = 0x00020000
    _WS_EX_NOACTIVATE = 0x08000000
    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _WM_CLOSE = 0x0010
    _WM_DESTROY = 0x0002
    _WM_PAINT = 0x000F
    _WM_ERASEBKGND = 0x0014
    _WM_SIZE = 0x0005
    _SIZE_MINIMIZED = 1
    _BI_RGB = 0
    _DIB_RGB_COLORS = 0
    _SRCCOPY = 0x00CC0020
    _CW_USEDEFAULT = -2147483648

    def __init__(self):
        self._lock = Lock()
        self._ready = Event()
        self._thread: Thread | None = None
        self._thread_id = 0
        self._hwnd = None
        self._bitmap = None
        self._config: AvatarConfig | None = None
        self._running = False
        self._available = False
        self._visible = False
        self._always_on_top = False
        self._error: str | None = None
        self._wndproc_ref = None
        self._class_name: str | None = None
        self._apis = None

    def start(self, config: AvatarConfig) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if os.name != "nt":
                raise OSError("The native avatar window is available only on Windows")
            self._config = config
            self._ready.clear()
            self._error = None
            self._thread = Thread(target=self._window_main, name="sylphy-avatar-window", daemon=True)
            self._thread.start()
        if not self._ready.wait(5.0):
            self._error = "Avatar window did not initialize before the startup timeout."
            self.stop()
            raise TimeoutError(self._error)
        with self._lock:
            unavailable = not self._available
            error = self._error or "Avatar window is unavailable"
        if unavailable:
            self.stop()
            raise RuntimeError(error)

    def show(self) -> bool:
        return self._post(self._WM_APP_SHOW)

    def hide(self) -> bool:
        return self._post(self._WM_APP_HIDE)

    def set_state(self, state: PresentationState) -> bool:
        # v1 has one default image for every state; keep the typed state on the
        # controller without implying emotion or starting another rendering task.
        del state
        return self._post(self._WM_APP_STATE)

    def set_always_on_top(self, enabled: bool) -> bool:
        return self._post(self._WM_APP_TOPMOST, int(enabled))

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
            apis = self._apis
        if thread and thread.is_alive():
            if thread_id and apis:
                apis["user32"].PostThreadMessageW(thread_id, self._WM_QUIT, 0, 0)
            thread.join(timeout=2.0)
        with self._lock:
            if thread and thread.is_alive():
                self._error = "Avatar UI thread did not stop within two seconds."
                self._available = False
                self._running = False
            self._thread = None if not thread or not thread.is_alive() else thread

    def status(self) -> SurfaceStatus:
        with self._lock:
            running = self._running and bool(self._thread and self._thread.is_alive())
            return SurfaceStatus(running, self._available and running, self._visible and running,
                                 self._always_on_top, self._error)

    def _post(self, message: int, parameter: int = 0) -> bool:
        with self._lock:
            if not self._running or not self._thread_id or not self._apis:
                return False
            ok = self._apis["user32"].PostThreadMessageW(self._thread_id, message, parameter, 0)
            if not ok:
                self._error = "Could not queue a command for the avatar window."
                self._available = False
                self._running = False
            return bool(ok)

    def _window_main(self) -> None:
        try:
            if os.name != "nt":
                raise OSError("The native avatar window is available only on Windows")
            import ctypes
            from ctypes import wintypes
            from PIL import Image, ImageDraw, ImageFont, ImageOps

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._set_api_signatures(user32, gdi32, kernel32, ctypes, wintypes)
            with self._lock:
                self._apis = {"ctypes": ctypes, "wintypes": wintypes, "user32": user32,
                              "gdi32": gdi32, "kernel32": kernel32,
                              "Image": Image, "ImageDraw": ImageDraw,
                              "ImageFont": ImageFont, "ImageOps": ImageOps}
                self._thread_id = kernel32.GetCurrentThreadId()
            pixels, diagnostic = self._render_asset(Image, ImageDraw, ImageFont, ImageOps)
            self._create_bitmap(pixels, ctypes, wintypes, gdi32)
            self._register_and_create_window(ctypes, wintypes, user32, kernel32)
            with self._lock:
                self._running = True
                self._available = True
                self._visible = bool(self._config.start_visible)
                self._always_on_top = bool(self._config.always_on_top)
                self._error = diagnostic or self._config.config_error
            self._ready.set()
            self._message_loop(ctypes, wintypes, user32)
        except Exception as exc:
            with self._lock:
                self._running = False
                self._available = False
                self._visible = False
                self._error = f"Avatar window failed ({type(exc).__name__})."
                logger.exception("Avatar window initialization or message loop failed")
            self._ready.set()
        finally:
            self._cleanup_window()

    def _render_asset(self, Image, ImageDraw, ImageFont, ImageOps):
        config = self._config
        width, height = config.width, config.height
        background = (30, 33, 42, 255)
        canvas = Image.new("RGBA", (width, height), background)
        diagnostic = None
        asset = self._resolve_asset(config)
        if asset is not None:
            try:
                with Image.open(asset) as opened:
                    if opened.width > 8192 or opened.height > 8192 or opened.width * opened.height > 64_000_000:
                        raise ValueError("Avatar PNG exceeds the supported image dimensions")
                    image = opened.convert("RGBA")
                fitted = ImageOps.contain(image, (max(1, width - 24), max(1, height - 24)),
                                          method=Image.Resampling.LANCZOS)
                canvas.alpha_composite(fitted, ((width - fitted.width) // 2,
                                                (height - fitted.height) // 2))
            except Exception as exc:
                diagnostic = f"Avatar asset could not be loaded ({type(exc).__name__}); using development placeholder."
        else:
            diagnostic = "Avatar asset is missing or outside the approved asset location; using development placeholder."
        if asset is None or diagnostic:
            draw = ImageDraw.Draw(canvas)
            inset = max(12, width // 12)
            draw.rounded_rectangle((inset, inset, width - inset, height - inset),
                                   radius=18, fill=(48, 53, 66, 255), outline=(115, 126, 147, 255), width=2)
            label = "SYLPHY\nDEVELOPMENT\nPLACEHOLDER"
            font = ImageFont.load_default()
            bounds = draw.multiline_textbbox((0, 0), label, font=font, spacing=8, align="center")
            text_w, text_h = bounds[2] - bounds[0], bounds[3] - bounds[1]
            draw.multiline_text(((width - text_w) / 2, (height - text_h) / 2), label,
                                fill=(229, 232, 238, 255), font=font, spacing=8, align="center")
        return canvas.convert("RGB").tobytes("raw", "BGRX"), diagnostic

    @staticmethod
    def _resolve_asset(config: AvatarConfig) -> Path | None:
        selected = config.asset_path
        path = (selected if selected.is_absolute() else config.project_root / selected).resolve()
        if str(path).startswith("\\\\"):
            return None
        if not config.allow_external_asset:
            approved = (config.project_root / "assets" / "avatar").resolve()
            try:
                path.relative_to(approved)
            except ValueError:
                return None
        if not path.is_file() or path.suffix.casefold() != ".png":
            return None
        return path

    def _create_bitmap(self, pixels, ctypes, wintypes, gdi32) -> None:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        config = self._config
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = config.width
        info.bmiHeader.biHeight = -config.height  # Top-down DIB; output dimensions preserve image ratio.
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = self._BI_RGB
        bits = ctypes.c_void_p()
        gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                           wintypes.DWORD]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        bitmap = gdi32.CreateDIBSection(None, ctypes.byref(info), self._DIB_RGB_COLORS,
                                        ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise OSError("Could not create the avatar image surface")
        ctypes.memmove(bits, pixels, len(pixels))
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._bitmap = bitmap

    def _register_and_create_window(self, ctypes, wintypes, user32, kernel32) -> None:
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", POINT),
                        ("lPrivate", wintypes.DWORD)]

        class WNDCLASSW(ctypes.Structure):
            pass

        wndproc_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        WNDCLASSW._fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", wndproc_type),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", ctypes.c_void_p), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

        def window_proc(hwnd, message, wparam, lparam):
            try:
                if message == self._WM_CLOSE:
                    user32.ShowWindow(hwnd, self._SW_HIDE)
                    with self._lock:
                        self._visible = False
                    return 0
                if message == self._WM_PAINT:
                    self._paint(hwnd, ctypes, wintypes, user32, self._apis["gdi32"])
                    return 0
                if message == self._WM_ERASEBKGND:
                    return 1
                if message == self._WM_SIZE and wparam == self._SIZE_MINIMIZED:
                    with self._lock:
                        self._visible = False
                if message == self._WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                logger.exception("Avatar window message handling failed")
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        callback = wndproc_type(window_proc)
        self._wndproc_ref = callback
        class_name = f"SylphyAvatarWindowV1_{id(self):x}"
        self._class_name = class_name
        instance = kernel32.GetModuleHandleW(None)
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        definition = WNDCLASSW(0, callback, 0, 0, instance, None, None, None, None, class_name)
        if not user32.RegisterClassW(ctypes.byref(definition)) and ctypes.get_last_error() != 1410:
            raise ctypes.WinError(ctypes.get_last_error())
        style = self._WS_OVERLAPPED | self._WS_CAPTION | self._WS_SYSMENU | self._WS_MINIMIZEBOX
        ex_style = self._WS_EX_NOACTIVATE
        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD,
                                             wintypes.BOOL, wintypes.DWORD]
        bounds = RECT(0, 0, self._config.width, self._config.height)
        user32.AdjustWindowRectEx(ctypes.byref(bounds), style, False, ex_style)
        hwnd = user32.CreateWindowExW(ex_style, class_name, "SYLPHY — Development Avatar",
                                      style, self._CW_USEDEFAULT, self._CW_USEDEFAULT,
                                      bounds.right - bounds.left, bounds.bottom - bounds.top,
                                      None, None, instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd
        self._set_topmost(self._config.always_on_top, user32)
        if self._config.start_visible:
            user32.ShowWindow(hwnd, self._SW_SHOWNOACTIVATE)
            user32.UpdateWindow(hwnd)

    def _message_loop(self, ctypes, wintypes, user32) -> None:
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", POINT),
                        ("lPrivate", wintypes.DWORD)]

        message = MSG()
        user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        while True:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result < 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if result == 0:
                break
            if message.message == self._WM_APP_SHOW:
                user32.ShowWindow(self._hwnd, self._SW_SHOWNOACTIVATE)
                with self._lock:
                    self._visible = True
            elif message.message == self._WM_APP_HIDE:
                user32.ShowWindow(self._hwnd, self._SW_HIDE)
                with self._lock:
                    self._visible = False
            elif message.message == self._WM_APP_STATE:
                pass
            elif message.message == self._WM_APP_TOPMOST:
                self._set_topmost(bool(message.wParam), user32)
            else:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))

    def _set_topmost(self, enabled: bool, user32) -> None:
        if not self._hwnd:
            return
        target = self._HWND_TOPMOST if enabled else self._HWND_NOTOPMOST
        user32.SetWindowPos(self._hwnd, self._apis["ctypes"].c_void_p(target), 0, 0, 0, 0,
                            self._SWP_NOMOVE | self._SWP_NOSIZE | self._SWP_NOACTIVATE)
        with self._lock:
            self._always_on_top = enabled

    def _paint(self, hwnd, ctypes, wintypes, user32, gdi32) -> None:
        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", RECT),
                        ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL),
                        ("rgbReserved", wintypes.BYTE * 32)]

        paint = PAINTSTRUCT()
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
        memory_dc = gdi32.CreateCompatibleDC(hdc)
        old_bitmap = gdi32.SelectObject(memory_dc, self._bitmap)
        gdi32.BitBlt(hdc, 0, 0, self._config.width, self._config.height,
                     memory_dc, 0, 0, self._SRCCOPY)
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.EndPaint(hwnd, ctypes.byref(paint))

    def _cleanup_window(self) -> None:
        with self._lock:
            apis = self._apis
            hwnd = self._hwnd
            bitmap = self._bitmap
            class_name = self._class_name
            self._hwnd = None
            self._bitmap = None
            self._running = False
            self._available = False
            self._visible = False
            self._thread_id = 0
        if apis:
            if hwnd:
                apis["user32"].DestroyWindow(hwnd)
            if bitmap:
                apis["gdi32"].DeleteObject(bitmap)
            if class_name:
                apis["user32"].UnregisterClassW.argtypes = [
                    apis["wintypes"].LPCWSTR, apis["wintypes"].HINSTANCE
                ]
                apis["user32"].UnregisterClassW(class_name,
                                                apis["kernel32"].GetModuleHandleW(None))
        with self._lock:
            self._class_name = None
            self._wndproc_ref = None

    @staticmethod
    def _set_api_signatures(user32, gdi32, kernel32, ctypes, wintypes) -> None:
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.GetMessageW.restype = ctypes.c_int
        user32.CreateWindowExW.restype = wintypes.HWND
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
