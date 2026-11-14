"""CI-only native proof that consumer setup's private Git gives the agent a shell.

Run on a Windows runner with an environment that holds the real shell tool.
The PATH is a new PC's: Windows folders only, so the runner's own Git for
Windows is hidden and System32's WSL launcher is the only other bash. No model
call, installer window, account, trust or policy change. A clean ordinary
account and the real setup file remain separate clean-room acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
UTILITIES = ("ls cat grep sed awk find sort head tail wc tr cut xargs mkdir rm cp mv chmod touch date "
             "sleep basename dirname diff patch tar gzip unzip curl ssh perl git")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inside(path, root):
    return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(str(root)) + os.sep)


async def tool_commands():
    """The worker's side: the real tool, its own resolution, ordinary commands."""
    from amplifier_module_tool_bash import BashTool
    tool = BashTool({"working_dir": os.getcwd()})
    results = {}
    for name, command in (
            ("echo", "echo amplifier-shell"),
            ("pipe", "printf 'b\\na\\n' | sort | head -n 1"),
            ("git", "git --version"),
            ("gitPath", "cygpath -w \"$(command -v git)\""),
            ("write", "echo written-by-shell > proof.txt && cat proof.txt")):
        result = await tool.execute({"command": command})
        output = result.output if isinstance(result.output, dict) else {"stderr": str(result.output)}
        results[name] = {"success": result.success, "returncode": output.get("returncode"),
                         "stdout": output.get("stdout", ""), "stderr": output.get("stderr", "")}
    executable, wsl = await tool._resolve_windows_bash()
    return {"executable": executable, "wsl": wsl, "results": results,
            "described": "WINDOWS SHELL: Git Bash" in tool.description}


def run(base, receipt):
    report = {"schema": 1, "ok": False, "stage": "preflight", "modelRequests": 0,
              "cleanAccountQualified": False, "setupFileQualified": False}
    try:
        assert sys.platform == "win32" and sys.version_info[:2] == (3, 13) and sys.maxsize > 2 ** 32
        base = Path(base).resolve()
        base.mkdir(parents=True)
        pin = load("private_git", SCRIPTS / "windows_private_git.py").PIN

        report["stage"] = "pinned download"
        archive = base / "git.tar.bz2"
        digest, size = hashlib.sha256(), 0
        with urllib.request.urlopen(pin["url"], timeout=60) as response, open(archive, "xb") as output:
            while block := response.read(1 << 20):
                size += len(block)
                assert size <= 268435456
                digest.update(block)
                output.write(block)
        assert digest.hexdigest() == pin["sha256"], "The published archive differs from the pin."

        report["stage"] = "setup's unpacker with an isolated interpreter"
        install = base / "install"
        git = install / "git"
        for folder in (git, install / "app", install / "workspace"):
            folder.mkdir(parents=True)
        (install / "consumer-install.json").write_text("{}")
        unpacked = subprocess.run(
            [sys._base_executable, "-I", "-S", "-B", str(SCRIPTS / "windows_private_git.py"),
             "--archive", str(archive), "--destination", str(git)],
            capture_output=True, text=True, timeout=600)
        assert unpacked.returncode == 0, unpacked.stderr[-2000:]
        report["git"] = json.loads(unpacked.stdout)
        assert report["git"]["ok"] is True and report["git"]["version"] == pin["version"]

        report["stage"] = "unpacked tree"
        bash = git / "bin/bash.exe"
        for name in ("cmd/git.exe", "bin/bash.exe", "bin/sh.exe", "usr/bin/bash.exe"):
            assert (git / name).read_bytes()[:2] == b"MZ", name
        link = git / "dev/fd"
        attributes = os.stat(link, follow_symlinks=False).st_file_attributes
        assert attributes & 0x4 and not attributes & 0x400, "Runtime link must be a system file, not a reparse point."
        assert os.stat(git / "ucrt64/libexec/git-core/git.exe").st_nlink == 1
        sys.path.insert(0, str(ROOT))
        from amplifier_web.windows_read_files import open_file
        # The installed app's own reader: no reparse point, hard link or alias.
        with open_file(git / "cmd/git.exe") as stream:
            assert stream.read(2) == b"MZ"

        report["stage"] = "host PATH on a PC without developer tools"
        windows = Path(os.environ["SystemRoot"])
        clean = os.pathsep.join(str(windows / part) for part in (
            "System32", "", "System32/Wbem", "System32/WindowsPowerShell/v1.0"))
        other = shutil.which("bash", path=clean)
        assert shutil.which("git", path=clean) is None and not (other and inside(other, install))
        # Recorded, not required: the runner image decides whether the hazard exists.
        report["wslLauncherOnCleanPath"] = bool(other and inside(other, windows))
        from amplifier_web import host_dependencies
        os.environ["AMPLIFIER_WEB_HOME"] = str(install / "app")
        path = host_dependencies.tool_path(clean)
        assert os.path.normcase(path.split(os.pathsep)[0]) == os.path.normcase(str(bash.parent))
        environment = {name: value for name, value in os.environ.items()
                       if name.upper() not in {"PATH", "AMPLIFIER_BASH_WINDOWS_SHELL", "MSYSTEM", "SHELL"}}
        environment["PATH"] = path
        report["pathSelectsPrivateShell"] = True

        report["stage"] = "the real shell tool in a worker environment"
        worker = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                                cwd=install / "workspace", env=environment,
                                capture_output=True, text=True, timeout=300)
        assert worker.returncode == 0, worker.stderr[-4000:]
        tool = json.loads(worker.stdout.strip().splitlines()[-1])
        results = tool["results"]
        assert os.path.normcase(tool["executable"]) == os.path.normcase(str(bash)), tool["executable"]
        assert tool["wsl"] is False and tool["described"] is True
        assert all(row["success"] and row["returncode"] == 0 for row in results.values()), results
        assert results["echo"]["stdout"].strip() == "amplifier-shell"
        assert results["pipe"]["stdout"].strip() == "a"
        assert results["git"]["stdout"].strip() == "git version " + pin["version"]
        assert inside(results["gitPath"]["stdout"].strip(), git), results["gitPath"]
        assert results["write"]["stdout"].strip() == "written-by-shell"
        assert (install / "workspace/proof.txt").read_text().strip() == "written-by-shell"
        report["tool"] = {"resolvedPrivateShell": True, "wsl": False,
                          "commands": sorted(results), "gitVersion": results["git"]["stdout"].strip()}

        report["stage"] = "runtime links and core utilities"
        def shell(command):
            done = subprocess.run([str(bash), "-c", command], cwd=install / "workspace", env=environment,
                                  capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            assert done.returncode == 0, (command, done.stdout[-1000:], done.stderr[-1000:])
            return done.stdout.strip()
        assert shell("cat <(echo fd-ok)") == "fd-ok"
        assert shell("echo stdin-ok | cat /dev/stdin") == "stdin-ok"
        assert shell("readlink /dev/fd /etc/mtab").split() == ["/proc/self/fd", "/proc/mounts"]
        assert shell(f"for t in {UTILITIES}; do command -v $t >/dev/null || echo missing:$t; done") == ""
        report["tree"] = {"runtimeLinks": True, "utilities": len(UTILITIES.split())}

        report["stage"] = "consumer setup compiles with the embedded pin and unpacker"
        builder = load("native_bootstrap_builder", SCRIPTS / "windows_bootstrap_native.py")
        artifact = {"url": "https://github.com/microsoft/amplifier-unified/releases/download/v1.2.3/"
                           "amplifier_unified-1.2.3-py3-none-any.whl", "sha256": "a" * 64, "bytes": 12345}
        plan = {"schema": 1, "kind": "windows-consumer-preview", "source": "b" * 40, "version": "1.2.3",
                "python": "3.13.17", "artifact": artifact,
                "graph": [{"name": "amplifier-unified", "version": "1.2.3",
                           "url": "https://github.com/microsoft/amplifier-unified", "revision": "b" * 40,
                           "delivery": {key: artifact[key] for key in ("url", "sha256")}}]}
        inputs = builder.prepare(SCRIPTS, base / "build", consumer=plan)
        setup = base / "build/setup.exe"
        compiler = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        compiled = subprocess.run(
            [str(compiler), *inputs["compilerOptions"], "/out:" + str(setup),
             *["/reference:" + name for name in inputs["references"]],
             *[str(base / "build" / name) for name in inputs["sources"]]],
            capture_output=True, text=True, timeout=180)
        assert compiled.returncode == 0, compiled.stdout[-4000:]
        # The entry's own self-test: no window, install, policy or network effect.
        assert subprocess.run([str(setup), "--self-test"], timeout=60).returncode == 0
        report.update(ok=True, stage="complete", setupCompiled=True)
        return 0
    finally:
        Path(receipt).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(asyncio.run(tool_commands())))
        return 0
    if args.root is None or args.receipt is None:
        parser.error("--root and --receipt are required")
    return run(args.root, args.receipt)


if __name__ == "__main__":
    raise SystemExit(main())
