"""Actual stdio ACP client -> authenticated host -> native Worker/Core.

This composes the existing real CLI and native runtime paths; only provider
decisions are scripted. Run with the locked dev graph (missing modules fail).
Relocating this test and its named helpers, with no backend source beside them,
also permits qualification of an installed wheel outside the checkout.
"""
import asyncio
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys

import aiohttp
import pytest

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


@pytest.mark.parametrize("wrapper", ["direct", "published-instance", "unified-profile"])
async def test_stdio_acp_executes_real_core_tool_and_same_native_resume(tmp_path, monkeypatch, aiohttp_client, wrapper):
    binary = os.environ.get("KISS502_INSTANCE_BINARY") if wrapper == "published-instance" else None
    if wrapper == "published-instance":
        if not binary:
            pytest.skip("Select verified published Instance 0.22.15 ARM64 binary for wrapper qualification")
        assert hashlib.sha256(Path(binary).read_bytes()).hexdigest() == (
            "8772fc97435eb9922bed694b9e0d4ef43d5312c0ee036ff0173b9a27a18afbfa")
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
        f"    config:\n      audit: {json.dumps(str(audit))}\n"
        f"      create_task: {'true' if wrapper == 'unified-profile' else 'false'}\n")
    runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                             startup_timeout=45)
    app = await create_app(tmp_path / "app", workspace=workspace, runtime=runtime,
                           preload_providers=False, voice=False,
                           background_updates=False, background_work=False)
    service = app["service"]
    runtime.app_bridge, runtime.home = service.app_bridge, tmp_path / "app"
    from amplifier_web.agent_instance import NAMESPACE, room_source, session_metadata, prompt_metadata
    source = room_source("native-profile-room", "https://fixture.example")
    if wrapper == "unified-profile":
        service.server_config["agentInstance"] = {
            "id": "urn:fixture:U", "label": "Fixture U", "workspace": str(workspace),
            "bundle": str(bundle), "sources": [source]}
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
    relay = [sys.executable, "-I", "-B", "-m", "amplifier_web",
             "--data-dir", str(tmp_path / "relay"), "--workspace", str(workspace),
             "acp", "--bundle", str(bundle), "--server", str(client.make_url("")).rstrip("/"),
             "--token-file", str(credential)]
    instance_process = None
    instance_init = None
    instance_log = None
    endpoint_command = relay

    async def prepare_instance():
        nonlocal instance_process, instance_init, instance_log, endpoint_command
        home = tmp_path / "instance"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        instance_log = (tmp_path / "instance.log").open("wb")
        instance_init = await asyncio.create_subprocess_exec(
            binary, "init", "--home", str(home), "--name", "Native ACP fixture",
            "--bind", "127.0.0.1", "--port", str(port),
            stdout=instance_log, stderr=instance_log)
        assert await asyncio.wait_for(instance_init.wait(), 10) == 0
        config_path = home / "config.json"
        configured = json.loads(config_path.read_text())
        value = configured["config"]
        value.update(enabled=True, setup_complete=True, purpose="Offline native ACP fixture")
        value["server"].update(welcome_port=None, public_url=f"https://127.0.0.1:{port}")
        value["backend"] = {
            "command": relay[0], "args": relay[1:],
            "env": {"PYTHONDONTWRITEBYTECODE": "1"}, "secret_env": {},
            "working_directory": str(workspace), "allowed_working_roots": [str(workspace)]}
        config_path.write_text(json.dumps(configured))
        instance_process = await asyncio.create_subprocess_exec(
            binary, "serve", "--home", str(home), stdout=instance_log, stderr=instance_log)
        trust = ssl.create_default_context(cafile=str(home / "tls/root-ca.crt"))
        control = (home / "secrets/control-token").read_text().strip()
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=trust),
                headers={"Authorization": "Bearer " + control}) as api:
            async with asyncio.timeout(15):
                while True:
                    assert instance_process.returncode is None
                    try:
                        async with api.get(f"https://127.0.0.1:{port}/api/v1/config") as response:
                            assert response.status == 200
                            break
                    except aiohttp.ClientConnectorError:
                        await asyncio.sleep(.02)
            async with api.post(f"https://127.0.0.1:{port}/api/v1/tokens", json={
                "name": "native-acp-fixture", "scopes": ["acp"], "expires_in_seconds": 600}) as response:
                assert response.status == 200
                token = (await response.json())["token"]
        attach = tmp_path / "instance-token"
        attach.write_text(token)
        attach.chmod(0o600)
        endpoint_command = [binary, "acp", "--home", str(home), "--token-file", str(attach)]

    async def launch():
        process = await asyncio.create_subprocess_exec(
            *endpoint_command,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        peer = NativePeer(process)
        options = {"_meta": {NAMESPACE: {"version": 1}}} if wrapper == "unified-profile" else {}
        initialized = await peer.call("initialize", protocolVersion=1, **options)
        assert initialized["result"]["protocolVersion"] == 1
        if options:
            assert initialized["result"]["_meta"][NAMESPACE]["agentId"] == "urn:fixture:U"
        return peer

    try:
        if binary:
            await prepare_instance()
        first = await launch()
        session_options = {"_meta": session_metadata("urn:fixture:U", source)} if wrapper == "unified-profile" else {}
        sid = (await first.call("session/new", cwd=str(workspace), mcpServers=[], **session_options))["result"]["sessionId"]
        source_inputs = []
        for turn in (1, 2):
            peer = first if turn == 1 else await launch()
            if turn == 2:
                assert (await peer.call("session/resume", sessionId=sid, cwd=str(workspace),
                                       mcpServers=[], **session_options)).get("error") is None
            binding = prompt_metadata("urn:fixture:U", source, f"source-input-{turn}",
                actor="fixture-person", audience=["conversation"], reply_to=source["conversationId"],
                interruption_revision=0)
            options = {"_meta": binding} if wrapper == "unified-profile" else {}
            response = await peer.call("session/prompt", sessionId=sid,
                                      prompt=[{"type": "text", "text": f"Native input {turn}: 漢🙂"}], **options)
            assert response.get("result", {}).get("stopReason") == "end_turn", response
            text = "".join(row["params"]["update"].get("content", {}).get("text", "")
                           for row in peer.updates if row.get("method") == "session/update")
            assert f"Native reply {turn}: 漢🙂" in text, peer.updates
            assert service._session(sid)["title"] == f"Native ACP effect {turn}"
            if options:
                inspected = await peer.call("_amplifier/instance", sessionId=sid,
                    operation="inspectInput", binding=binding[NAMESPACE])
                assert "result" in inspected, inspected
                source_inputs.append(inspected["result"])
                assert inspected["result"]["delivery"] == "accepted"
                assert bool(inspected["result"]["work"]) is (turn == 1), inspected
            await peer.eof()
        rows = [json.loads(line) for line in audit.read_text().splitlines()]
        provider_rows = [row for row in rows if row["kind"] == "provider"]
        assert [row["round"] for row in provider_rows] == [1, 2]
        worker_pid = runtime.workers[sid]["process"].pid
        assert {row["pid"] for row in rows} == {worker_pid}
        for provider_row in provider_rows:
            for name, worker_module in provider_row["modules"].items():
                path = Path(importlib.import_module(name).__file__).resolve()
                assert worker_module == {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
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
        evidence = {"status": "passed", "runtime": graph, "nativeSession": sid, "wrapper": wrapper,
                    "project": project_slug(workspace), "workerPid": worker_pid,
                    "nativeInputs": dict(delivered), "nativeTerminals": dict(terminal),
                    "provider": provider_rows, "effects": commands, "sourceInputs": source_inputs,
                    "limits": ["Scripted provider; no paid API", "loop-live/context-simple composition, not full Work bundle",
                               "Native worker remains resident across ACP reconnect; no host restart/compaction",
                               "Text Unicode final output, not token-streaming/media qualification"]}
        (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        output = os.environ.get("AMPLIFIER_TEST_ARTIFACTS")
        if output:
            target = Path(output) / ("native-acp-" + wrapper)
            target.mkdir(parents=True, exist_ok=True)
            (target / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
            (target / "events.json").write_text(json.dumps(events, indent=2) + "\n")
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
        for process in (instance_init, instance_process):
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        if instance_log:
            instance_log.close()
        await client.close()