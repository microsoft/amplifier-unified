"""Actual stdio ACP client -> authenticated host -> native Worker/Core.

This composes the existing real CLI and native runtime paths; only provider
decisions are scripted. Run with the locked dev graph (missing modules fail).
Relocating this test and its named helpers, with no backend source beside them,
also permits qualification of an installed wheel outside the checkout.
"""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys

from test_acp_cli import StdioPeer
from fixtures.peer_return_worker_probe import qualification_runtime


class NativePeer(StdioPeer):
    async def result(self, identity):
        async with asyncio.timeout(75):
            while True:
                line = await self.process.stdout.readline()
                assert line, (await self.process.stderr.read()).decode()
                row = json.loads(line)
                if row.get("id") == identity:
                    return row
                self.updates.append(row)


async def test_stdio_acp_executes_real_core_tool_and_same_native_resume(tmp_path, monkeypatch, aiohttp_client):
    graph, paths = qualification_runtime()
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.session_files import project_slug
    from amplifier_web.session_identity import native_id

    for key, folder in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
                        ("AMPLIFIER_WEB_HOME", "app"),
                        ("AMPLIFIER_SESSION_STATE_HOME", "ownership")):
        directory = tmp_path / folder
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(key, str(directory))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    (tmp_path / "shared/settings.yaml").write_text("bundle:\n  app: []\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider_source = Path(__file__).parent / "fixtures/acp_native_provider.py"
    provider = tmp_path / "provider/amplifier_module_provider_acp_native_fixture"
    provider.mkdir(parents=True)
    (provider / "__init__.py").write_text(
        "from importlib.util import spec_from_file_location, module_from_spec\n"
        f"_spec=spec_from_file_location('acp_native_fixture', {str(provider_source.resolve())!r})\n"
        "_module=module_from_spec(_spec)\n_spec.loader.exec_module(_module)\n"
        "Provider=_module.Provider\nmount=_module.mount\n")
    audit = tmp_path / "provider.jsonl"
    bundle = tmp_path / "bundle.yaml"
    bundle.write_text(
        f"bundle:\n  name: native-acp-fixture\n  version: {SNAPSHOT_VERSION}\n"
        "session:\n  orchestrator:\n    module: loop-live\n"
        f"    source: {json.dumps(str(paths['amplifier_module_loop_live']))}\n"
        "    config:\n      use_streaming: false\n  context:\n    module: context-simple\n"
        f"    source: {json.dumps(str(paths['amplifier_module_context_simple']))}\n"
        "providers:\n  - module: provider-acp-native-fixture\n"
        f"    source: {json.dumps(str(provider.parent))}\n"
        f"    config:\n      audit: {json.dumps(str(audit))}\n")
    runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                             startup_timeout=45)
    app = await create_app(tmp_path / "app", workspace=workspace, runtime=runtime,
                           preload_providers=False, voice=False,
                           background_updates=False, background_work=False)
    service = app["service"]
    runtime.app_bridge, runtime.home = service.app_bridge, tmp_path / "app"
    client = await aiohttp_client(app)
    credential = tmp_path / "owner-token"
    credential.write_text(app["control_token"])
    credential.chmod(0o600)
    processes, events = [], []
    original = service._execution_observers.original

    async def observe(kind, payload):
        result = await original(kind, payload)
        events.append((kind, dict(payload)))
        return result

    async def observe_batch(rows):
        result = await original.batch(rows)
        events.extend((kind, dict(payload)) for kind, payload in rows)
        return result

    # Preserve the sink and its batch interface; observe native facts, not replace
    # transport/admission/terminal production with scripted events.
    service._execution_observers.original = type(original)(observe, observe_batch)

    async def launch():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-m", "amplifier_web",
            "--data-dir", str(tmp_path / "relay"), "--workspace", str(workspace),
            "acp", "--bundle", str(bundle), "--server", str(client.make_url("")).rstrip("/"),
            "--token-file", str(credential),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        peer = NativePeer(process)
        assert (await peer.call("initialize", protocolVersion=1))["result"]["protocolVersion"] == 1
        return peer

    try:
        first = await launch()
        sid = (await first.call("session/new", cwd=str(workspace), mcpServers=[]))["result"]["sessionId"]
        for turn in (1, 2):
            peer = first if turn == 1 else await launch()
            if turn == 2:
                assert (await peer.call("session/resume", sessionId=sid, cwd=str(workspace),
                                       mcpServers=[]))["result"] == {}
            response = await peer.call("session/prompt", sessionId=sid,
                                      prompt=[{"type": "text", "text": f"Native input {turn}: 漢🙂"}])
            assert response.get("result", {}).get("stopReason") == "end_turn", response
            text = "".join(row["params"]["update"].get("content", {}).get("text", "")
                           for row in peer.updates if row.get("method") == "session/update")
            assert f"Native reply {turn}: 漢🙂" in text, peer.updates
            assert service._session(sid)["title"] == f"Native ACP effect {turn}"
            await peer.eof()
        rows = [json.loads(line) for line in audit.read_text().splitlines()]
        provider_rows = [row for row in rows if row["kind"] == "provider"]
        assert [row["round"] for row in provider_rows] == [1, 2]
        worker_pid = runtime.workers[sid]["process"].pid
        assert {row["pid"] for row in rows} == {worker_pid}
        assert worker_pid not in {os.getpid(), *(p.pid for p in processes)}
        commands = [json.loads(raw) for (raw,) in service.db.execute(
            "SELECT receipt FROM commands WHERE id IN ('native-acp-title-1','native-acp-title-2')")]
        assert len(commands) == 2 and all(row["accepted"] for row in commands)
        row = service._session(sid)
        assert native_id(row) == runtime.workers[sid]["runtime_id"] == sid
        delivered = Counter(payload["inputId"] for kind, payload in events
                            if kind == "runtime.status" and payload.get("event") == "input.delivered")
        terminal = Counter(identity for kind, payload in events
                           if kind == "runtime.generation" and payload.get("event") == "generation.finished"
                           for identity in payload.get("input_ids", []))
        assert len(delivered) == 2 and set(delivered.values()) == {1}
        assert terminal == delivered
        assert len([m for m in row["messages"] if m["role"] == "user"]) == 2
        evidence = {"status": "passed", "runtime": graph, "nativeSession": sid,
                    "project": project_slug(workspace), "workerPid": worker_pid,
                    "nativeInputs": dict(delivered), "nativeTerminals": dict(terminal),
                    "provider": provider_rows, "effects": commands,
                    "limits": ["Scripted provider; no paid API", "loop-live/context-simple composition, not full Work bundle",
                               "Native worker remains resident across ACP reconnect; no host restart/compaction",
                               "Text Unicode final output, not token-streaming/media qualification"]}
        (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        output = os.environ.get("AMPLIFIER_TEST_ARTIFACTS")
        if output:
            target = Path(output) / "native-acp"
            target.mkdir(parents=True, exist_ok=True)
            (target / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
            (target / "events.json").write_text(json.dumps(events, indent=2) + "\n")
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
        await client.close()