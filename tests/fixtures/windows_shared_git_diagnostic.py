"""Manual CI discriminator for cold activation failures, NOT app qualification.

No installs, source substitutions, worker hooks or production mutations. Raw
captures remain private. Unknown owners stay alive until the five-minute CI job
terminates their owner; a cancelled/timed-out job is never diagnostic success.
"""
import asyncio
import hashlib
import importlib.util
import json
import locale
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

SHA = "11f47553e965956a99c1fed0bd1305aa8f75163206f6c2c51c75b97ad1e32dc4"
URL = ("https://raw.githubusercontent.com/microsoft/amplifier-foundation/"
       "c716df3cea84b2539aabdfea58a69ae676a1e853/amplifier_foundation/sources/_windows_git.py")
REPOS = {"context-simple": "amplifier-module-context-simple",
         "context-intelligence": "amplifier-bundle-context-intelligence"}
ERRORS = {None, "other", "HelperCleanupError", "CleanupError", "TimeoutExpired",
          "CalledProcessError", "TimeoutError", "OSError", "PermissionError",
          "FileNotFoundError", "AssertionError", "ValueError", "TypeError",
          "RuntimeError", "CancelledError"}
BOOLS = ("assigned", "membership_verified", "termination_requested", "process_exited",
         "tree_empty", "io_finalized", "handles_closed", "owner_retained")
STAGES = {"terminate", "cancel_read", "query_job", "wait_process", "finish_read",
          "wait_io", "close_handle", "other"}
LIMIT = 256 * 1024
OWNERS, RETAINED = [], []


def private(root, name, raw):
    with directory(root, owned_root=root) as pinned:
        pinned.publish_private(name, raw)  # Exclusive, never replace old evidence.


def save(root, name, value):
    private(root, name, json.dumps(value, sort_keys=True).encode("utf-8"))


def failure(error):
    for kind in (subprocess.TimeoutExpired, subprocess.CalledProcessError,
                 TimeoutError, OSError, PermissionError, FileNotFoundError,
                 AssertionError, ValueError, TypeError, RuntimeError, asyncio.CancelledError):
        if type(error) is kind:
            return kind.__name__
    return "CleanupError" if type(error) is CleanupError else "other"


def cleanup(value):
    # Project, never forward arbitrary helper exception metadata.
    result = {key: value.get(key) if type(value.get(key)) is bool else None for key in BOOLS}
    result["errors"] = []
    for row in value.get("errors", [])[:8]:
        stage, number = row.get("stage"), row.get("winerror")
        result["errors"].append({"stage": stage if type(stage) is str and stage in STAGES else "other",
            "winerror": number if type(number) is int and -(2**31) <= number < 2**32 else None})
    return result


def argv(repo):
    return ["git", "ls-remote", "https://github.com/microsoft/" + REPOS[repo],
            "refs/heads/main", "refs/tags/main", "refs/tags/main^{}"]


def child(root, repo):
    assert Path.cwd() == root
    with directory(root, owned_root=root) as pinned:
        raw = pinned.read_private("helper.py", limit=32768)
    assert hashlib.sha256(raw).hexdigest() == SHA
    spec = importlib.util.spec_from_file_location("verified_shared_git", root / "helper.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(raw, str(root / "helper.py"), "exec"), module.__dict__)
    operation = module._Operation(argv(repo), 35)
    output, error, code = "", None, None
    try:
        output, code = operation.run(), 0
    except BaseException as exc:
        error = "HelperCleanupError" if type(exc) is module._CleanupError else failure(exc)
        if type(exc) is subprocess.CalledProcessError:
            code, output = exc.returncode, exc.output or ""
    raw = output.encode("utf-8" if sys.flags.utf8_mode else locale.getencoding())
    try:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        save(root, "B-" + repo + "-helper.json", {"exception": error,
            "returncode": code, "outputComplete": error in {None, "CalledProcessError"},
            "cleanup": cleanup(operation.cleanup)})
    finally:
        while module._PENDING:  # Outer Job owns finite termination of this child.
            time.sleep(1)
    return int(error is not None)


def helper_result(root, label):
    try:
        with directory(root, owned_root=root) as pinned:
            raw = pinned.read_private(label + "-helper.json", limit=16384)
        value = json.loads(raw)
        assert type(value) is dict and set(value) == {"exception", "returncode", "outputComplete", "cleanup"}
        assert value["exception"] is None or type(value["exception"]) is str and value["exception"] in ERRORS
        assert value["returncode"] is None or type(value["returncode"]) is int and -(2**31) <= value["returncode"] < 2**32
        assert type(value["outputComplete"]) is bool
        value["cleanup"] = cleanup(value["cleanup"])
        return value
    except BaseException:
        return None


async def compare(root, repo, route, env, git):
    label = route + "-" + repo
    save(root, label + "-starting.json", {"stage": "starting"})
    owner = result = error = None
    tasks, captures = [], {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = overflow = confirmed = settled = False
    started = time.monotonic()
    async def drain(name):
        nonlocal overflow
        while block := await getattr(owner, name).read(16384):
            room = LIMIT - len(captures[name])
            captures[name].extend(block[:room])
            if len(block) > room:
                overflow = True
                raise ValueError("capture limit")
    command = [git, *argv(repo)[1:]] if route == "A" else [
        sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()), "--child", str(root), repo]
    cancelled = None
    try:
        async with asyncio.timeout(50):
            owner = await WindowsOwnedProcess.start(command, cwd=str(root), env=env,
                trusted_python=sys._base_executable, gate_timeout=10, cleanup_timeout=5)
            OWNERS.append(owner)
            # Both routes keep stdin open/unwritten until natural process exit.
            tasks = [asyncio.create_task(owner.wait()),
                     *(asyncio.create_task(drain(name)) for name in captures)]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
            assert not pending
            result = tasks[0].result()
    except BaseException as exc:
        error, timed_out = failure(exc), type(exc) is TimeoutError
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            cancelled = exc
        if type(exc) is CleanupError:
            owner = exc.owner
            if owner not in OWNERS:
                OWNERS.append(owner)
    finally:
        if owner is not None:
            try:
                if owner.state != "cleanup_failed":  # Never retry failed finalization.
                    result = await owner.close()  # Existing primitive's bounded native close.
                confirmed = result is not None and result.active_processes == 0 and owner.tree_empty and owner.io_finalized
            except BaseException as exc:
                error = error or failure(exc)
        for task in tasks:
            if not task.done():
                task.cancel()
        done, pending = await asyncio.wait(tasks, timeout=6) if tasks else (set(), set())
        for task in done:
            if not task.cancelled():
                task.exception()
        settled = not pending
        if not confirmed or not settled:
            RETAINED.append((owner, tasks, root))
    row = {"repo": repo, "route": route, "seconds": round(time.monotonic() - started, 3),
        "stage": "completed", "exception": error, "timedOut": timed_out, "overflow": overflow,
        "stdoutBytes": len(captures["stdout"]), "stderrBytes": len(captures["stderr"]),
        "mainRef": any(re.fullmatch(rb"[a-f0-9]{40,64}\s+refs/heads/main", line)
                       for line in bytes(captures["stdout"]).splitlines()),
        "refSyntax": bool(captures["stdout"]) and all(re.fullmatch(
            rb"[a-f0-9]{40,64}\s+refs/(?:heads/main|tags/main(?:\^\{\})?)", line)
            for line in bytes(captures["stdout"]).splitlines()),
        "returncode": result.payload_returncode if result is not None else None,
        "activeProcesses": result.active_processes if result is not None else None,
        "treeEmpty": bool(owner is not None and owner.tree_empty),
        "ioFinalized": bool(owner is not None and owner.io_finalized),
        "discarded": bool(owner is not None and owner.output_discarded),
        "tasksSettled": settled, "cleanupConfirmed": bool(confirmed),
        "helper": helper_result(root, label) if route == "B" and confirmed and settled else None}
    for name, raw in captures.items():
        private(root, label + "-" + name + ".bin", bytes(raw))
    save(root, label + "-completed.json", row)
    if cancelled is not None:
        raise cancelled
    return row


def successful(row):
    inner = row["helper"]
    return (row["exception"] is None and not row["timedOut"] and not row["overflow"]
        and row["mainRef"] and row["refSyntax"] and row["returncode"] == 0
        and row["cleanupConfirmed"] and row["activeProcesses"] == 0
        and row["treeEmpty"] and row["ioFinalized"] and row["tasksSettled"] and not row["discarded"]
        and (row["route"] == "A" or inner is not None and inner["exception"] is None
             and inner["returncode"] == 0 and inner["outputComplete"]
             and all(inner["cleanup"][key] is True for key in
                     ("process_exited", "tree_empty", "io_finalized", "handles_closed"))
             and inner["cleanup"]["owner_retained"] is False and not inner["cleanup"]["errors"]))


async def experiment(report):
    report["stage"] = "helper-download"
    # Only immutable public source, hard size/hash bound; no Git/pip/uv install.
    with urllib.request.urlopen(URL, timeout=15) as response:
        raw = response.read(32769)
    assert len(raw) <= 32768 and hashlib.sha256(raw).hexdigest() == SHA
    original = Path(tempfile.gettempdir())
    canonical = original.resolve(strict=True)
    before, after = original.stat(), canonical.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    for part in (original, *original.parents, canonical, *canonical.parents):
        info = part.lstat()
        assert stat.S_ISDIR(info.st_mode) and not info.st_file_attributes & 0x400
    root = canonical / ("git-diagnostic-" + uuid.uuid4().hex[:12])
    assert not root.exists()
    with directory(root, owned_root=root, create=True):
        pass
    private(root, "helper.py", raw)
    user = root / "user"
    for path in (user, user / "roaming", user / "local", user / "cache"):
        with directory(path, owned_root=root, create=True):
            pass
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(HOME=str(user), USERPROFILE=str(user), APPDATA=str(user / "roaming"),
        LOCALAPPDATA=str(user / "local"), XDG_CONFIG_HOME=str(user), XDG_CACHE_HOME=str(user / "cache"),
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    git = shutil.which("git")
    assert git and Path(git).is_absolute() and Path(git).is_file()
    report["gitWhichSha256"] = hashlib.sha256(Path(git).read_bytes()).hexdigest()
    # This hash is which('git'), NOT proof of bare CreateProcess executable identity.
    report["stage"] = "comparison"
    for repo in REPOS:
        for route in ("A", "B"):
            report["cases"].append(await compare(root, repo, route, env, git))
            if RETAINED:
                report["stage"] = "retained-unknown"
                return
    report["stage"] = "completed"
    report["status"] = "PASS" if all(successful(row) for row in report["cases"]) else "FAIL"


def validate(report):
    assert set(report) == {"schema", "diagnosticOnly", "status", "stage", "python", "windowsBuild",
                           "helperSha256", "gitWhichSha256", "cases", "exception"}
    assert report["schema"] == 1 and report["diagnosticOnly"] is True
    assert report["status"] in {"PASS", "FAIL"}
    assert report["stage"] in {"preflight", "helper-download", "comparison", "completed", "retained-unknown"}
    assert re.fullmatch(r"3\.13\.[0-9]{1,4}", report["python"])
    assert type(report["windowsBuild"]) is int and 0 < report["windowsBuild"] < 2**32
    assert report["helperSha256"] == SHA
    assert report["gitWhichSha256"] is None or re.fullmatch("[a-f0-9]{64}", report["gitWhichSha256"])
    assert report["exception"] is None or type(report["exception"]) is str and report["exception"] in ERRORS
    assert type(report["cases"]) is list and len(report["cases"]) <= 4
    for index, row in enumerate(report["cases"]):
        assert list(REPOS)[index // 2] == row["repo"] and row["route"] == ("A", "B")[index % 2]
        assert set(row) == {"repo", "route", "seconds", "stage", "exception", "timedOut", "overflow",
            "stdoutBytes", "stderrBytes", "mainRef", "refSyntax", "returncode", "activeProcesses",
            "treeEmpty", "ioFinalized", "discarded", "tasksSettled", "cleanupConfirmed", "helper"}
        assert type(row["seconds"]) in (float, int) and 0 <= row["seconds"] <= 300
        assert row["stage"] == "completed" and row["exception"] in ERRORS
        for key in ("timedOut", "overflow", "mainRef", "refSyntax", "treeEmpty", "ioFinalized",
                    "discarded", "tasksSettled", "cleanupConfirmed"):
            assert type(row[key]) is bool
        for key in ("stdoutBytes", "stderrBytes"):
            assert type(row[key]) is int and 0 <= row[key] <= LIMIT
        for key in ("returncode", "activeProcesses"):
            assert row[key] is None or type(row[key]) is int and -(2**31) <= row[key] < 2**32
        if row["helper"] is not None:
            h = row["helper"]
            assert set(h) == {"exception", "returncode", "outputComplete", "cleanup"}
            assert h["exception"] in ERRORS and type(h["outputComplete"]) is bool
            assert h["returncode"] is None or type(h["returncode"]) is int and -(2**31) <= h["returncode"] < 2**32
            assert set(h["cleanup"]) == {*BOOLS, "errors"}
            assert all(h["cleanup"][key] is None or type(h["cleanup"][key]) is bool for key in BOOLS)
            assert type(h["cleanup"]["errors"]) is list and len(h["cleanup"]["errors"]) <= 8
            for e in h["cleanup"]["errors"]:
                assert set(e) == {"stage", "winerror"} and e["stage"] in STAGES
                assert e["winerror"] is None or type(e["winerror"]) is int and -(2**31) <= e["winerror"] < 2**32
    if report["status"] == "PASS":
        assert report["stage"] == "completed" and len(report["cases"]) == 4
        assert report["exception"] is None and all(successful(row) for row in report["cases"])


if __name__ == "__main__":
    # Explicit checkout import under -I -S; this is NOT installed-source evidence.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from amplifier_web.windows_files import directory
    from amplifier_web.windows_jobs import CleanupError, WindowsOwnedProcess
    assert os.name == "nt" and sys.implementation.name == "cpython"
    assert sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
    if len(sys.argv) == 4 and sys.argv[1] == "--child" and sys.argv[3] in REPOS:
        raise SystemExit(child(Path(sys.argv[2]), sys.argv[3]))
    assert len(sys.argv) == 1 and os.environ.get("GITHUB_ACTIONS") == "true"
    report = {"schema": 1, "diagnosticOnly": True, "status": "FAIL", "stage": "preflight",
        "python": ".".join(map(str, sys.version_info[:3])), "windowsBuild": sys.getwindowsversion().build,
        "helperSha256": SHA, "gitWhichSha256": None, "cases": [], "exception": None}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        try:
            loop.run_until_complete(experiment(report))
        except BaseException as error:
            report.update(status="FAIL", exception=failure(error))
            if RETAINED:
                report["stage"] = "retained-unknown"
        validate(report)
        # The workflow uploads exactly this validated file, never private scratch.
        destination = Path(__file__).resolve().parents[2] / "windows-shared-git-diagnostic-result.json"
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True)
        if RETAINED:
            loop.run_forever()  # Retain lifelines; CI job owner terminates at 5 min.
        raise SystemExit(0 if report["status"] == "PASS" else 1)
    except BaseException:
        if RETAINED:
            loop.run_forever()  # Even an export failure cannot drop native custody.
        raise
    finally:
        if not RETAINED:
            loop.close()