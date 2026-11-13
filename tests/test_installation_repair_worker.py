"""No service, installer, browser, or network effects: stdlib fixtures + mocks."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import venv
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "repair_worker_test_subject",
    Path(__file__).resolve().parents[1] / "amplifier_web/installation_repair_worker.py",
)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class RepairFixture(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "data"
        self.home.mkdir(mode=0o700)
        self.namespace = self.home / "installation-repair"
        self.namespace.mkdir(mode=0o700)
        self.folder = self.namespace / "operation-1"
        self.folder.mkdir(mode=0o700)
        self.tools, self.bins = self.root / "tools", self.root / "bin"
        self.tools.mkdir()
        self.bins.mkdir()
        self.prefix = self.tools / "amplifier-unified"
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "bin/python").write_text("old interpreter")
        (self.prefix / "bin/amplifier-unified").write_text("old launcher target")
        (self.prefix / "old-marker").write_text("preserve exact old prefix")
        self.populate_distribution(self.prefix, "1.2.2", "c" * 40)
        (self.prefix / "uv-receipt.toml").write_text("old uv receipt")
        self.old_inode = self.prefix.stat().st_ino
        self.launcher = self.bins / "amplifier-unified"
        self.launcher.symlink_to(self.prefix / "bin/amplifier-unified")
        (self.home / "updates").mkdir()
        self.pointer = self.home / "updates/application-active.json"
        self.pointer.write_bytes(b'{"exact": "saved pointer bytes"}\n')
        self.pointer.chmod(0o600)
        self.pointer_before = self.pointer.read_bytes()
        self.retained = []
        for name in ("app.sqlite3", "settings.yaml", "source-store/input", "runtime/cache",
                     "sessions/chat/record", "updates/applications/retained/receipt"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("leave untouched: " + name).encode())
            self.retained.append((path, path.read_bytes(), path.stat().st_ino))
        (self.home / "config/auth").mkdir(parents=True)
        self.token = self.home / "config/auth/control-token"
        self.token.write_text("a" * 64 + "\n")
        self.token.chmod(0o600)
        self.service_file = self.root / "service.unit"
        self.service_file.write_text("managed service fixture")
        self.uv = self.root / "uv"
        self.uv.write_text("not executed")
        self.uv.chmod(0o700)
        shutil.copyfile(Path(worker.__file__).with_name("app_release_artifacts.py"),
                        self.folder / "app_release_artifacts.py")
        with patch.object(worker, "__file__", str(self.folder / "installation_repair_worker.py")):
            self.artifacts = worker._sibling("app_release_artifacts")
        wheel = self.folder / self.artifacts.filename("1.2.3")
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(self.artifacts.STAMP, json.dumps({
                "schema": 1, "repository": self.artifacts.REPOSITORY,
                "revision": "b" * 40, "version": "1.2.3",
            }))
        self.artifact = {
            "path": str(wheel),
            "url": self.artifacts.SOURCE + "/releases/download/v1.2.3/" + wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }
        self.commands = [
            ["systemctl", "--user", "stop", "amplifier-unified.service"],
            ["systemctl", "--user", "start", "amplifier-unified.service"], None,
        ]
        info = self.home.stat()
        self.plan = {
            "operationId": self.folder.name, "previewId": "preview-1", "hostInstanceId": "service-uuid",
            "home": str(self.home), "uid": os.getuid(), "dataStat": [info.st_dev, info.st_ino],
            "runningIdentity": {"version": "1.2.2", "revision": "c" * 40, "instanceId": "d" * 32},
            "toolRoot": str(self.tools), "binRoot": str(self.bins), "uv": str(self.uv),
            "basePython": str(Path(sys.executable).resolve()),
            "service": {"commands": self.commands, "path": str(self.service_file),
                        "sha256": worker._digest(self.service_file), "pid": 123,
                        "processStart": "Mon Nov 16 11:22:33 2026"},
            "launcher": worker.file_identity(self.launcher),
            "bootstrap": worker.bootstrap_identity(self.prefix),
            "uvReceiptDigest": worker._digest(self.prefix / "uv-receipt.toml"),
            "target": {"version": "1.2.3", "revision": "b" * 40, "source": self.artifact["url"]},
            "extras": ["native-desktop"], "artifact": self.artifact,
            "probe": "PACKAGE_PROBE", "dependencyProbe": "DEPENDENCY_PROBE",
            "health": {"urls": ["http://127.0.0.1:12345/api/health"], "pin": None},
            "pointer": {"exists": True, "sha256": worker._digest(self.pointer)},
            "env": {"SENSITIVE": "do-not-leak-fixture-secret", "UV_NO_CACHE": "1",
                    "PLAYWRIGHT_BROWSERS_PATH": str(self.home / "browser-cache")},
        }
        operation = {
            "operationId": self.folder.name, "previewId": "preview-1", "hostInstanceId": "service-uuid",
            "status": "accepted", "stage": "accepted", "reinstalled": False,
            "serviceState": "not-checked", "healthVerified": False, "detail": "Accepted",
        }
        worker.write(self.folder / "operation.json", operation, exclusive=True)
        worker.write(self.folder / "plan.json", self.plan, exclusive=True)
        self.events = []
        self.pid = 123
        self.active_locks = set()
        self.failure_mode = None
        self.doctor_ok = True
        self.health_override = None
        self.protocol = 1
        self.process_start = self.plan["service"]["processStart"]
        self.graph_change = None
        self.other_process = False
        self.reset = SimpleNamespace(
            repair_lock=self.repair_lock, tool_recovery_root=self.tool_recovery_root,
            service_commands=lambda home: self.commands, assert_no_processes=self.no_processes,
            stop_service=self.stop, start_service=self.start,
        )
        self.addCleanup(patch.stopall)
        patch.object(worker, "siblings", return_value=(self.reset, self.artifacts)).start()
        patch.object(worker, "command", side_effect=self.command).start()
        patch.object(worker, "_service_pid", side_effect=lambda plan: self.pid).start()
        patch.object(worker, "_health_get", side_effect=self.health).start()
        patch.object(worker, "HEALTH_WAIT", 0).start()

    def populate_distribution(self, prefix, version, revision, artifact=None):
        site = prefix / "lib/python3.13/site-packages"
        dist = site / ("amplifier_unified-" + version + ".dist-info")
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text("Name: amplifier-unified\nVersion: " + version + "\n")
        if artifact:
            (dist / "direct_url.json").write_text(json.dumps(self.direct_url(artifact)))
        app = site / "amplifier_web"
        app.mkdir()
        (app / "release-source.json").write_text(json.dumps({
            "schema": 1, "repository": "microsoft/amplifier-unified",
            "version": version, "revision": revision,
        }))

    def direct_url(self, artifact):
        return {"url": Path(artifact["path"]).as_uri(),
                "archive_info": {"hashes": {"sha256": artifact["sha256"]}}}

    def graph(self):
        return [
            {"name": "amplifier-unified", "version": "1.2.3", "direct": self.direct_url(self.artifact),
             "stamp": {"schema": 1, "repository": "microsoft/amplifier-unified",
                       "version": "1.2.3", "revision": "b" * 40}},
            {"name": "example", "version": "4.5.6", "direct": None},
        ]

    def save_plan(self):
        worker.write(self.folder / "plan.json", self.plan)

    def tool_recovery_root(self, tools):
        return tools.parent / (tools.name + "-amplifier-unified-recovery")

    @contextmanager
    def repair_lock(self, path, **kwargs):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.events.append(("lock", path))
        if self.failure_mode == "lease" and path == self.home:
            raise RuntimeError("do-not-leak-fixture-secret")
        self.assertNotIn(path, self.active_locks)
        self.active_locks.add(path)
        try:
            yield
        finally:
            self.active_locks.remove(path)
            self.events.append(("unlock", path))

    def no_processes(self, prefix, home):
        self.assertEqual(self.pid, 0)
        self.events.append(("unused", prefix))

    def stop(self, commands):
        self.events.append(("stop", commands))
        self.assertEqual(self.receipt()["stage"], "stopping")
        self.pid = 0
        return True

    def start(self, commands):
        self.assertNotIn(self.home, self.active_locks)
        self.events.append(("start", commands))
        if self.failure_mode == "start":
            raise RuntimeError("do-not-leak-fixture-secret")
        self.pid = 456

    def health(self, url, pin, token):
        self.assertEqual(token, "a" * 64)
        successor = self.pid != 123
        self.events.append(("successor-health" if successor else "old-health",))
        value = {
            "ok": True, "app": "amplifier-unified",
            "dataIdentity": hashlib.sha256(str(self.home).encode()).hexdigest(),
            **(self.plan["target"] if successor else self.plan["runningIdentity"]),
        }
        if successor:
            value["instanceId"] = "e" * 32
            value["installationRepair"] = {"operationId": self.folder.name, "protocol": 1}
            if self.health_override:
                value.update(self.health_override)
        return value

    def command(self, *args, env=None, **kwargs):
        args = [str(arg) for arg in args]
        if args[0] == "systemctl":
            return subprocess.CompletedProcess([], 0, (
                "MainPID=" + str(self.pid) + "\nFragmentPath=" + str(self.service_file)
                + "\nDropInPaths=\nNeedDaemonReload=no\n"), "")
        if args[0] == "ps":
            if args[-1] == "lstart=":
                return subprocess.CompletedProcess([], 0, self.process_start + "\n", "")
            if args[-1] == "uid=":
                return subprocess.CompletedProcess([], 0, str(os.getuid()) + "\n", "")
            output = f"123 {self.prefix}/bin/python -m amplifier_web\n"
            if self.other_process:
                output += f"789 {self.prefix}/bin/python -m amplifier_web --data-dir /other-profile\n"
            return subprocess.CompletedProcess([], 0, output, "")
        if args[0] == str(self.uv) and args[1:3] == ["tool", "install"]:
            is_candidate = env["UV_TOOL_DIR"] != str(self.tools)
            event = "candidate-install" if is_candidate else "fresh-install"
            self.events.append((event, args, dict(env)))
            self.assertEqual(env["UV_NO_CACHE"], "1")
            target = Path(env["UV_TOOL_DIR"]) / "amplifier-unified"
            (target / "bin").mkdir(parents=True)
            (target / "bin/python").write_text("fresh interpreter")
            (target / "bin/amplifier-unified").write_text("fresh launcher target")
            self.populate_distribution(target, "1.2.3", "b" * 40, self.artifact)
            (target / "uv-receipt.toml").write_text("fresh uv receipt")
            (target / "new-marker").write_text("preserve failed candidates too")
            if not is_candidate:
                self.assertIn(self.home, self.active_locks)
                self.assertFalse(self.pointer.exists())
                self.assertTrue((self.tool_recovery_root(self.tools) / self.folder.name / "previous").is_dir())
                self.launcher.unlink()
                self.launcher.symlink_to(self.prefix / "bin/amplifier-unified")
            if self.failure_mode == event:
                raise worker.CommandFailed()
            if self.failure_mode == "crash" and not is_candidate:
                raise KeyboardInterrupt("do-not-leak-fixture-secret")
            return subprocess.CompletedProcess([], 0, "", "")
        if args[0] == str(self.uv) and args[1:3] == ["pip", "freeze"]:
            self.events.append(("freeze", args))
            return subprocess.CompletedProcess([], 0, "example==4.5.6\n", "")
        if "playwright" in args:
            self.events.append(("browser-install", args, dict(env)))
            return subprocess.CompletedProcess([], 0, "", "")
        if "-c" in args:
            is_candidate = str(self.folder / "candidate-tools") in args[0]
            kind = "candidate" if is_candidate else "fresh"
            if worker.PROTOCOL_PROBE in args:
                self.events.append((kind + "-protocol", args))
                return subprocess.CompletedProcess([], 0, json.dumps({"protocol": self.protocol}), "")
            if worker.GRAPH_PROBE in args:
                self.events.append((kind + "-graph", args))
                graph = self.graph()
                if self.graph_change and not is_candidate:
                    graph = self.graph_change(graph)
                return subprocess.CompletedProcess([], 0, json.dumps(graph), "")
            probe = "dependency" if "DEPENDENCY_PROBE" in args else "package"
            self.events.append((kind + "-" + probe, args))
            self.assertEqual(args[1:3], ["-I", "-c"])
            self.assertEqual(args[-len(self.plan["extras"]):], self.plan["extras"])
            if self.failure_mode == kind + "-" + probe:
                raise ValueError("do-not-leak-fixture-secret")
            return subprocess.CompletedProcess([], 0, "AMPLIFIER_UPDATE_PROBE=" + json.dumps({
                "ok": True, "isolated": True, "version": self.plan["target"]["version"],
            }), "")
        if "doctor" in args:
            self.events.append(("doctor", args))
            return subprocess.CompletedProcess([], 0, json.dumps({"ok": self.doctor_ok}), "")
        raise AssertionError("Unexpected command")

    def receipt(self):
        return worker.read(self.folder / "operation.json")

    def run_worker(self):
        with patch("builtins.print") as printed:
            result = worker.run(self.folder)
        printed.assert_not_called()
        self.assertEqual(result, self.receipt())
        self.assertNotIn("do-not-leak-fixture-secret", (self.folder / "operation.json").read_text())
        for path, raw, inode in self.retained:
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_ino, inode)
        return result

    def names(self):
        return [row[0] for row in self.events]

    def test_success_candidate_before_stop_probes_constraints_and_backups(self):
        result = self.run_worker()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["reinstalled"])
        self.assertTrue(result["healthVerified"])
        self.assertTrue(result["doctorVerified"])
        names = self.names()
        ordered = ["candidate-install", "candidate-package", "candidate-dependency", "freeze",
                   "old-health", "stop", "fresh-install", "fresh-package", "fresh-dependency",
                   "start", "doctor", "successor-health"]
        self.assertEqual([name for name in names if name in ordered], ordered)
        install = next(row for row in self.events if row[0] == "fresh-install")
        self.assertIn("--force", install[1])
        self.assertEqual(install[1][install[1].index("--constraint") + 1], str(self.folder / "constraints.txt"))
        self.assertIn(self.artifact["path"], install[1][install[1].index("--from") + 1])
        self.assertEqual(worker.read(self.folder / "plan.json"), self.plan)
        previous = self.tool_recovery_root(self.tools) / self.folder.name / "previous"
        self.assertEqual(previous.stat().st_ino, self.old_inode)
        self.assertEqual((previous / "old-marker").read_text(), "preserve exact old prefix")
        self.assertEqual((self.folder / "backup/application-active.json").read_bytes(), self.pointer_before)
        self.assertTrue((self.folder / "backup/launcher").is_symlink())
        self.assertFalse(self.pointer.exists())
        self.assertEqual(stat.S_IMODE((self.folder / "operation.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.folder / "constraints.txt").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.folder / "candidate-graph.json").stat().st_mode), 0o600)
        self.assertFalse(self.active_locks)

    def test_candidate_install_failure_never_stops(self):
        self.failure_mode = "candidate-install"
        result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("failed", "preflight-failed"))
        self.assertFalse(result["reinstalled"])
        self.assertNotIn("stop", self.names())
        self.assertEqual(self.prefix.stat().st_ino, self.old_inode)
        self.assertEqual(self.pointer.read_bytes(), self.pointer_before)

    def test_candidate_package_and_dependency_failure_never_stop(self):
        self.failure_mode = "candidate-dependency"
        result = self.run_worker()
        self.assertEqual(result["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())
        self.assertIn("candidate-package", self.names())

    def test_managed_browser_candidate_installs_before_probe_using_parent_cache(self):
        self.plan["extras"] = ["managed-browser"]
        self.save_plan()
        self.assertEqual(self.run_worker()["status"], "ready")
        self.assertLess(self.names().index("browser-install"), self.names().index("candidate-package"))
        browser = next(row for row in self.events if row[0] == "browser-install")
        self.assertEqual(browser[1][1:], ["-I", "-m", "playwright", "install", "chromium"])
        self.assertEqual(browser[2]["PLAYWRIGHT_BROWSERS_PATH"], self.plan["env"]["PLAYWRIGHT_BROWSERS_PATH"])

    def test_rollback_restores_exact_paths_preserves_failed_candidate_and_restarts_after_lease(self):
        self.failure_mode = "fresh-dependency"
        result = self.run_worker()
        self.assertEqual(result["status"], "rolled-back")
        self.assertFalse(result["reinstalled"])
        self.assertFalse(result["healthVerified"])
        self.assertEqual(self.prefix.stat().st_ino, self.old_inode)
        self.assertEqual(self.pointer.read_bytes(), self.pointer_before)
        self.assertTrue(self.launcher.is_symlink())
        failed = self.tool_recovery_root(self.tools) / self.folder.name / "failed"
        self.assertTrue((failed / "new-marker").exists())
        self.assertTrue((self.folder / "candidate-tools/amplifier-unified/new-marker").exists())
        self.assertEqual(self.names().count("start"), 1)
        self.assertNotIn("doctor", self.names())

    def test_rollback_incomplete_is_unknown_and_never_restarts(self):
        self.failure_mode = "fresh-install"
        real_move = worker._move

        def move(source, destination):
            if source.name == "previous":
                raise OSError("do-not-leak-fixture-secret")
            real_move(source, destination)

        with patch.object(worker, "_move", side_effect=move):
            result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("unknown", "rollback-incomplete"))
        self.assertNotIn("start", self.names())
        self.assertEqual(result["serviceState"], "stopped")

    def test_lease_failure_leaves_service_stopped_without_claiming_install(self):
        self.failure_mode = "lease"
        result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("failed", "left-stopped"))
        self.assertEqual(result["serviceState"], "stopped")
        self.assertFalse(result["reinstalled"])
        self.assertEqual(self.prefix.stat().st_ino, self.old_inode)
        self.assertNotIn("fresh-install", self.names())
        self.assertNotIn("start", self.names())

    def test_start_failure_is_reinstalled_not_ready(self):
        self.failure_mode = "start"
        result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("reinstalled", "restart-incomplete"))
        self.assertTrue(result["reinstalled"])
        self.assertFalse(result["healthVerified"])
        self.assertEqual(result["serviceState"], "stopped")

    def test_doctor_failure_does_not_claim_ready(self):
        self.doctor_ok = False
        result = self.run_worker()
        self.assertEqual(result["status"], "reinstalled")
        self.assertFalse(result["doctorVerified"])
        self.assertFalse(result["healthVerified"])

    def test_old_health_instance_is_not_a_successor(self):
        self.health_override = {"instanceId": self.plan["runningIdentity"]["instanceId"]}
        result = self.run_worker()
        self.assertEqual(result["status"], "reinstalled")
        self.assertTrue(result["doctorVerified"])
        self.assertFalse(result["healthVerified"])
        self.assertEqual(result["serviceState"], "running")

    def test_wrong_successor_data_identity_not_ready(self):
        self.health_override = {"dataIdentity": "wrong"}
        self.assertFalse(self.run_worker()["healthVerified"])

    def test_successor_requires_this_operation_and_protocol_not_just_package_health(self):
        self.health_override = {"installationRepair": {"operationId": "another-operation", "protocol": 1}}
        self.assertEqual(self.run_worker()["status"], "reinstalled")

    def test_successor_rejects_boolean_protocol(self):
        self.health_override = {"installationRepair": {"operationId": self.folder.name, "protocol": True}}
        self.assertFalse(self.run_worker()["healthVerified"])

    def test_prefeature_candidate_protocol_fails_before_stop(self):
        self.protocol = 0
        self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())
        self.assertIn("candidate-protocol", self.names())

    def test_downgrade_is_rejected_before_candidate_install(self):
        self.plan["runningIdentity"]["version"] = "1.2.4"
        self.save_plan()
        self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("candidate-install", self.names())
        self.assertNotIn("stop", self.names())

    def test_graph_version_mismatch_rolls_back(self):
        def change(rows):
            rows[1]["version"] = "4.5.7"
            return rows
        self.graph_change = change
        self.assertEqual(self.run_worker()["status"], "rolled-back")
        self.assertEqual(self.prefix.stat().st_ino, self.old_inode)

    def test_graph_missing_dependency_rolls_back(self):
        self.graph_change = lambda rows: rows[:1]
        self.assertEqual(self.run_worker()["status"], "rolled-back")

    def test_graph_extra_dependency_rolls_back(self):
        self.graph_change = lambda rows: rows + [{"name": "foreign-extra", "version": "1", "direct": None}]
        self.assertEqual(self.run_worker()["status"], "rolled-back")

    def test_graph_dependency_source_mismatch_rolls_back(self):
        def change(rows):
            rows[1]["direct"] = {"url": "https://unselected.example/other.whl",
                                 "archive_info": {"hashes": {"sha256": "f" * 64}}}
            return rows
        self.graph_change = change
        self.assertEqual(self.run_worker()["status"], "rolled-back")

    def test_graph_app_archive_source_change_rolls_back(self):
        def change(rows):
            rows[0]["direct"]["url"] = "file:///different/app.whl"
            return rows
        self.graph_change = change
        self.assertEqual(self.run_worker()["status"], "rolled-back")

    def test_graph_saved_privately_uses_exact_official_url_only_for_app(self):
        self.assertEqual(self.run_worker()["status"], "ready")
        rows = worker.read(self.folder / "candidate-graph.json")
        self.assertEqual(rows[0]["direct"]["url"], self.artifact["url"])
        self.assertEqual(rows[1]["direct"], None)

    def test_changed_bootstrap_after_candidate_never_stops(self):
        real_candidate = worker.candidate
        def candidate(*args):
            real_candidate(*args)
            path = next(self.prefix.glob("lib/python*/site-packages/*.dist-info/METADATA"))
            path.write_text(path.read_text() + "changed")
        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_changed_launcher_after_candidate_never_stops(self):
        real_candidate = worker.candidate
        def candidate(*args):
            real_candidate(*args)
            self.launcher.unlink()
            self.launcher.symlink_to(self.prefix / "bin/python")
        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_changed_uv_receipt_after_candidate_never_stops(self):
        real_candidate = worker.candidate
        def candidate(*args):
            real_candidate(*args)
            (self.prefix / "uv-receipt.toml").write_text("foreign install receipt")
        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_reused_pid_with_new_start_time_never_stops(self):
        real_candidate = worker.candidate
        def candidate(*args):
            real_candidate(*args)
            self.process_start = "Mon Nov 16 11:22:35 2026"
        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_other_profile_using_shared_bootstrap_never_stops(self):
        self.other_process = True
        self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_owned_0755_data_directory_works_but_group_writable_does_not(self):
        self.home.chmod(0o755)
        self.assertEqual(self.run_worker()["status"], "ready")
        self.home.chmod(0o775)
        with self.assertRaises(ValueError):
            worker.directory(self.home)

    def test_foreign_launcher_preserved_unknown_without_restarting(self):
        original = self.command
        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            if ("tool" in args and "install" in args
                    and kwargs.get("env", {}).get("UV_TOOL_DIR") == str(self.tools)):
                self.launcher.unlink()
                self.launcher.symlink_to(self.root / "foreign-entry")
            return result
        with patch.object(worker, "command", side_effect=command):
            self.assertEqual(self.run_worker()["status"], "unknown")
        self.assertEqual(os.readlink(self.launcher), str(self.root / "foreign-entry"))
        self.assertNotIn("start", self.names())
        self.assertTrue((self.folder / "backup/application-active.json").exists())

    def test_foreign_prefix_provenance_is_not_moved_or_replaced_during_rollback(self):
        original = self.command
        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            if ("tool" in args and "install" in args
                    and kwargs.get("env", {}).get("UV_TOOL_DIR") == str(self.tools)):
                path = next(self.prefix.glob("lib/python*/site-packages/*.dist-info/direct_url.json"))
                path.write_text('{"url":"file:///foreign/archive.whl","archive_info":{}}')
                self.foreign_inode = self.prefix.stat().st_ino
            return result
        with patch.object(worker, "command", side_effect=command):
            self.assertEqual(self.run_worker()["status"], "unknown")
        self.assertEqual(self.prefix.stat().st_ino, self.foreign_inode)
        self.assertTrue((self.tool_recovery_root(self.tools) / self.folder.name / "previous").exists())
        self.assertFalse((self.tool_recovery_root(self.tools) / self.folder.name / "failed").exists())
        self.assertNotIn("start", self.names())

    def test_changed_backup_before_rollback_refuses_all_restore_moves(self):
        original = self.command
        def command(*args, **kwargs):
            if "DEPENDENCY_PROBE" in args and str(self.prefix) in str(args[0]):
                (self.folder / "backup/application-active.json").write_text("changed backup")
                raise ValueError("probe failed")
            return original(*args, **kwargs)
        with patch.object(worker, "command", side_effect=command):
            self.assertEqual(self.run_worker()["status"], "unknown")
        self.assertTrue((self.prefix / "new-marker").exists())
        self.assertFalse(self.pointer.exists())
        self.assertNotIn("start", self.names())

    def test_installer_failure_uses_only_safe_policy_facts_and_fixed_guidance(self):
        policy = SimpleNamespace(
            installer_failure_facts=Mock(return_value={
                "reason": "requires-python", "package": "example", "constraint": ">=3.14",
                "stderr": "do-not-leak-fixture-secret", "url": "https://token@private.example",
            }),
            installer_failure_guidance=Mock(return_value="Use a supported package release."),
        )
        original = self.command
        def command(*args, **kwargs):
            if "tool" in args and "install" in args:
                raise worker._installer_failure("private output", "do-not-leak-fixture-secret")
            return original(*args, **kwargs)
        with patch.object(worker, "_FAILURE_POLICY", policy), patch.object(
                worker, "command", side_effect=command):
            result = self.run_worker()
        self.assertEqual(result["stage"], "preflight-failed")
        self.assertEqual(result["installerFailure"], {
            "reason": "requires-python", "package": "example", "constraint": ">=3.14"})
        self.assertIn("Use a supported package release.", result["detail"])
        policy.installer_failure_facts.assert_called_once_with("private output", "do-not-leak-fixture-secret")
        self.assertNotIn("stop", self.names())

    def test_installer_guidance_survives_successful_rollback_receipt(self):
        original = self.command
        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            if ("tool" in args and "install" in args
                    and kwargs.get("env", {}).get("UV_TOOL_DIR") == str(self.tools)):
                raise worker.CommandFailed({"reason": "resolution-failed"}, "Check selected dependencies.")
            return result
        with patch.object(worker, "command", side_effect=command):
            result = self.run_worker()
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["installerFailure"], {"reason": "resolution-failed"})
        self.assertIn("Check selected dependencies.", result["detail"])

    def test_uv_empty_local_archive_info_is_absence_not_conflicting_checksum(self):
        row = {
            "name": "amplifier-unified", "version": self.plan["target"]["version"],
            "stamp": {"schema": 1, "repository": "microsoft/amplifier-unified",
                      "version": self.plan["target"]["version"],
                      "revision": self.plan["target"]["revision"]},
            "direct": {"url": Path(self.artifact["path"]).as_uri(), "archive_info": {}},
        }
        normalized = worker._app_source(self.plan, row)
        self.assertEqual(normalized["direct"]["archive_info"]["hashes"]["sha256"],
                         self.artifact["sha256"])
        row["direct"]["archive_info"] = {"hashes": {"sha256": "wrong"}}
        with self.assertRaises(ValueError):
            worker._app_source(self.plan, row)

    def test_worker_never_repeats_nonaccepted_operation(self):
        self.assertEqual(self.run_worker()["status"], "ready")
        count = len(self.events)
        self.assertEqual(self.run_worker()["status"], "ready")
        self.assertEqual(len(self.events), count)

    def test_explicit_known_reinstalled_verification_recovers_without_effect_replay(self):
        self.doctor_ok = False
        self.assertEqual(self.run_worker()["status"], "reinstalled")
        self.doctor_ok = True
        before = self.names().count("stop"), self.names().count("start"), self.names().count("fresh-install")
        result = worker.verify(self.folder)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["healthVerified"])
        self.assertEqual(before, (self.names().count("stop"), self.names().count("start"),
                                  self.names().count("fresh-install")))

    def test_verification_never_clears_unknown_or_replays_accepted_repair(self):
        with self.assertRaises(ValueError):
            worker.verify(self.folder)
        operation = worker.read(self.folder / "operation.json")
        operation.update(status="unknown", reinstalled=True)
        worker.write(self.folder / "operation.json", operation)
        with self.assertRaises(ValueError):
            worker.verify(self.folder)
        self.assertEqual(worker.read(self.folder / "operation.json")["status"], "unknown")
        self.assertFalse(self.events)

    def test_delegated_generation_host_in_other_profile_blocks_before_stop(self):
        original = self.command
        def command(*args, **kwargs):
            if list(args[:2]) == ["ps", "-axo"]:
                return subprocess.CompletedProcess([], 0,
                    "789 /other-data/updates/applications/rev/gen/tools/amplifier-unified/bin/python "
                    "-I -m amplifier_web --data-dir /other-data serve\n", "")
            return original(*args, **kwargs)
        with patch.object(worker, "command", side_effect=command):
            result = self.run_worker()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("stop", self.names())

    def test_default_foreground_sibling_without_serve_blocks_before_stop(self):
        original = self.command
        def command(*args, **kwargs):
            if list(args[:2]) == ["ps", "-axo"]:
                return subprocess.CompletedProcess([], 0,
                    "789 /other-data/updates/applications/rev/gen/tools/amplifier-unified/bin/python "
                    "-I -m amplifier_web --data-dir /profiles/B --port 9876\n", "")
            return original(*args, **kwargs)
        with patch.object(worker, "command", side_effect=command):
            result = self.run_worker()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("stop", self.names())

    def test_serving_invocation_distinguishes_default_launch_from_passive_cli(self):
        for tail in ([], ["--port", "9876"], ["--data-dir=/profiles/B", "--port=9876"],
                     ["--no-open", "--data-dir", "/profiles/B", "serve"]):
            with self.subTest(tail=tail):
                self.assertTrue(worker._serving_invocation(
                    ["/generation/bin/python", "-I", "-m", "amplifier_web", *tail]))
                self.assertTrue(worker._serving_invocation(["/bin/amplifier-unified", *tail]))
        self.assertFalse(worker._serving_invocation(
            ["/generation/bin/python", "-I", "-m", "amplifier_web",
             "--data-dir", "/profiles/B", "doctor", "--json"]))
        self.assertFalse(worker._serving_invocation(
            ["/generation/bin/python", "-I", "-m", "amplifier_web.runtime_worker"]))

    def test_health_only_verify_changed_final_launcher_remains_held_without_effects(self):
        self.doctor_ok = False
        self.assertEqual(self.run_worker()["status"], "reinstalled")
        self.doctor_ok = True
        self.launcher.unlink()
        self.launcher.symlink_to(self.prefix / "bin/python")
        before = self.names().count("stop"), self.names().count("start"), self.names().count("fresh-install")
        result = worker.verify(self.folder)
        self.assertEqual(result["status"], "reinstalled")
        self.assertFalse(result["healthVerified"])
        self.assertEqual(before, (self.names().count("stop"), self.names().count("start"),
                                  self.names().count("fresh-install")))

    def test_post_move_rollback_receipt_write_failure_stays_unknown_not_released(self):
        original = worker.Worker.receipt
        failed_once = False
        def receipt(subject, status, stage, detail, **facts):
            nonlocal failed_once
            if stage == "rolling-back" and not failed_once:
                failed_once = True
                raise OSError("fixture receipt unavailable")
            return original(subject, status, stage, detail, **facts)
        self.failure_mode = "fresh-install"
        with patch.object(worker.Worker, "receipt", receipt):
            result = self.run_worker()
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["healthVerified"])
        self.assertNotIn("start", self.names())
        count = len(self.events)
        self.run_worker()
        self.assertEqual(len(self.events), count)

    def test_crash_preserves_unknown_and_no_replay_or_restart(self):
        self.failure_mode = "crash"
        result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("unknown", "interrupted"))
        self.assertNotIn("start", self.names())
        self.assertTrue((self.tool_recovery_root(self.tools) / self.folder.name / "previous").exists())
        count = len(self.events)
        self.run_worker()
        self.assertEqual(count, len(self.events))

    def test_changed_artifact_rejected_before_candidate_or_stop(self):
        Path(self.artifact["path"]).write_bytes(b"tampered wheel")
        self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("candidate-install", self.names())
        self.assertNotIn("stop", self.names())

    def test_pointer_changed_during_candidate_fails_before_stop(self):
        real_candidate = worker.candidate

        def candidate(*args):
            real_candidate(*args)
            self.pointer.write_text("changed after preview")

        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_service_pid_changes_during_candidate_fail_before_stop(self):
        real_candidate = worker.candidate

        def candidate(*args):
            real_candidate(*args)
            self.pid = 999

        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_service_file_changes_during_candidate_fail_before_stop(self):
        real_candidate = worker.candidate

        def candidate(*args):
            real_candidate(*args)
            self.service_file.write_text("changed definition")

        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_plan_changes_during_candidate_fail_before_stop(self):
        real_candidate = worker.candidate

        def candidate(*args):
            real_candidate(*args)
            self.save_plan()
            worker.write(self.folder / "plan.json", {**self.plan, "previewId": "different"})

        with patch.object(worker, "candidate", side_effect=candidate):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_wrong_old_health_never_stops(self):
        with patch.object(worker, "_health_get", return_value={"ok": True, "app": "wrong"}):
            self.assertEqual(self.run_worker()["stage"], "preflight-failed")
        self.assertNotIn("stop", self.names())

    def test_no_prior_pointer_preserves_absence_on_rollback(self):
        self.pointer.unlink()
        self.plan["pointer"] = {"exists": False, "sha256": None}
        self.save_plan()
        self.failure_mode = "fresh-install"
        result = self.run_worker()
        self.assertEqual(result["status"], "rolled-back")
        self.assertFalse(self.pointer.exists())
        self.assertTrue(self.launcher.is_symlink())

    def test_foreign_pointer_during_fresh_install_is_preserved_unknown_not_rolled_back(self):
        original = self.command

        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            if ("tool" in args and "install" in args
                    and kwargs.get("env", {}).get("UV_TOOL_DIR") == str(self.tools)):
                self.pointer.write_text("unexpected fresh pointer")
            return result

        with patch.object(worker, "command", side_effect=command):
            result = self.run_worker()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.pointer.read_text(), "unexpected fresh pointer")
        self.assertEqual((self.folder / "backup/application-active.json").read_bytes(), self.pointer_before)
        self.assertNotIn("start", self.names())

    def test_plan_symlink_failure_is_preflight_and_no_repeat(self):
        plan_path = self.folder / "plan.json"
        real_plan = self.folder / "private-plan.json"
        plan_path.rename(real_plan)
        plan_path.symlink_to(real_plan)
        result = self.run_worker()
        self.assertEqual((result["status"], result["stage"]), ("failed", "preflight-failed"))
        self.assertFalse(self.events)
        self.run_worker()
        self.assertFalse(self.events)

    def test_private_receipt_symlink_hardlink_and_public_permissions_rejected(self):
        path = self.folder / "other.json"
        worker.write(path, {"kept": True}, exclusive=True)
        linked = self.folder / "link.json"
        linked.symlink_to(path)
        for action in (lambda: worker.read(linked), lambda: worker.write(linked, {}),
                       lambda: worker.run(self.root / "operation-link")):
            if not (self.root / "operation-link").exists():
                (self.root / "operation-link").symlink_to(self.folder, target_is_directory=True)
            with self.assertRaises(ValueError):
                action()
        hardlink = self.folder / "hard.json"
        os.link(path, hardlink)
        with self.assertRaises(ValueError):
            worker.read(hardlink)
        hardlink.unlink()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            worker.read(path)

    def test_exclusive_write_and_lock_do_not_replace_existing_state(self):
        path = self.folder / "other.json"
        worker.write(path, {"kept": True}, exclusive=True)
        with self.assertRaises(FileExistsError):
            worker.write(path, {}, exclusive=True)
        self.assertEqual(worker.read(path), {"kept": True})
        lock = self.folder / "worker.lock"
        with worker.lock(lock):
            with self.assertRaises(BlockingIOError):
                worker.run(self.folder)
        self.assertEqual(self.receipt()["status"], "accepted")


class HealthTransportTests(unittest.TestCase):
    def test_loaded_service_rejects_overrides_or_stale_definition(self):
        plan = {"service": {"path": "/unit", "pid": 123, "processStart": "original"}, "env": {},
                "uid": os.getuid()}
        for wrong in ["FragmentPath=/other", "DropInPaths=/override", "NeedDaemonReload=yes"]:
            values = {"MainPID": "123", "FragmentPath": "/unit", "DropInPaths": "", "NeedDaemonReload": "no"}
            key, value = wrong.split("=", 1)
            values[key] = value
            result = subprocess.CompletedProcess([], 0, "\n".join(k + "=" + v for k, v in values.items()), "")
            with self.subTest(wrong=wrong), patch.object(worker.sys, "platform", "linux"), patch.object(
                    worker, "command", return_value=result):
                with self.assertRaises(ValueError):
                    worker._check_service_process(plan)

    def test_mac_start_time_and_owner_checked_not_only_pid(self):
        plan = {"service": {"pid": 123, "processStart": "original"}, "env": {}, "uid": os.getuid()}
        for start, owner in [("reused PID start", str(os.getuid())), ("original", str(os.getuid() + 1))]:
            with self.subTest(start=start, owner=owner), patch.object(worker.sys, "platform", "darwin"), patch.object(
                    worker, "command", side_effect=[
                        subprocess.CompletedProcess([], 0, start, ""),
                        subprocess.CompletedProcess([], 0, owner, ""),
                    ]):
                with self.assertRaises(ValueError):
                    worker._check_service_process(plan)

    def test_mac_stopped_service_is_not_confused_with_its_detached_repair_job(self):
        plan = {
            "service": {"commands": [
                ["launchctl", "bootout", f"gui/{os.getuid()}/com.microsoft.amplifier-unified"],
                ["launchctl", "bootstrap"],
                ["launchctl", "print", f"gui/{os.getuid()}/com.microsoft.amplifier-unified"],
            ]}, "env": {},
        }
        with patch.object(worker.sys, "platform", "darwin"), patch.object(
                worker, "command", side_effect=[
                    subprocess.CompletedProcess([], 113, "", ""),
                    subprocess.CompletedProcess([], 0,
                                                "123 0 com.microsoft.amplifier-unified.repair.operation", ""),
                ]):
            self.assertEqual(worker._service_pid(plan), 0)
        with patch.object(worker.sys, "platform", "darwin"), patch.object(
                worker, "command", side_effect=[
                    subprocess.CompletedProcess([], 113, "", ""),
                    subprocess.CompletedProcess([], 0, "123 0 com.microsoft.amplifier-unified", ""),
                ]):
            with self.assertRaises(ValueError):
                worker._service_pid(plan)

    def test_linux_pid_lookup_uses_exact_systemd_property(self):
        plan = {"service": {"commands": [
            ["systemctl", "--user", "stop", "amplifier-unified.service"], [], None,
        ]}, "env": {"manager": "private"}}
        with patch.object(worker, "command", return_value=subprocess.CompletedProcess([], 0, "123\n", "")) as call:
            self.assertEqual(worker._service_pid(plan), 123)
        call.assert_called_once_with(
            "systemctl", "--user", "show", "amplifier-unified.service",
            "--property=MainPID", "--value", env=plan["env"], check=False)

    def test_rejects_remote_credentialed_unpinned_or_nonhealth_urls(self):
        for url, pin in [
            ("https://example.org/api/health", "a" * 64),
            ("http://localhost/api/health", None),
            ("http://127.0.0.1@evil.org/api/health", None),
            ("http://user@127.0.0.1/api/health", None),
            ("http://127.0.0.1/api/health?token=x", None),
            ("http://127.0.0.1/api/health#fragment", None),
            ("https://127.0.0.1/api/health", None),
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                worker._health_url(url, pin)

    def test_tls_pin_mismatch_never_sends_authorization(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b"wrong leaf"
        with patch.object(worker.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaises(ValueError):
                worker._health_get("https://127.0.0.1:123/api/health", "a" * 64, "private")
        connection.connect.assert_called_once()
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_tls_pin_verified_before_get_and_no_redirect_following(self):
        connection = Mock()
        connection.sock.getpeercert.return_value = b"exact leaf"
        response = connection.getresponse.return_value
        response.status = 302
        response.read.return_value = b"{}"
        with patch.object(worker.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaises(ValueError):
                worker._health_get("https://[::1]:123/api/health",
                                   hashlib.sha256(b"exact leaf").hexdigest(), "private")
        connection.request.assert_called_once_with(
            "GET", "/api/health", headers={"Authorization": "Bearer private"})


class DetachedHelperTests(unittest.TestCase):
    def test_metadata_graph_and_protocol_probe_in_stdlib_only_fixture_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            environment = root / "fixture-environment"
            # Standard-library venv only: no pip, package installs, or downloads.
            venv.EnvBuilder(with_pip=False).create(environment)
            environment.chmod(0o700)
            site = next(environment.glob("lib/python*/site-packages"))
            app = site / "amplifier_web"
            app.mkdir()
            (app / "__init__.py").write_text("")
            (app / "installation_repair.py").write_text("PROTOCOL = 1\n")
            stamp = {"schema": 1, "repository": "microsoft/amplifier-unified",
                     "version": "1.2.3", "revision": "b" * 40}
            (app / "release-source.json").write_text(json.dumps(stamp))
            for name, version in [("z-last", "2.0"), ("amplifier_unified", "1.2.3"), ("a-first", "1.0")]:
                dist = site / (name.replace("-", "_") + "-" + version + ".dist-info")
                dist.mkdir()
                (dist / "METADATA").write_text("Name: " + name + "\nVersion: " + version + "\n")
                if name == "amplifier_unified":
                    direct = {"url": "file:///private/exact-wheel.whl",
                              "archive_info": {"hashes": {"sha256": "a" * 64}}}
                    (dist / "direct_url.json").write_text(json.dumps(direct))
            python = environment / "bin/python"
            graph = subprocess.run([str(python), "-I", "-B", "-c", worker.GRAPH_PROBE],
                                   capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(graph.returncode, 0, graph.stderr)
            rows = json.loads(graph.stdout)
            self.assertEqual([row["name"] for row in rows], ["a-first", "amplifier-unified", "z-last"])
            self.assertEqual(rows[1]["stamp"], stamp)
            self.assertEqual(rows[1]["direct"], direct)
            self.assertIsNone(rows[0]["direct"])
            # Match the canonical bootstrap hash recipe, without importing app.
            metadata = site / "amplifier_unified-1.2.3.dist-info/METADATA"
            expected = hashlib.sha256(metadata.read_bytes())
            expected.update(metadata.with_name("direct_url.json").read_bytes())
            # Fixture modes follow the test host's umask; reads are tested using
            # private fixtures, not by weakening the production ownership check.
            for path in environment.rglob("*"):
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o022)
            self.assertEqual(worker.bootstrap_identity(environment),
                             {"prefix": str(environment), "digest": expected.hexdigest()})
            protocol = subprocess.run([str(python), "-I", "-B", "-c", worker.PROTOCOL_PROBE],
                                      capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(protocol.returncode, 0, protocol.stderr)
            self.assertEqual(json.loads(protocol.stdout), {"protocol": 1})
            (app / "installation_repair.py").write_text("PROTOCOL = 0\n")
            rejected = subprocess.run([str(python), "-I", "-B", "-c", worker.PROTOCOL_PROBE],
                                      capture_output=True, text=True, cwd=root, timeout=15)
            self.assertNotEqual(rejected.returncode, 0)

    def test_main_requires_both_isolation_flags_before_reading_any_operation(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        with patch.object(worker.sys, "argv", ["worker.py", "/does-not-matter"]), patch.object(
                worker.sys, "flags", SimpleNamespace(isolated=1, no_site=0)), patch.object(worker, "run") as run:
            self.assertEqual(worker.main(), 2)
            run.assert_not_called()

    def test_nonzero_child_exception_contains_classified_facts_not_raw_output(self):
        process = Mock(pid=456, returncode=1)
        process.communicate.return_value = ("sensitive raw stdout", "sensitive raw stderr")
        policy = SimpleNamespace(installer_failure_facts=lambda *parts: {"reason": "resolution-failed"},
                                 installer_failure_guidance=lambda facts: "Check the selected dependencies.")
        with patch.object(worker, "_FAILURE_POLICY", policy), patch.object(
                worker.subprocess, "Popen", return_value=process):
            with self.assertRaises(worker.CommandFailed) as raised:
                worker.command("private command", "private argument")
        self.assertEqual(raised.exception.facts, {"reason": "resolution-failed"})
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("private", repr(raised.exception.__dict__))

    def test_child_timeout_terminates_and_reaps_process_group_before_returning(self):
        process = Mock(pid=456)
        process.communicate.side_effect = subprocess.TimeoutExpired(["private", "arguments"], 10)
        process.wait.side_effect = [subprocess.TimeoutExpired([], 5), 0]
        with patch.object(worker.subprocess, "Popen", return_value=process), patch.object(
                worker.os, "killpg") as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                worker.command("private", "arguments")
        self.assertEqual(killpg.call_args_list, [
            unittest.mock.call(456, worker.signal.SIGTERM),
            unittest.mock.call(456, worker.signal.SIGKILL),
        ])
        self.assertEqual(process.wait.call_count, 2)

    def test_copied_siblings_load_under_isolated_python_without_app_or_cwd_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            for name in ("installation_repair_worker", "reset", "reset_retention", "app_release_artifacts"):
                shutil.copyfile(Path(worker.__file__).with_name(name + ".py"), folder / (name + ".py"))
                (folder / (name + ".py")).chmod(0o600)
            # A tempting cwd module must never participate in sibling loading.
            (folder / "amplifier_web.py").write_text("raise RuntimeError('cwd imported')")
            script = (
                "import importlib.util,sys; from pathlib import Path; "
                "p=Path(sys.argv[1]); "
                "s=importlib.util.spec_from_file_location('detached',p/'installation_repair_worker.py'); "
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "r,a=m.siblings();"
                "assert Path(r.__file__).parent==p;"
                "assert Path(r.reset_retention.__file__).parent==p;"
                "assert Path(a.__file__).parent==p;"
                "assert 'amplifier_web' not in sys.modules;"
                "assert all(Path(v or '.').resolve()!=p for v in sys.path)"
            )
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script, str(folder)],
                                    cwd=folder, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()