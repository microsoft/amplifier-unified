"""Manual CI discriminator for cold activation failures, NOT app qualification.

No installs, source substitutions, worker hooks or production mutations. Raw
captures remain private. Unknown owners stay alive until the five-minute CI job
terminates their owner; a cancelled/timed-out job is never diagnostic success.
Only two split-stream public-async cases run, using context-simple: exact protocol
setup alone, then the real pending reader alone. No no-stream or combined case
is repeated. Successful helper calls expose no operation cleanup object.
"""
import ast
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
REPOS = {"context-simple": "amplifier-module-context-simple"}
VARIANTS = ("protocol-only", "reader-only")
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
JOB_FIELDS = ("exception", "timedOut", "overflow", "returncode", "activeProcesses",
              "treeEmpty", "ioFinalized", "discarded", "tasksSettled", "cleanupConfirmed")
STREAM_SOURCES = {
    "configureBodySha256": "c180a6bf2f92065709ec7f3dc76b6142dcc231c5c7d5fd159c7ff6435130d3a1",
    "readerSourceSha256": "9912b288c98166658c6c3ead02fbd6d8dc7db39e3ad518733be461fa165851bc",
    "protocolSourceSha256": "0316c164a342ab4d7fc58ce4d62070293ce09fb5c35fa169a37ccc37c1390d06",
}


def worker_streams(variant):
    """Execute the unmodified checked-out function/module, no full app imports.

    LF normalization is Python's source newline normalization, so checkout CRLF
    does not change the attested function AST or actual input-reader code.
    """
    assert variant in VARIANTS
    app = Path(__file__).resolve().parents[2] / "amplifier_web"
    worker = ast.parse((app / "runtime_worker.py").read_bytes())
    functions = [node for node in worker.body
                 if isinstance(node, ast.FunctionDef) and node.name == "configure_protocol"]
    assert len(functions) == 1
    function = functions[0]
    assert hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest() == STREAM_SOURCES["configureBodySha256"]
    inputs = []
    for filename, key in (("worker_input.py", "readerSourceSha256"),
                          ("runtime_protocol.py", "protocolSourceSha256")):
        raw = (app / filename).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == STREAM_SOURCES[key]
        inputs.append((filename, raw))
    # All source identities are attested; only the chosen variant executes.
    protocol = reader = None
    try:
        if variant == "protocol-only":
            scope = {"os": os, "sys": sys, "_PROTOCOL": None}
            # Exact AST, no rewritten calls, globals or body; no input reader.
            exec(compile(ast.Module(body=[function], type_ignores=[]),
                         str(app / "runtime_worker.py"), "exec"), scope)
            scope["configure_protocol"]()
            protocol = scope["_PROTOCOL"]
        else:
            modules = []
            for filename, raw in inputs:
                spec = importlib.util.spec_from_file_location("diagnostic_" + filename[:-3], app / filename)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                exec(compile(raw, str(app / filename), "exec"), module.__dict__)
                modules.append(module)
            reader = modules[0].ThreadedPipeReader(sys.stdin, modules[1].MAX_MESSAGE_BYTES)
    except BaseException:
        if protocol is not None:
            protocol.close()
        raise
    return protocol, reader


def reader_pending(reader, request):
    # Dequeued request + live daemon + unresolved delivery is direct source-state
    # evidence, not an assertion that a kernel ReadFile stack was inspected.
    return (not request.done() and reader._pending is not None and not reader._pending.done()
            and reader._requests.empty() and reader._thread.is_alive()
            and not reader._closed.is_set() and not reader._eof)


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


async def child(root, repo, variant):
    protocol, reader = worker_streams(variant)
    request = None
    try:
        assert (protocol is not None) == (variant == "protocol-only")
        assert (reader is not None) == (variant == "reader-only")
        if reader is not None:
            request = asyncio.create_task(reader.readline())
            # No new deadline: the unchanged outer owned operation bounds setup.
            while not reader_pending(reader, request):
                if request.done():
                    request.result()
                    raise AssertionError("stdin delivery was not pending")
                assert reader._thread.is_alive() and not reader._closed.is_set()
                await asyncio.sleep(.01)
        facts = {**STREAM_SOURCES, "variant": variant,
                 "protocolConfigured": protocol is not None, "readerActive": reader is not None,
                 "pendingReadDequeued": reader is not None, "pendingAfterHelper": False}
        output = protocol if protocol is not None else sys.stdout.buffer
        return await helper_child(root, repo, output, reader, request, facts)
    finally:
        # Existing cancellation semantics stop delivery, NOT the OS read.
        # Do not join the daemon or close its buffered descriptor on the loop.
        try:
            if reader is not None:
                reader.close()
            if request is not None:
                request.cancel()
                try:
                    await request
                except asyncio.CancelledError:
                    pass
        finally:
            if protocol is not None:
                protocol.close()


async def helper_child(root, repo, protocol, reader, request, stream_facts):
    assert Path.cwd() == root
    with directory(root, owned_root=root) as pinned:
        raw = pinned.read_private("helper.py", limit=32768)
    assert hashlib.sha256(raw).hexdigest() == SHA
    spec = importlib.util.spec_from_file_location("verified_shared_git", root / "helper.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(raw, str(root / "helper.py"), "exec"), module.__dict__)
    output, error, code, error_cleanup = "", None, None, None
    try:
        output, code = await module.check_output(argv(repo), timeout=35), 0
    except BaseException as exc:
        error = "HelperCleanupError" if type(exc) is module._CleanupError else failure(exc)
        facts = getattr(exc, "cleanup", None)
        if type(facts) is dict:
            error_cleanup = cleanup(facts)  # Only exposed failure/cancel facts.
        if type(exc) is subprocess.CalledProcessError:
            code, output = exc.returncode, exc.output or ""
    raw = output.encode("utf-8" if sys.flags.utf8_mode else locale.getencoding())
    try:
        # Exact configure_protocol's descriptor only in protocol-only. Reader-only
        # writes ordinary stdout unchanged, so each variant isolates one delta.
        protocol.write(raw)
        protocol.flush()
        stream_facts["pendingAfterHelper"] = reader is not None and reader_pending(reader, request)
        save(root, stream_facts["variant"] + "-" + repo + "-helper.json", {"entry": "public-check-output-async", "exception": error,
            "returncode": code, "outputComplete": error in {None, "CalledProcessError"},
            "pendingEmpty": not module._PENDING, "errorCleanup": error_cleanup,
            "streamContext": stream_facts})
    finally:
        while module._PENDING:  # Outer Job owns finite termination of this child.
            time.sleep(1)
    return int(error is not None)


def helper_result(root, label):
    try:
        with directory(root, owned_root=root) as pinned:
            raw = pinned.read_private(label + "-helper.json", limit=16384)
        value = json.loads(raw)
        assert type(value) is dict and set(value) == {
            "entry", "exception", "returncode", "outputComplete", "pendingEmpty", "errorCleanup", "streamContext"}
        validate_stream_context(value["streamContext"])
        assert value["entry"] == "public-check-output-async"
        assert value["exception"] is None or type(value["exception"]) is str and value["exception"] in ERRORS
        assert value["returncode"] is None or type(value["returncode"]) is int and -(2**31) <= value["returncode"] < 2**32
        assert type(value["outputComplete"]) is bool
        assert type(value["pendingEmpty"]) is bool
        if value["errorCleanup"] is not None:
            assert value["exception"] is not None
            value["errorCleanup"] = cleanup(value["errorCleanup"])
        return value
    except BaseException:
        return None


def nested_result(root, repo, variant):
    try:
        with directory(root, owned_root=root) as pinned:
            raw = pinned.read_private("inner-" + variant + "-" + repo + "-completed.json", limit=16384)
        value = json.loads(raw)
        validate_case(value)
        assert value["repo"] == repo and value["route"] == variant and value["innerJob"] is None
        return {key: value[key] for key in JOB_FIELDS}
    except BaseException:
        return None


async def nested(root, repo, variant):
    # The unchanged leaf gets exactly this child's inherited env/cwd. Outer
    # stdin remains open; the extra owned pipe is likewise left open/unwritten.
    try:
        row = await compare(root, repo, variant, dict(os.environ), None, inner=True)
        with directory(root, owned_root=root) as pinned:
            output = pinned.read_private("inner-" + variant + "-" + repo + "-stdout.bin", limit=LIMIT)
            errors = pinned.read_private("inner-" + variant + "-" + repo + "-stderr.bin", limit=LIMIT)
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(errors)
        sys.stderr.buffer.flush()
        return int(not successful(row, inner=True))
    finally:
        if RETAINED:
            # The retained outer Job's unchanged 50-second deadline owns this
            # process. Never exit/drop an unknown inner owner before that.
            await asyncio.Event().wait()


async def compare(root, repo, route, env, git, *, inner=False):
    assert route in VARIANTS  # No repeat of accepted no-stream/failing combined.
    label = ("inner-" if inner else "") + route + "-" + repo
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
    command = [
        sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
        "--child" if inner else "--nested", str(root), repo, route]
    cancelled = None
    try:
        async with asyncio.timeout(50):
            owner = await WindowsOwnedProcess.start(command, cwd=str(root), env=env,
                trusted_python=sys._base_executable, gate_timeout=10, cleanup_timeout=5)
            OWNERS.append(owner)
            # Preserve the accepted child's open/unwritten stdin and ownership.
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
        "helper": helper_result(root, label.removeprefix("inner-")) if confirmed and settled else None,
        "innerJob": nested_result(root, repo, route) if not inner and confirmed and settled else None}
    for name, raw in captures.items():
        private(root, label + "-" + name + ".bin", bytes(raw))
    save(root, label + "-completed.json", row)
    if cancelled is not None:
        raise cancelled
    return row


def job_success(row):
    return (row["exception"] is None and not row["timedOut"] and not row["overflow"]
        and row["returncode"] == 0 and row["cleanupConfirmed"] and row["activeProcesses"] == 0
        and row["treeEmpty"] and row["ioFinalized"] and row["tasksSettled"] and not row["discarded"])


def successful(row, *, inner=False):
    helper = row["helper"]
    return (job_success(row) and row["mainRef"] and row["refSyntax"]
        and row["route"] in VARIANTS
        and helper is not None and helper["entry"] == "public-check-output-async"
        and helper["exception"] is None and helper["returncode"] == 0 and helper["outputComplete"]
        and helper["pendingEmpty"] and helper["errorCleanup"] is None
        and helper["streamContext"]["variant"] == row["route"]
        and helper["streamContext"]["protocolConfigured"] == (row["route"] == "protocol-only")
        and all(helper["streamContext"][key] == (row["route"] == "reader-only")
                for key in ("readerActive", "pendingReadDequeued", "pendingAfterHelper"))
        and (row["innerJob"] is None if inner else row["innerJob"] is not None and job_success(row["innerJob"])))


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
    for variant in VARIANTS:
        report["cases"].append(await compare(root, "context-simple", variant, env, git))
        if RETAINED:
            report["stage"] = "retained-unknown"
            return
    report["stage"] = "completed"
    report["status"] = "PASS" if all(successful(row) for row in report["cases"]) else "FAIL"


def validate_job(row):
    assert type(row) is dict and set(row) == set(JOB_FIELDS)
    assert row["exception"] is None or type(row["exception"]) is str and row["exception"] in ERRORS
    for key in ("timedOut", "overflow", "treeEmpty", "ioFinalized", "discarded", "tasksSettled", "cleanupConfirmed"):
        assert type(row[key]) is bool
    for key in ("returncode", "activeProcesses"):
        assert row[key] is None or type(row[key]) is int and -(2**31) <= row[key] < 2**32


def validate_case(row):
    assert type(row) is dict and set(row) == {
        "repo", "route", "seconds", "stage", *JOB_FIELDS,
        "stdoutBytes", "stderrBytes", "mainRef", "refSyntax", "helper", "innerJob"}
    assert row["repo"] in REPOS and row["route"] in VARIANTS
    assert type(row["seconds"]) in (float, int) and 0 <= row["seconds"] <= 300
    assert row["stage"] == "completed"
    validate_job({key: row[key] for key in JOB_FIELDS})
    for key in ("mainRef", "refSyntax"):
        assert type(row[key]) is bool
    for key in ("stdoutBytes", "stderrBytes"):
        assert type(row[key]) is int and 0 <= row[key] <= LIMIT
    if row["innerJob"] is not None:
        validate_job(row["innerJob"])
    if row["helper"] is not None:
        h = row["helper"]
        assert type(h) is dict and set(h) == {
            "entry", "exception", "returncode", "outputComplete", "pendingEmpty", "errorCleanup", "streamContext"}
        validate_stream_context(h["streamContext"])
        assert h["entry"] == "public-check-output-async" and type(h["pendingEmpty"]) is bool
        assert h["exception"] in ERRORS and type(h["outputComplete"]) is bool
        assert h["returncode"] is None or type(h["returncode"]) is int and -(2**31) <= h["returncode"] < 2**32
        if h["errorCleanup"] is not None:
            assert h["exception"] is not None
            c = h["errorCleanup"]
            assert type(c) is dict and set(c) == {*BOOLS, "errors"}
            assert all(c[key] is None or type(c[key]) is bool for key in BOOLS)
            assert type(c["errors"]) is list and len(c["errors"]) <= 8
            for e in c["errors"]:
                assert set(e) == {"stage", "winerror"} and e["stage"] in STAGES
                assert e["winerror"] is None or type(e["winerror"]) is int and -(2**31) <= e["winerror"] < 2**32


def validate_stream_context(value):
    assert type(value) is dict and set(value) == {
        *STREAM_SOURCES, "variant", "protocolConfigured", "readerActive", "pendingReadDequeued", "pendingAfterHelper"}
    assert value["variant"] in VARIANTS
    assert all(value[key] == digest for key, digest in STREAM_SOURCES.items())
    assert all(type(value[key]) is bool for key in
               ("protocolConfigured", "readerActive", "pendingReadDequeued", "pendingAfterHelper"))


def validate(report):
    assert set(report) == {"schema", "qualification", "diagnosticOnly", "status", "stage", "python", "windowsBuild",
                           "helperSha256", "gitWhichSha256", "cases", "exception"}
    assert report["schema"] == 5 and report["qualification"] == "nested-split-stream-public-check-output-async"
    assert report["diagnosticOnly"] is True
    assert report["status"] in {"PASS", "FAIL"}
    assert report["stage"] in {"preflight", "helper-download", "comparison", "completed", "retained-unknown"}
    assert re.fullmatch(r"3\.13\.[0-9]{1,4}", report["python"])
    assert type(report["windowsBuild"]) is int and 0 < report["windowsBuild"] < 2**32
    assert report["helperSha256"] == SHA
    assert report["gitWhichSha256"] is None or re.fullmatch("[a-f0-9]{64}", report["gitWhichSha256"])
    assert report["exception"] is None or type(report["exception"]) is str and report["exception"] in ERRORS
    assert type(report["cases"]) is list and len(report["cases"]) <= 2
    for index, row in enumerate(report["cases"]):
        validate_case(row)
        assert row["repo"] == "context-simple" and row["route"] == VARIANTS[index]
    if report["status"] == "PASS":
        assert report["stage"] == "completed" and len(report["cases"]) == 2
        assert report["exception"] is None and all(successful(row) for row in report["cases"])


if __name__ == "__main__":
    # Explicit checkout import under -I -S; this is NOT installed-source evidence.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from amplifier_web.windows_files import directory
    from amplifier_web.windows_jobs import CleanupError, WindowsOwnedProcess
    assert os.name == "nt" and sys.implementation.name == "cpython"
    assert sys.version_info[:2] == (3, 13) and struct.calcsize("P") == 8
    if len(sys.argv) == 5 and sys.argv[1] == "--child" and sys.argv[3] in REPOS and sys.argv[4] in VARIANTS:
        raise SystemExit(asyncio.run(child(Path(sys.argv[2]), sys.argv[3], sys.argv[4])))
    if len(sys.argv) == 5 and sys.argv[1] == "--nested" and sys.argv[3] in REPOS and sys.argv[4] in VARIANTS:
        raise SystemExit(asyncio.run(nested(Path(sys.argv[2]), sys.argv[3], sys.argv[4])))
    assert len(sys.argv) == 1 and os.environ.get("GITHUB_ACTIONS") == "true"
    report = {"schema": 5, "qualification": "nested-split-stream-public-check-output-async",
        "diagnosticOnly": True, "status": "FAIL", "stage": "preflight",
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