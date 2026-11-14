"""CI-only native checks of consumer setup's folder, removal and stop messages.

Compiles the actual setup sources with tests/windows/native_setup.Tests.cs on a
Windows runner and runs that entry. It shows no setup window and installs
nothing. The real setup file on a clean ordinary account is separate
clean-room acceptance.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run(base, receipt):
    report = {"schema": 1, "ok": False, "stage": "build inputs", "setupFileQualified": False,
              "cleanAccountQualified": False}
    try:
        assert sys.platform == "win32"
        base = Path(base).resolve()
        spec = importlib.util.spec_from_file_location("native_bootstrap_builder", ROOT / "scripts/windows_bootstrap_native.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        inputs = builder.prepare(ROOT / "scripts", base / "build")
        report["stage"] = "compile"
        program = base / "build/native-setup-tests.exe"
        compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        compiled = subprocess.run(
            [str(compiler), *inputs["compilerOptions"], "/main:UnifiedBootstrap.NativeSetupTests",
             "/out:" + str(program), *["/reference:" + name for name in inputs["references"]],
             *[str(base / "build" / name) for name in inputs["sources"]],
             str(ROOT / "tests/windows/native_setup.Tests.cs")],
            capture_output=True, text=True, timeout=180)
        assert compiled.returncode == 0, compiled.stdout[-4000:]
        report["stage"] = "native checks"
        tested = subprocess.run([str(program)], capture_output=True, text=True, timeout=120)
        actual = json.loads(tested.stdout.strip().splitlines()[-1])
        report["native"] = actual
        assert tested.returncode == 0 and actual["ok"] is True, tested.stderr[-2000:]
        assert actual["assertions"] >= 50
        assert actual["elevation"] in {"none", "run-as-administrator", "always-administrator", "administrator"}
        # The runner has no Amplifier task. "unavailable" here would mean that
        # setup stops on every ordinary PC, so only a proven "absent" passes.
        assert actual["signInTask"] == "absent"
        assert actual["ordinaryFolder"] in {"foreign", "unfinished"}
        report.update(ok=True, stage="complete")
        return 0
    finally:
        Path(receipt).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    return run(args.root, args.receipt)


if __name__ == "__main__":
    raise SystemExit(main())
