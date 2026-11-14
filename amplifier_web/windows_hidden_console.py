"""Give a windowless Windows host one console that has no window.

pythonw.exe starts without a console. Windows then creates a new, visible
console for every console program that such a process starts with default
creation flags. A no-window flag at each known call site cannot cover code
that this application does not own.

ensure_hidden_console() attaches the calling process to a console that has no
window. A console program started with default flags inherits that console, so
Windows creates no window for it or for the console programs it starts.

This is a default, not a sandbox:
- A program that requests CREATE_NEW_CONSOLE still gets a window.
- A GUI child and a DETACHED_PROCESS child have no console. Their own console
  children get a new window unless that child prevents it.
- os.startfile/ShellExecute start a console program in a new console.
Known call sites keep their explicit no-window flag.

Measured facts that this file depends on (native Windows 11, CPython 3.13):
- A console attached after process start replaces the process's console
  control handlers. Python's signal settings then give no protection: one
  Ctrl+C event from a child on the shared console ends the process with
  0xC000013A. The explicit handler below is therefore required.
- A plain attach in a process without standard handles makes them console
  handles. A child then waits forever for console input that nobody can type.
  The standard handles are therefore restored to their earlier values.

Standard library only. This file must stay importable by path, outside the
package, so that a native check can load the exact shipped mechanism.
"""
from pathlib import Path
import subprocess
import sys
import threading

_STANDARD_HANDLES = (0xFFFFFFF6, 0xFFFFFFF5, 0xFFFFFFF4)  # input, output, error
_HELPER = "import sys;sys.stdout.write('1');sys.stdout.flush();sys.stdin.read()"
_control_handler = None  # Windows keeps only the address; keep the object alive.


def ensure_hidden_console(*, timeout=10.0):
    """Attach a console without a window. Report the result; never stop startup.

    status: "attached" (new console without a window), "existing" (the process
    already had a console; nothing changed), "not-applicable" (not Windows), or
    "unavailable" (explicit no-window flags remain the only cover).
    """
    if sys.platform != "win32":
        return {"status": "not-applicable"}
    try:
        return _attach(timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"status": "unavailable", "errorType": type(error).__name__,
                "windowsError": getattr(error, "winerror", None)}


def _attach(timeout):
    import ctypes as c

    global _control_handler
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleCP.argtypes = []
    kernel.GetConsoleCP.restype = c.c_uint32
    if kernel.GetConsoleCP():
        return {"status": "existing"}
    # The console sibling of the host's own base interpreter. A venv
    # Scripts/python.exe is a redirector; its child would own the console.
    python = Path(getattr(sys, "_base_executable", None) or sys.executable).with_name("python.exe")
    if not python.is_file():
        raise FileNotFoundError("The console interpreter beside the host interpreter is missing.")
    handler_type = c.WINFUNCTYPE(c.c_int32, c.c_uint32)
    kernel.GetStdHandle.argtypes = [c.c_uint32]
    kernel.GetStdHandle.restype = c.c_void_p
    kernel.SetStdHandle.argtypes = [c.c_uint32, c.c_void_p]
    kernel.SetStdHandle.restype = c.c_int32
    kernel.AttachConsole.argtypes = [c.c_uint32]
    kernel.AttachConsole.restype = c.c_int32
    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, c.c_int32]
    kernel.SetConsoleCtrlHandler.restype = c.c_int32
    saved = [(name, kernel.GetStdHandle(name)) for name in _STANDARD_HANDLES]
    # CREATE_NO_WINDOW gives a console program a console without a window.
    # This process then joins that console; it lives while any process uses it.
    helper = subprocess.Popen(
        [str(python), "-I", "-S", "-c", _HELPER], executable=str(python), cwd=str(python.parent),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    timer = threading.Timer(timeout, helper.kill)
    timer.daemon = True
    timer.start()
    try:
        # The byte proves that the helper runs, and so has joined its console.
        if helper.stdout.read(1) != b"1":
            raise OSError("The console helper did not start.")
        if not kernel.AttachConsole(helper.pid):
            raise c.WinError(c.get_last_error())
    finally:
        timer.cancel()
        helper.stdin.close()
        helper.stdout.close()
        try:
            helper.wait(timeout)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.wait()
    # Keep this process's own standard handles exactly as they were. Only the
    # console membership changes; pipes and redirection behave as before.
    for name, handle in saved:
        kernel.SetStdHandle(name, handle)

    # Children now share this console. Their Ctrl+C or Ctrl+Break event reaches
    # every process on it. This host stops only through its own stop request,
    # so it handles both events and does nothing. Close, sign-out and shutdown
    # events keep their default result. Children keep their own handling.
    @handler_type
    def keep_running(event):
        return 1 if event in (0, 1) else 0

    if not kernel.SetConsoleCtrlHandler(keep_running, 1):
        # A shared console without this handler is worse than no console.
        error = c.WinError(c.get_last_error())
        kernel.FreeConsole()
        for name, handle in saved:
            kernel.SetStdHandle(name, handle)
        raise error
    _control_handler = keep_running
    return {"status": "attached"}
