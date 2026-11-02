"""Passive lease checks; Win32 stubs are not native-platform qualification."""

import ctypes
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from amplifier_web import generation_leases


class GenerationLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def lease(self, pid=123):
        path = self.home / "updates/worker-leases/worker.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 1, "generation": "source", "pid": pid}))
        return path

    def windows(self, *, handle=0x100000001, wait=258, error=0):
        kernel32 = SimpleNamespace(
            OpenProcess=Mock(return_value=handle),
            WaitForSingleObject=Mock(return_value=wait),
            CloseHandle=Mock(return_value=1),
        )
        kill = Mock(side_effect=AssertionError("Windows liveness must not signal"))
        self.enterContext(
            patch.object(generation_leases, "os", SimpleNamespace(name="nt", kill=kill))
        )
        load = self.enterContext(
            patch.object(ctypes, "WinDLL", return_value=kernel32, create=True)
        )
        self.enterContext(
            patch.object(ctypes, "get_last_error", return_value=error, create=True)
        )
        return kernel32, load, kill

    def test_windows_live_or_reused_pid_is_retained_without_signaling(self):
        path = self.lease()
        kernel32, load, kill = self.windows()

        self.assertEqual(generation_leases.references(self.home), {"source"})
        self.assertTrue(path.exists())
        load.assert_called_once_with("kernel32", use_last_error=True)
        kernel32.OpenProcess.assert_called_once_with(0x00100000, False, 123)
        kernel32.WaitForSingleObject.assert_called_once_with(0x100000001, 0)
        kernel32.CloseHandle.assert_called_once_with(0x100000001)
        kill.assert_not_called()
        self.assertEqual(
            kernel32.OpenProcess.argtypes,
            [ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint32],
        )
        self.assertIs(kernel32.OpenProcess.restype, ctypes.c_void_p)
        self.assertEqual(
            kernel32.WaitForSingleObject.argtypes,
            [ctypes.c_void_p, ctypes.c_uint32],
        )
        self.assertIs(kernel32.WaitForSingleObject.restype, ctypes.c_uint32)
        self.assertEqual(kernel32.CloseHandle.argtypes, [ctypes.c_void_p])
        self.assertIs(kernel32.CloseHandle.restype, ctypes.c_int32)

    def test_windows_exited_process_reaps_lease_and_closes_handle(self):
        path = self.lease()
        kernel32, _, kill = self.windows(wait=0)

        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())
        kernel32.CloseHandle.assert_called_once_with(0x100000001)
        kill.assert_not_called()

    def test_windows_absent_process_reaps_lease_without_a_handle(self):
        path = self.lease()
        kernel32, _, kill = self.windows(handle=None, error=87)

        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())
        kernel32.WaitForSingleObject.assert_not_called()
        kernel32.CloseHandle.assert_not_called()
        kill.assert_not_called()

    def test_windows_open_errors_retain_lease(self):
        path = self.lease()
        kernel32, _, _ = self.windows(handle=None)
        for error in (5, 0, 6, 8):
            with self.subTest(error=error), patch.object(
                ctypes, "get_last_error", return_value=error
            ):
                self.assertEqual(generation_leases.references(self.home), {"source"})
                self.assertTrue(path.exists())
        kernel32.WaitForSingleObject.assert_not_called()
        kernel32.CloseHandle.assert_not_called()

    def test_windows_wait_errors_retain_lease_and_close_handle(self):
        path = self.lease()
        kernel32, _, _ = self.windows()
        for result in (0xFFFFFFFF, 0x80, 42):
            with self.subTest(result=result):
                kernel32.WaitForSingleObject.return_value = result
                kernel32.CloseHandle.reset_mock()
                self.assertEqual(generation_leases.references(self.home), {"source"})
                self.assertTrue(path.exists())
                kernel32.CloseHandle.assert_called_once_with(0x100000001)

    def test_windows_wait_exception_retains_lease_and_closes_handle(self):
        path = self.lease()
        kernel32, _, _ = self.windows()
        kernel32.WaitForSingleObject.side_effect = OSError("wait unavailable")

        with self.assertRaisesRegex(OSError, "wait unavailable"):
            generation_leases.references(self.home)
        self.assertTrue(path.exists())
        kernel32.CloseHandle.assert_called_once_with(0x100000001)

    def test_windows_loader_failure_retains_lease(self):
        path = self.lease()
        _, load, kill = self.windows()
        load.side_effect = OSError("inspection unavailable")

        with self.assertRaisesRegex(OSError, "inspection unavailable"):
            generation_leases.references(self.home)
        self.assertTrue(path.exists())
        kill.assert_not_called()

    def test_windows_oversized_pid_is_not_truncated(self):
        path = self.lease(pid=0x100000000)
        kernel32, load, kill = self.windows()

        self.assertEqual(generation_leases.references(self.home), {"source"})
        self.assertTrue(path.exists())
        load.assert_not_called()
        kernel32.OpenProcess.assert_not_called()
        kill.assert_not_called()

    def test_posix_liveness_semantics_are_unchanged(self):
        for outcome, retained in (
            (None, True),
            (PermissionError(), True),
            (ProcessLookupError(), False),
        ):
            with self.subTest(outcome=outcome):
                path = self.lease()
                kill = Mock(side_effect=outcome)
                with patch.object(
                    generation_leases, "os", SimpleNamespace(name="posix", kill=kill)
                ):
                    self.assertEqual(
                        generation_leases.references(self.home),
                        {"source"} if retained else set(),
                    )
                kill.assert_called_once_with(123, 0)
                self.assertEqual(path.exists(), retained)

    def test_posix_unknown_error_fails_closed(self):
        path = self.lease()
        with patch.object(
            generation_leases,
            "os",
            SimpleNamespace(name="posix", kill=Mock(side_effect=OSError("unknown"))),
        ):
            with self.assertRaisesRegex(OSError, "unknown"):
                generation_leases.references(self.home)
        self.assertTrue(path.exists())

    def test_invalid_lease_fails_closed_before_process_check(self):
        for pid in (0, -1, True, "123"):
            with self.subTest(pid=pid):
                path = self.lease(pid=pid)
                with patch.object(generation_leases, "_process_exists") as exists:
                    with self.assertRaisesRegex(ValueError, "storage retained"):
                        generation_leases.references(self.home)
                exists.assert_not_called()
                self.assertTrue(path.exists())

    def test_real_subprocess_survives_checks_and_exited_lease_is_reaped(self):
        # Runs the real platform implementation, not Win32 stubs. The marker is
        # essential: Windows TerminateProcess(..., 0) can also return exit code 0.
        script = (
            "import sys; "
            "print('ready', flush=True); "
            "sys.stdin.read(); "
            "print('survived', flush=True)"
        )
        with subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            path = self.lease(pid=child.pid)
            try:
                for _ in range(3):
                    self.assertEqual(generation_leases.references(self.home), {"source"})
                    self.assertTrue(path.exists())
                output, errors = child.communicate("release\n", timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(output.splitlines(), ["ready", "survived"])
        self.assertEqual(generation_leases.references(self.home), set())
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()