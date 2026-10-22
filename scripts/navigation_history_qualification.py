"""Fixed navigation-only controls and out-of-source wheel proof on an owned CI runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/destination-history"


def run(name, argv, *, cwd=ROOT, env=None, timeout=180, expected=0):
    log = OUTPUT / f"{name}.log"
    with log.open("w") as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"Stopping exact owned qualification process group {process.pid}", flush=True)
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise
    assert code == expected, f"{name}: exit {code}, expected {expected}; see {log.name}"
    return log.read_text()


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Run only in the owned hosted qualification job"
    assert json.loads((OUTPUT / "qualification.json").read_text())["passed"], "Positive source checks must pass first"
    result = {"scope": "Navigation-specific negative controls and installed wheel", "checks": {}}
    try:
        layout = ROOT / "frontend/src/settings-layout.jsx"
        original_layout = layout.read_bytes()
        try:
            needle = b"if(entry.key===latest.current.trail.at(-1)?.key)return;"
            assert original_layout.count(needle) == 1
            layout.write_bytes(original_layout.replace(needle, b""))
            text = run("negative-settings-reopen", ["node", "--test",
                       "--test-name-pattern=discard trail reconciliation",
                       "tests/settings-drafts.test.mjs"], cwd=ROOT / "frontend", expected=1)
            assert "Reconciling the already-current parent must not publish a Settings reopen" in text
            assert "not ok" in text
            result["checks"]["settingsCloseReopen"] = "rejected at redundant Settings navigation assertion"
        finally:
            layout.write_bytes(original_layout)

        model = ROOT / "frontend/src/destination-history.js"
        original = model.read_bytes()
        try:
            needle = b"frame.host===receipt.hostInstanceId&&frame.revision>=receipt.stateRevision"
            assert original.count(needle) == 1
            model.write_bytes(original.replace(needle, b"frame.host===receipt.hostInstanceId"))
            text = run("negative-revision", ["node", "--test", "--test-name-pattern=cursor commits only",
                       "tests/destination-history.test.mjs"], cwd=ROOT / "frontend", expected=1)
            assert "old-revision" in text and "not ok" in text
            result["checks"]["revisionFence"] = "rejected at old-revision assertion"
        finally:
            model.write_bytes(original)

        entry = ROOT / "frontend/src/main.jsx"
        original = entry.read_bytes()
        static = ROOT / "amplifier_web/static"
        with tempfile.TemporaryDirectory(prefix="history-negative-") as temporary:
            backup = Path(temporary) / "static"
            shutil.copytree(static, backup)
            try:
                needle = b"destinationHistory.current.observe(next,historyOptions);"
                assert original.count(needle) == 1
                entry.write_bytes(original.replace(
                    needle, b"destinationHistory.current.observe(conversationNavigation.current.apply(next),historyOptions);"))
                run("negative-build", ["npm", "run", "build", "--prefix", "frontend"])
                plan = json.loads((OUTPUT / "plan.json").read_text())
                plan["scenarios"] = [row for row in plan["scenarios"] if row["id"] == "destination-history"]
                plan["scenarios"][0]["env"]["AMPLIFIER_TEST_OUTPUT"] = "{root}/output/destination-history/negative-browser"
                target = OUTPUT / "negative-plan.json"
                target.write_text(json.dumps(plan))
                run("negative-optimistic", [sys.executable, "scripts/browser_check_runner.py",
                    "--plan", str(target), "--output", str(OUTPUT / "negative-runner"),
                    "--budget-seconds", "90"], expected=1, timeout=120)
                report = json.loads((OUTPUT / "negative-runner/results.json").read_text())
                row, = report["scenarios"]
                text = (OUTPUT / "negative-runner" / row["log"]).read_text()
                assert report["complete"] and row["status"] == "failed" and row["exitCode"] == 1
                assert row["cleanup"] == {"remaining": [], "terminated": 0}
                assert "Unrelated authoritative state must not record an optimistic selection" in text
                result["checks"]["optimisticIsolation"] = "rejected at unchanged-trail assertion"
            finally:
                entry.write_bytes(original)
                if static.exists():
                    shutil.rmtree(static)
                shutil.copytree(backup, static)

        run("wheel-build", ["uv", "build", "--wheel", "--out-dir", str(OUTPUT / "wheel")])
        wheel, = (OUTPUT / "wheel").glob("*.whl")
        result["wheelSha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        run("wheel-install", ["uv", "pip", "install", "--python", sys.executable,
                             "--no-deps", "--reinstall", str(wheel)])
        with tempfile.TemporaryDirectory(prefix="history-installed-") as temporary:
            harness = Path(temporary)
            shutil.copytree(ROOT / "tests", harness / "tests", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "frontend/tests", harness / "frontend/tests")
            (harness / "frontend/node_modules").symlink_to(ROOT / "frontend/node_modules", target_is_directory=True)
            env = {**os.environ, "AMPLIFIER_TEST_PYTHON": sys.executable,
                   "AMPLIFIER_SHELL_NODE_MODULES": str(ROOT / "frontend/node_modules"),
                   "AMPLIFIER_TEST_OUTPUT": str(OUTPUT / "installed-browser")}
            env.pop("PYTHONPATH", None)
            origin = run("wheel-origin", [sys.executable, "-c",
                "import amplifier_web; print(amplifier_web.__file__)"], cwd=harness, env=env).strip()
            package = Path(origin).parent
            assert "site-packages" in package.parts and not package.is_relative_to(ROOT / "amplifier_web")
            assert not (harness / "amplifier_web").exists()
            with zipfile.ZipFile(wheel) as archive:
                payloads = [name for name in archive.namelist() if name.startswith("amplifier_web/") and not name.endswith("/")]
                expected = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in payloads}
            def verify():
                assert all(hashlib.sha256((package.parent / name).read_bytes()).hexdigest() == digest
                           for name, digest in expected.items())
            verify()
            run("installed-auth", [sys.executable, "-m", "pytest", "--asyncio-mode=auto", "-q",
                "tests/test_history_scope.py", "tests/test_auth.py",
                f"--junitxml={OUTPUT / 'installed-python.xml'}"], cwd=harness, env=env)
            cases = ET.parse(OUTPUT / "installed-python.xml").getroot().findall(".//testcase")
            assert cases and all(not any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) for case in cases)
            run("installed-browser", ["node", "tests/destination-history-browser.mjs"],
                cwd=harness / "frontend", env=env)
            for folder in (ROOT / "output/browser-checks/destination-history", OUTPUT / "installed-browser"):
                touch = json.loads((folder / "wide-touch-home.json").read_text())
                assert touch["passed"] and touch["input"] == {"width": 1024, "coarse": True}
                assert all(touch[key] for key in ("oldSelectorRejected", "restoredVisible",
                                                 "keyboardHome", "tapHome", "allWorkspacesLabel"))
                assert touch["pageErrors"] == [] and touch["box"]["width"] >= 44 and touch["box"]["height"] >= 44
            result["checks"]["wideTouchHome"] = "source and installed positive, old-selector rejection, keyboard and tap"
            verify()
            result["checks"]["installedWheel"] = {"payloadsVerifiedBeforeAfter": len(expected),
                "staticFiles": sum(name.startswith("amplifier_web/static/") for name in expected),
                "pythonCases": len(cases), "browser": "passed", "applicationOutsideHarness": True}
        result["passed"] = True
    except Exception as exc:
        result.update(passed=False, error=str(exc))
        raise
    finally:
        (OUTPUT / "additional-qualification.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()