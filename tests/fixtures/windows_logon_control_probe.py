"""Explicit native mechanism check; not clean install or product acceptance.

Use a create-new private root. No scheduler registration, real app, credential,
provider or existing listener is touched. Source paths are test inputs only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace


def module(path):
    selected = importlib.util.spec_from_file_location(
        "amplifier_web.windows_task_runtime", path)
    value = importlib.util.module_from_spec(selected)
    sys.modules[selected.name] = value
    selected.loader.exec_module(value)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    assert sys.platform == "win32" and sys.version_info[:2] == (3, 13)
    runtime = module(args.runtime)
    spec = SimpleNamespace(data_dir=str(args.root), registration_id="a" * 32)
    from amplifier_web.windows_files import directory
    if args.child:
        from aiohttp import web
        runner = runtime.Runner(spec)
        app = web.Application()
        async def alive(request):
            return web.json_response({"synthetic": True})
        async def cleanup(app):
            with directory(args.root, owned_root=args.root, create=False) as pinned:
                pinned.publish_private("cleaned.json", b'{"cleaned":true}')
        async def deadline(app):
            async def expire():
                await asyncio.sleep(30)
                from aiohttp.web_runner import GracefulExit
                raise GracefulExit()
            task = asyncio.create_task(expire())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        app.router.add_get("/", alive)
        app.on_cleanup.append(cleanup)
        app.cleanup_ctx.append(deadline)
        runner.attach(app)
        with runtime.PrivateLog(spec) as log:
            log.write("synthetic café / 日本語\n")
            web.run_app(app, host="127.0.0.1", port=0,
                        print=lambda text: runner.phase("listening"))
        runner.phase("stopped")
        return
    if args.root.exists():
        raise RuntimeError("Use a fresh native probe root; never replay this fixture.")
    with directory(args.root, owned_root=args.root, create=True):
        pass
    child = subprocess.Popen([sys.executable, "-I", "-B", __file__,
                              "--runtime", str(args.runtime), "--root", str(args.root), "--child"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # A dead observer cannot orphan a permanent synthetic server.
    deadline = time.monotonic() + 20
    try:
        while True:
            record = runtime.observe(spec)
            if record and record["phase"] == "listening":
                break
            assert child.poll() is None, "Synthetic host exited before ready"
            assert time.monotonic() < deadline, "Synthetic host did not become ready"
            time.sleep(.1)
        with runtime.process(record["pid"]) as actual:
            assert actual and runtime.matches(record, actual) and not actual["exited"]()
        assert child.poll() is None, "Passive observation terminated the sentinel"
        # Independent native listener table, without requesting/reading a body.
        import ctypes as C
        from ctypes import wintypes as W
        ip = C.WinDLL("iphlpapi")
        ip.GetExtendedTcpTable.argtypes = [C.c_void_p, C.POINTER(W.DWORD), W.BOOL,
                                           W.DWORD, W.DWORD, W.DWORD]
        size = W.DWORD()
        assert ip.GetExtendedTcpTable(None, C.byref(size), False, 2, 3, 0) in {0, 122}
        buffer = C.create_string_buffer(size.value)
        assert ip.GetExtendedTcpTable(buffer, C.byref(size), False, 2, 3, 0) == 0
        rows = (C.c_uint32 * 6)
        count = C.cast(buffer, C.POINTER(W.DWORD))[0]
        ports = []
        for index in range(count):
            row = rows.from_buffer(buffer, 4 + index * C.sizeof(rows))
            if row[5] == record["pid"]:
                ports.append(int.from_bytes(row[2].to_bytes(4, "little")[:2], "big"))
        assert len(ports) == 1 and ports[0] != 8942
        assert runtime.listener_owned(record, "127.0.0.1", ports[0])
        assert not runtime.listener_owned({**record, "pid": os.getpid()}, "127.0.0.1", ports[0])
        runtime.request_stop(spec, timeout=15)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0 and stderr == b""
        assert runtime.observe(spec) is None
        with directory(args.root, owned_root=args.root, create=False) as pinned:
            assert json.loads(pinned.read_private("cleaned.json", limit=1024)) == {"cleaned": True}
        assert "café / 日本語" in runtime.read_log(spec)
        result = {"syntheticNativeControl": "passed", "cleanup": True,
                  "returncode": child.returncode, "sentinelSurvivedPassiveReads": True,
                  "listenerPidMatched": True, "wrongPidRejected": True,
                  "stderrEmpty": True, "forceUsed": False,
                  "sourceSha256": hashlib.sha256(args.runtime.read_bytes()).hexdigest(),
                  "realAppOrSchedulerOrProvider": False}
        print(json.dumps(result))
    finally:
        if child.poll() is None:
            # Only request normal cleanup for this exact fixture's receipt.
            # Refusal/timeout retains the child/root for explicit reconciliation.
            runtime.request_stop(spec, timeout=15)
            child.communicate(timeout=5)


if __name__ == "__main__":
    main()