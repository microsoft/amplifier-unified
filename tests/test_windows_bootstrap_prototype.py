"""Prototype packaging/command models, not clean-Windows installation evidence."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capsule = load("windows_bootstrap_capsule")
launcher = load("windows_prototype_launch")


def wheel(path, name="amplifier-unified", version="0.54.5"):
    with zipfile.ZipFile(path, "w") as output:
        output.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


class CapsuleTests(unittest.TestCase):
    def test_reproducible_hashed_capsule_and_fresh_output_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheels = base / "wheels"
            wheels.mkdir()
            wheel(wheels / "amplifier_unified-0.54.5-py3-none-any.whl")
            wheel(wheels / "dependency-1-py3-none-any.whl", "dependency", "1")
            args = dict(source="a" * 40, version="0.54.5", python="3.13.8",
                        url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")
            first = capsule.build(wheels, base / "first", **args)
            second = capsule.build(wheels, base / "second", **args)
            self.assertEqual(first, second)
            manifest = json.loads((base / "first/windows-prototype.json").read_text())
            self.assertEqual(manifest["status"], "prototype")
            self.assertEqual(manifest["source"], "a" * 40)
            with zipfile.ZipFile(base / "first/windows-prototype.zip") as archive:
                requirements = archive.read("requirements.txt").decode()
                self.assertEqual(requirements.count("--hash=sha256:"), 2)
                self.assertNotIn("git+", requirements)
                self.assertEqual(archive.read("launch.py"),
                                 (ROOT / "scripts/windows_prototype_launch.py").read_bytes())
                for entry in archive.infolist():
                    self.assertNotIn("..", entry.filename)
                    self.assertFalse(entry.filename.startswith("/"))
            with self.assertRaises(FileExistsError):
                capsule.build(wheels, base / "first", **args)
            self.assertEqual(capsule.sha256(base / "first/windows-prototype.zip"), first["capsuleSha256"])

    def test_rejects_missing_app_duplicate_identity_and_invalid_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheel(base / "other-1-py3-none-any.whl", "other", "1")
            args = dict(source="a" * 40, version="0.54.5", python="3.13.8",
                        url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")
            with self.assertRaisesRegex(ValueError, "selected app"):
                capsule.build(base, base / "out", **args)
            wheel(base / "duplicate-1-py3-none-any.whl", "other", "1")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                capsule.build(base, base / "out", **args)
            for key, value in (("source", "main"), ("python", "3.15"),
                               ("url", "http://example.test/capsule.zip")):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    capsule.build(base, base / "out", **{**args, key: value})
            self.assertFalse((base / "out").exists())

    def test_rejects_symlink_wheel(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheel(base / "real-0.54.5-py3-none-any.whl")
            (base / "link-0.54.5-py3-none-any.whl").symlink_to(base / "real-0.54.5-py3-none-any.whl")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                capsule.build(base, base / "out", source="a"*40, version="0.54.5", python="3.13.8",
                              url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")

    def test_wrong_platform_and_free_threaded_wheels_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("pkg-1-cp313-cp313-manylinux_x86_64.whl",
                         "pkg-1-cp313t-cp313t-win_amd64.whl"):
                path = Path(temporary) / name
                wheel(path, "pkg", "1")
                with self.assertRaisesRegex(ValueError, "ordinary Windows"):
                    capsule.wheel_identity(path)


class LauncherTests(unittest.TestCase):
    def test_environment_survives_missing_shell_path_without_copying_keys(self):
        root = Path("/synthetic/trial space/Unicode-λ")
        receipt = {"uv": "/private/tools/uv", "git": "/private/git/bin/git",
                   "environment": {"OPENAI_API_KEY": "must-not-be-used"}}
        with mock.patch.dict(os_environ := launcher.os.environ, {"PATH": "/old/path"}, clear=True):
            before = dict(os_environ)
            env = launcher.child_environment(root, receipt)
            self.assertEqual(env["AMPLIFIER_SESSION_STATE_HOME"], str(root / "ownership"))
            self.assertEqual(env["AMPLIFIER_HOME"], str(root / "shared"))
            self.assertEqual(env["AMPLIFIER_UNIFIED_IMPORT_HOME"], str(root / "shared"))
            self.assertEqual(env["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"], str(root / "shared/projects"))
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("HOME", env)
            self.assertNotIn("USERPROFILE", env)
            self.assertEqual(env["PATH"], "/private/tools:/private/git/bin:/old/path")
            self.assertEqual(dict(os_environ), before)

    def test_cli_commands_use_list_argv_and_hide_sensitive_child_errors(self):
        root = Path("/synthetic/space λ")
        scope = launcher.connection_args(root)
        self.assertEqual(scope[scope.index("--bind")+1], "127.0.0.1")
        self.assertEqual(scope[scope.index("--port")+1], "8942")
        self.assertEqual(scope[scope.index("--public-origin")+1], "https://127.0.0.1:8942")
        with mock.patch.object(launcher.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"public-ca", b"")
            self.assertEqual(launcher.run_cli(root / "exe", ["setup-tls", "export"], {}), b"public-ca")
            self.assertEqual(run.call_args.args[0], [str(root / "exe"), "setup-tls", "export"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            run.return_value = subprocess.CompletedProcess([], 1, b"sensitive-grant", b"secret")
            with self.assertRaises(RuntimeError) as caught:
                launcher.run_cli(root / "exe", ["open"], {})
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("sensitive-grant", str(caught.exception))

    def test_listener_timeout_and_exited_child_are_distinct(self):
        server = mock.Mock()
        server.poll.return_value = 1
        with mock.patch.object(launcher.ssl, "create_default_context"), self.assertRaisesRegex(RuntimeError, "exited"):
            launcher.wait_for_listener(server, Path("/public/ca"))
        server.poll.return_value = None
        with mock.patch.object(launcher.ssl, "create_default_context"), \
             mock.patch.object(launcher.time, "monotonic", side_effect=[0, 100]), \
             self.assertRaisesRegex(RuntimeError, "startup did not finish"):
            launcher.wait_for_listener(server, Path("/public/ca"), timeout=1)
        server.kill.assert_not_called()
        server.terminate.assert_not_called()

    def test_check_rejects_non_windows_before_listener_or_mutation(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher.sys, "platform", "linux"), \
             self.assertRaisesRegex(RuntimeError, "Windows CPython"):
            launcher.check_installation(Path("/trial"), {})

    def test_host_nonzero_exit_is_not_launcher_success(self):
        server = mock.Mock()
        server.poll.return_value = 1
        server.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "unexpectedly"):
            launcher.keep_foreground(server)
        server.kill.assert_not_called()
        server.terminate.assert_not_called()

    def test_ctrl_c_waits_exact_child_and_preserves_cleanup_custody(self):
        server = mock.Mock()
        server.poll.side_effect = [None, None, 0]
        server.wait.side_effect = [KeyboardInterrupt, 0]
        server.returncode = 0
        launcher.keep_foreground(server)
        self.assertEqual(server.wait.call_count, 2)
        server.kill.assert_not_called()

    def test_ctrl_c_during_browser_setup_retains_normal_exit_classification(self):
        server = mock.Mock()
        server.poll.return_value = 130
        server.returncode = 130
        launcher.keep_foreground(server, interrupted=True)
        with self.assertRaisesRegex(RuntimeError, "did not complete normally"):
            launcher.keep_foreground(server)

    def test_browser_refusal_is_pending_without_shutdown_wait(self):
        with mock.patch.object(launcher, "run_cli", side_effect=RuntimeError("closed")), \
             mock.patch.object(launcher, "keep_foreground") as keep:
            self.assertFalse(launcher.open_owned_browser(Path("/host/exe"), Path("/trial"), {}))
            keep.assert_not_called()

    def test_relaunch_refuses_unowned_occupied_port_before_config(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher, "check_installation", return_value=Path("/host/exe")), \
             mock.patch.object(launcher, "child_environment", return_value={}), \
             mock.patch.object(launcher, "listener_present", return_value=True), \
             mock.patch.object(launcher, "browser_trust_ready", return_value=False), \
             mock.patch.object(launcher, "run_cli") as run, \
             mock.patch.object(launcher.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                launcher.launch(Path("/trial"), {})
            run.assert_not_called()
            spawn.assert_not_called()

    def test_relaunch_existing_host_uses_owner_open_without_second_server(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher, "check_installation", return_value=Path("/host/exe")), \
             mock.patch.object(launcher, "child_environment", return_value={}), \
             mock.patch.object(launcher, "listener_present", return_value=True), \
             mock.patch.object(launcher, "browser_trust_ready", return_value=True), \
             mock.patch.object(launcher, "run_cli") as run, \
             mock.patch.object(launcher.subprocess, "Popen") as spawn:
            launcher.launch(Path("/trial"), {})
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[1][-1], "open")
            spawn.assert_not_called()


class BootstrapSourceTests(unittest.TestCase):
    def test_prototype_keeps_public_gate_and_separate_artifact_identity(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        public = source.split("function Assert-NativeActivationContract", 1)[1].split("function ", 1)[0]
        self.assertIn("throw 'Native activation integration is pending", public)
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1].split("# Dot sourcing", 1)[0]
        self.assertNotIn("Assert-NativeActivationContract", proto)
        self.assertIn("Read-PrototypeManifest", proto)
        self.assertLess(proto.index("Assert-FileChecksum $planPath"), proto.index("Find-InstallHelper"))
        self.assertLess(proto.index("Assert-FileChecksum $archive"), proto.index("Expand-ContainedZip"))
        for required in ("'--require-hashes'", "'--no-index'", "'--no-deps'", "'--only-binary', ':all:'"):
            self.assertIn(required, proto)
        self.assertNotIn("'tool', 'install'", proto)
        self.assertNotIn("'service'", proto)
        self.assertNotIn("Register-ScheduledTask", proto)
        self.assertNotIn("Import-Certificate", proto)
        self.assertNotIn("-ExecutionPolicy", source)
        self.assertNotRegex(source, r"\$env:\w+\s*=")

    def test_no_prerequisite_fallback_and_absolute_relaunch(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1]
        self.assertIn("if (-not $uv) { $uv = Install-VerifiedHelper 'uv' }", proto)
        self.assertIn("if (-not $git) { $git = Install-VerifiedHelper 'git' }", proto)
        self.assertIn("'python', 'install', $plan.python, '--no-bin', '--no-registry'", proto)
        self.assertIn("$shortcutPath = Write-PrototypeShortcut $hostPython $launcher", proto)
        self.assertIn("$shortcut.TargetPath = $Python", source)
        self.assertIn("$shortcut.WorkingDirectory = $script:InstallWork", source)
        self.assertIn("ConvertTo-NativeArgument $launcher", proto)
        self.assertNotIn("py.exe", proto)
        self.assertNotIn("winget", proto.lower())
        self.assertNotIn("curl", proto.lower())
        for field in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_BIN_DIR"):
            self.assertIn(field, source.split("function Get-PrototypeEnvironment", 1)[1])

    def test_launcher_normal_startup_explicit_trust_and_no_auto_credential_read(self):
        source = (ROOT / "scripts/windows_prototype_launch.py").read_text()
        for text in ('"--no-open", "serve"', '"open"', 'subprocess.Popen', 'pinned.read_private("prototype.json"',
                     'os.startfile(str(ca))', 'answer == "REVIEW"', 'browser_trust_ready()'):
            self.assertIn(text, source)
        for text in ("control-token", "session-secret", "Import-Certificate", "CERT_NONE", "check_hostname = False",
                     "create_app(", "os.kill(", "TerminateProcess"):
            self.assertNotIn(text, source)

    def test_tool_rename_sharing_is_private_directory_only(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        method = source.split("public void EnableToolWrites", 1)[1].split("public FileStream CreateFile", 1)[0]
        for required in ("!pin.directory", "!pin.privateAcl", "!IsOwnedDirectory(path)",
                         "Verify(pin)", "0x20081u, 3u", "info.high != pin.high",
                         "previous.Dispose()"):
            self.assertIn(required, method)
        self.assertLess(method.index("Info info = Inspect"), method.index("previous.Dispose()"))
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1]
        self.assertLess(proto.index("EnableToolWrites($path)"), proto.index("'python', 'install'"))


@unittest.skipUnless(sys.platform == "win32", "native prototype PowerShell model pending")
class NativePrototypeModelTests(unittest.TestCase):
    def test_both_available_powershell_editions_offline(self):
        available = [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)]
        if not available:
            self.skipTest("No native PowerShell executable; no model execution claimed")
        for name in available:
            with self.subTest(shell=name):
                environment = {key: value for key, value in launcher.os.environ.items()
                               if name != "powershell.exe" or key.upper() != "PSMODULEPATH"}
                result = subprocess.run(
                    [shutil.which(name), "-NoProfile", "-NonInteractive", "-File",
                     str(ROOT / "tests/fixtures/windows_bootstrap_prototype.ps1"),
                     "-Installer", str(ROOT / "scripts/install.ps1")],
                    capture_output=True, text=True, timeout=90, env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PROTOTYPE OFFLINE MODEL PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()