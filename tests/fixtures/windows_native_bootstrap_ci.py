"""Compile/test the actual native bootstrap on a Windows build runner, offline.

No app install, developer dependency resolution, GUI, policy or service change.
Keep this separate from clean ordinary-account and public-download acceptance.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Native bootstrap fixture requires Windows.")
    output = Path(os.environ["RUNNER_TEMP"]) / "unified-native-bootstrap-offline"
    if output.exists():
        raise RuntimeError("Native fixture output already exists; preserve it rather than replaying.")
    spec = importlib.util.spec_from_file_location("native_bootstrap_builder", ROOT / "scripts/windows_bootstrap_native.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    report = {"schema": 1, "ok": False, "stage": "build-inputs", "publicDownloadQualified": False,
              "cleanAccountQualified": False, "nativeInstallationAttempted": False}
    inputs = builder.prepare(ROOT / "scripts", output)
    result_path = output / "result.json"
    try:
        test = ROOT / "tests/windows/native_bootstrap.Tests.cs"
        shutil.copyfile(test, output / "NativeBootstrap.Tests.cs")
        report["sources"] = {**inputs["sources"], "NativeBootstrap.Tests.cs": {
            "sha256": hashlib.sha256(test.read_bytes()).hexdigest(), "bytes": test.stat().st_size}}
        compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        references = ["/reference:" + name for name in inputs["references"]]
        sources = [str(output / name) for name in inputs["sources"]]
        for entry, filename, files in (
            (inputs["entry"], "unified-windows-prototype.exe", sources),
            ("UnifiedBootstrap.NativeBootstrapTests", "native-bootstrap-tests.exe", sources + [str(output / "NativeBootstrap.Tests.cs")]),
        ):
            report["stage"] = "compile"
            result = subprocess.run([str(compiler), *inputs["compilerOptions"], "/main:" + entry,
                "/out:" + str(output / filename), *references, *files], cwd=output,
                capture_output=True, timeout=120, check=False)
            (output / (filename + ".compiler-private.txt")).write_bytes(result.stdout + result.stderr)
            if result.returncode != 0:
                raise RuntimeError("Native bootstrap compilation failed.")
        report["stage"] = "actual-native-tests"
        tested = subprocess.run([str(output / "native-bootstrap-tests.exe")], cwd=output,
            capture_output=True, timeout=60, check=False)
        (output / "tests-private.stderr.txt").write_bytes(tested.stderr)
        if tested.returncode != 0 or len(tested.stdout) > 4096:
            raise RuntimeError("Actual native bootstrap tests failed.")
        actual = json.loads(tested.stdout.decode("utf-8-sig"))
        if (set(actual) != {"ok", "assertions", "actualCSharpParserAndWindowsArgv", "networkOrInstallEffects"}
                or actual["ok"] is not True or actual["assertions"] != 64
                or actual["actualCSharpParserAndWindowsArgv"] is not True
                or actual["networkOrInstallEffects"] is not False):
            raise RuntimeError("Incomplete native bootstrap test receipt.")
        report.update(ok=True, stage="complete", actualTests=actual,
            compiledExeSha256=hashlib.sha256((output / "unified-windows-prototype.exe").read_bytes()).hexdigest())
        return 0
    except Exception:
        # Raw compiler/test output stays out of the public artifact. No native
        # installation is attempted by this fixture, even on an elevated CI host.
        report["ok"] = False
        return 1
    finally:
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "sources"}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
