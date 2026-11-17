"""What a person sees while the Start-menu entry starts Amplifier, and one start at a time.

The Start-menu entry's program has no window. A start takes seconds, and a
first start can take minutes. Until the browser opened, the person saw
nothing, and a second click started Amplifier a second time: two browser tabs.

`sign()` shows one small window for the time of a start. It says that
Amplifier is starting. It needs no click, takes no keyboard focus and has no
button. It stays in front of other windows, in the corner above the clock.
While this program shows a message of its own, the sign is hidden: the person
reads one thing at a time. It goes away when the start ends, also when the
start ends with a failure.

`only(root)` lets one start at a time go on, for one installation in one
sign-in session. A second start finds the first one, does nothing and shows
nothing: the first one's sign is on the screen.

Standard library only. No console and no new dependency: the window is of a
class that Windows provides, so this file registers no window class and has
no window procedure.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
import threading

STARTING = "Amplifier is starting."
# While the launcher's step `prepare` runs. The failure text of that step says
# the same about the download (windows_consumer.start_failure).
PREPARING = (STARTING + "\n\nIt downloads the remaining parts of the app from the internet. "
             "This can take a few minutes.")
LOCK = "Local\\AmplifierUnified-start-"  # This sign-in session; the installation's lock is for all of them.
_WIDTH, _MARGIN, _POINTS = 340, 14, 10  # At 96 dots per inch.


class Running(RuntimeError):
    """Another start of this installation runs in this sign-in session."""


@contextmanager
def only(root):
    """One start at a time for this installation in this sign-in session.

    Raises `Running`, with nothing changed, when another start holds the
    lock. The lock is a named mutex of this person (windows_uninstall.single
    has its rules). A program that ended without releasing it leaves it to
    the next start. Enter and leave this block on one thread.

    A lock that Windows does not give is not a reason to keep the person
    from Amplifier: the start goes on without it. The only loss is a second
    browser tab. Elsewhere than on Windows there is no such start and no lock.
    """
    from . import windows_uninstall
    with ExitStack() as held:
        if os.name == "nt":
            try:
                held.enter_context(windows_uninstall.named(LOCK, root, wait=0))
            except windows_uninstall.Busy:
                raise Running() from None
            except OSError:
                pass
        yield


class Sign:
    """The window of one start. `step(name)` follows the start's own steps."""

    def __init__(self):
        self.window = self.label = None  # Window handles, for a native check.
        self._text, self._shown = STARTING, None
        self._thread = self._thread_id = None
        self._ready, self._closing = threading.Event(), threading.Event()

    def step(self, name):
        """The start names the step that is about to run (windows_installation.prepare_background)."""
        self._text = PREPARING if name == "prepare" else STARTING

    @property
    def text(self):
        return self._text

    def open(self, *, wait=2.0):
        if os.name != "nt":
            return
        self._thread = threading.Thread(target=self._run, name="amplifier-start-sign", daemon=True)
        self._thread.start()
        self._ready.wait(wait)

    def close(self, *, wait=2.0):
        self._closing.set()
        thread, self._thread = self._thread, None
        if thread is None:
            return
        try:
            if self._thread_id is not None:
                import ctypes
                from ctypes import wintypes
                post = ctypes.WinDLL("user32").PostThreadMessageW
                post.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                post(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        except (OSError, AttributeError, TypeError):
            pass  # The window's own timer sees `_closing`.
        thread.join(wait)

    def _run(self):
        # One thread makes the window, answers its messages and removes it.
        try:
            self._window()
        except (OSError, AttributeError, ValueError, TypeError):
            pass  # No sign. A start never depends on it.
        finally:
            self.window = self.label = None
            self._ready.set()

    def _window(self):
        import ctypes as C
        from ctypes import wintypes as W
        user = C.WinDLL("user32", use_last_error=True)
        gdi = C.WinDLL("gdi32", use_last_error=True)
        handle = C.c_void_p
        user.CreateWindowExW.restype = handle
        user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                                         handle, handle, handle, handle]
        user.DestroyWindow.argtypes = [handle]
        user.ShowWindow.argtypes = [handle, C.c_int]
        user.SetWindowPos.argtypes = [handle, handle, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        user.SetWindowTextW.argtypes = [handle, W.LPCWSTR]
        user.SendMessageW.argtypes = [handle, W.UINT, handle, handle]
        user.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, handle, W.UINT]
        user.GetDC.restype = handle
        user.GetDC.argtypes = [handle]
        user.ReleaseDC.argtypes = [handle, handle]
        user.DrawTextW.argtypes = [handle, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT]
        user.SetTimer.restype = handle
        user.SetTimer.argtypes = [handle, handle, W.UINT, handle]
        user.KillTimer.argtypes = [handle, handle]
        user.GetMessageW.argtypes = [C.POINTER(W.MSG), handle, W.UINT, W.UINT]
        user.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
        user.FindWindowExW.restype = handle
        user.FindWindowExW.argtypes = [handle, handle, W.LPCWSTR, W.LPCWSTR]
        user.GetWindowThreadProcessId.argtypes = [handle, C.POINTER(W.DWORD)]
        user.IsWindowVisible.argtypes = [handle]
        gdi.CreateFontW.restype = handle
        gdi.CreateFontW.argtypes = [C.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]
        gdi.SelectObject.restype = handle
        gdi.SelectObject.argtypes = [handle, handle]
        gdi.DeleteObject.argtypes = [handle]

        def checked(value):
            if not value:
                raise C.WinError(C.get_last_error())
            return value

        try:
            dots = user.GetDpiForSystem() or 96  # Windows 10 and later.
        except AttributeError:
            dots = 96
        width, margin = _WIDTH * dots // 96, _MARGIN * dots // 96
        font = gdi.CreateFontW(-(_POINTS * dots // 72), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        # A popup with a border, in front of other windows, with no taskbar
        # button, and never the active window (also not after a click on it).
        window = checked(user.CreateWindowExW(0x00000008 | 0x00000080 | 0x08000000, "STATIC", "",
                                              0x80000000 | 0x00800000, 0, 0, width, 10, None, None, None, None))
        timer = None
        try:
            # The text, in a second window inside the first: that gives it a margin. No `&` shortcut.
            label = checked(user.CreateWindowExW(0, "STATIC", "", 0x40000000 | 0x10000000 | 0x00000080,
                                                 margin, margin, width - 2 * margin, 10, window, None, None, None))
            if font:
                user.SendMessageW(label, 0x0030, font, 1)  # WM_SETFONT
            self._thread_id = threading.get_native_id()
            self.window, self.label = window, label
            written = None

            def own_message():
                # A message box or a question of this program (class #32770).
                found, process = None, W.DWORD()
                for _ in range(1000):  # Windows on this desktop; never an endless walk.
                    found = user.FindWindowExW(None, found, "#32770", None)
                    if not found:
                        break
                    user.GetWindowThreadProcessId(found, C.byref(process))
                    if process.value == os.getpid() and user.IsWindowVisible(found):
                        return True
                return False

            def place(text):
                # As high as the text needs, in the corner above the clock.
                area, size = W.RECT(), W.RECT(0, 0, width - 2 * margin, 0)
                device = user.GetDC(label)
                if device:
                    previous = gdi.SelectObject(device, font) if font else None
                    user.DrawTextW(device, text, -1, C.byref(size), 0x0400 | 0x0010 | 0x0800)  # Measure, wrapped.
                    if previous:
                        gdi.SelectObject(device, previous)
                    user.ReleaseDC(label, device)
                height = max(size.bottom, margin) + 2 * margin
                if not user.SystemParametersInfoW(0x0030, 0, C.byref(area), 0):  # The screen without the taskbar.
                    area = W.RECT(0, 0, width + 2 * margin, height + 2 * margin)
                user.SetWindowTextW(label, text)
                user.SetWindowPos(label, None, margin, margin, width - 2 * margin, height - 2 * margin, 0x0014)
                user.SetWindowPos(window, C.c_void_p(-1), area.right - width - margin,
                                  area.bottom - height - margin, width, height, 0x0010)  # Topmost, not active.

            def refresh():
                nonlocal written
                text, show = self._text, not own_message()
                if text != written:
                    place(text)
                    written = text
                if show != self._shown:
                    user.ShowWindow(window, 4 if show else 0)  # Shown without activation, or hidden.
                    self._shown = show

            refresh()
            timer = user.SetTimer(None, None, 200, None)
            self._ready.set()
            message = W.MSG()
            while not self._closing.is_set() and user.GetMessageW(C.byref(message), None, 0, 0) > 0:
                if message.message == 0x0113 and not message.hWnd:  # WM_TIMER of this thread.
                    refresh()
                else:
                    user.DispatchMessageW(C.byref(message))
        finally:
            if timer:
                user.KillTimer(None, timer)
            user.DestroyWindow(window)
            if font:
                gdi.DeleteObject(font)


@contextmanager
def sign():
    """Show the sign for the time of this block. Never raises for the sign itself."""
    shown = Sign()
    try:
        shown.open()
    except (OSError, RuntimeError):
        pass  # No thread: no sign.
    try:
        yield shown
    finally:
        shown.close()
