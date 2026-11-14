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
Known call sites keep their explicit no-window flag. That flag is also the
isolation boundary: such a child gets a console of its own. Children on the
shared console share its control events, code page and input.

Measured facts that this file depends on (native Windows 11, CPython 3.13):
- A console attached after process start replaces the process's console
  control handlers. Python's signal settings then give no protection: one
  Ctrl+C event from a child on the shared console ends the process with
  0xC000013A. The explicit handler below is therefore required.
- A plain attach in a process without standard handles makes them console
  handles. A child then waits forever for console input that nobody can type.
  The standard handles are therefore restored to their earlier values.
Both steps follow the attach at once, before the helper is released. If either
fails, the console is released again: a shared console without them is worse
than no console. The release is confirmed by state, not by a return value. A
process that stays attached to a console it could neither protect nor release
must not continue: one Ctrl+C from a child would end it later, without notice.
Nothing else in this process may call AttachConsole, AllocConsole or
FreeConsole afterwards: each one resets the handler table and so removes the
protection without notice. Windows calls a handler that is registered later
before this one. The handler stays registered until the process ends; do not
remove it at exit, because that would restore the default result.

The app host always has the launch guard's pipes as standard handles, and a
child with default standard handles inherits them. A caller with NO standard
handles keeps none, and so does such a child: cmd.exe then returned 1 for
"exit 3" (measured). Check that case before adding another caller.

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


class HiddenConsoleError(RuntimeError):
    """The process is attached to a console that it could not protect or release."""


# The status command shows this sentence to the owner.
_UNSAFE = ("Windows left this app attached to a helper console that the app could not make "
           "safe or release. Start Amplifier again.")


def ensure_hidden_console(*, timeout=5.0):
    """Attach a console without a window, and report the result.

    status: "attached" (new console without a window), "existing" (the process
    already had a console; nothing changed), "not-applicable" (not Windows), or
    "unavailable" (explicit no-window flags remain the only cover). When a
    completed attach had to be undone, "consoleReleased" and
    "standardHandlesRestored" report the confirmed state.

    A failure does not stop startup, with one exception: HiddenConsoleError,
    when the process stays attached to a console without its protection.
    """
    if sys.platform != "win32":
        return {"status": "not-applicable"}
    rollback = {}
    try:
        return _attach(timeout, rollback)
    except HiddenConsoleError:
        raise
    except Exception as error:  # An improvement, not a start condition.
        if rollback.get("consoleReleased") is False:
            # No later error, for example from helper cleanup, may hide this state.
            raise HiddenConsoleError(_UNSAFE) from error
        return {"status": "unavailable", "errorType": type(error).__name__,
                "windowsError": getattr(error, "winerror", None), **rollback}


def _attach(timeout, rollback):
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
    kernel.FreeConsole.argtypes = []
    kernel.FreeConsole.restype = c.c_int32
    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, c.c_int32]
    kernel.SetConsoleCtrlHandler.restype = c.c_int32

    def check(result):
        if not result:
            raise c.WinError(c.get_last_error())

    # Children share this console. Their Ctrl+C or Ctrl+Break event reaches
    # every process on it. This host stops only through its own stop request,
    # so it handles both events and does nothing. Close, sign-out and shutdown
    # events keep their default result. Children keep their own handling.
    @handler_type
    def keep_running(event):
        return 1 if event in (0, 1) else 0

    saved = [(name, kernel.GetStdHandle(name)) for name in _STANDARD_HANDLES]
    # CREATE_NO_WINDOW gives a console program a console without a window.
    # This process then joins that console; it lives while any process uses it.
    helper = subprocess.Popen(
        [str(python), "-I", "-S", "-c", _HELPER], executable=str(python), cwd=str(python.parent),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    attached = False
    try:
        timer = threading.Timer(timeout, helper.kill)
        timer.daemon = True
        timer.start()
        try:
            # The byte proves that the helper runs, and so has joined its console.
            if helper.stdout.read(1) != b"1":
                raise OSError("The console helper did not start.")
            check(kernel.AttachConsole(helper.pid))
            attached = True
            # Keep this process's own standard handles exactly as they were.
            # Only the console membership changes; pipes behave as before.
            for name, handle in saved:
                check(kernel.SetStdHandle(name, handle))
            check(kernel.SetConsoleCtrlHandler(keep_running, 1))
            _control_handler = keep_running
        finally:
            timer.cancel()
    except BaseException as error:
        # Entry proved that there was no console, so any console now is ours,
        # even if an error arrived before the flag was set.
        if attached or kernel.GetConsoleCP():
            rollback["consoleReleased"] = False  # Until the state check below confirms it.
            kernel.FreeConsole()
            for name, handle in saved:
                kernel.SetStdHandle(name, handle)
            # Confirm by state. FreeConsole resets the handler table.
            rollback["consoleReleased"] = not kernel.GetConsoleCP()
            rollback["standardHandlesRestored"] = all(
                kernel.GetStdHandle(name) == handle for name, handle in saved)
            if not rollback["consoleReleased"]:
                raise HiddenConsoleError(_UNSAFE) from error
            _control_handler = None
        raise
    finally:
        _release(helper, timeout)
    return {"status": "attached"}


def _release(helper, timeout):
    """End the helper. The console stays while this process uses it.

    Nothing here may change the attach result: the console is already either
    protected or released.
    """
    for stream in (helper.stdin, helper.stdout):
        try:
            stream.close()
        except Exception:
            pass
    try:
        try:
            helper.wait(timeout)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.wait(timeout)
    except Exception:
        pass  # It is a child in the host's Job and ends with it.
