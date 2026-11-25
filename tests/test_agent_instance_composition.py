"""Actual Unified source consumer plus advertised profile plus two Core workers.

X is an independent public-SDK subprocess; U is one configured public identity
with separate native contexts in two source rooms. No paid model or live service.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

from fixtures.peer_return_worker_probe import qualification_runtime
from test_service import Runtime as SourceRuntime
from test_shared_conversation import idle, until


async def test_native_public_agent_two_rooms_with_simultaneous_external_peer(tmp_path, monkeypatch, aiohttp_client):
    graph, paths = qualification_runtime()
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.service import AppService
    from amplifier_web.external_conversation import bindings
    for variable, directory in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
            ("AMPLIFIER_WEB_HOME", "backend"), ("AMPLIFIER_SESSION_STATE_HOME", "ownership")):
        path = tmp_path / directory
        path.mkdir(mode=0o700)
        monkeypatch.setenv(variable, str(path))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    (tmp_path / "shared/settings.yaml").write_text("bundle:\n  app: []\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = tmp_path / "provider/amplifier_module_provider_acp_native_fixture"
    provider.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures/acp_native_provider.py"
    (provider / "__init__.py").write_text(
        "from importlib.util import spec_from_file_location,module_from_spec\n"
        f"_spec=spec_from_file_location('shared_native_fixture',{str(fixture.resolve())!r})\n"
        "_module=module_from_spec(_spec)\n_spec.loader.exec_module(_module)\n"
        "Provider=_module.Provider\nmount=_module.mount\n")
    audit = tmp_path / "provider.jsonl"
    bundle = tmp_path / "native.yaml"
    bundle.write_text(
        f"bundle:\n  name: native-room-fixture\n  version: {SNAPSHOT_VERSION}\n"
        "session:\n  orchestrator:\n    module: loop-live\n"
        f"    source: {json.dumps(str(paths['amplifier_module_loop_live']))}\n"
        "    config:\n      use_streaming: false\n  context:\n    module: context-simple\n"
        f"    source: {json.dumps(str(paths['amplifier_module_context_simple']))}\n"
        "providers:\n  - module: provider-acp-native-fixture\n"
        f"    source: {json.dumps(str(provider.parent))}\n"
        f"    config:\n      shared: true\n      audit: {json.dumps(str(audit))}\n")
    runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                             startup_timeout=45)
    app = await create_app(tmp_path / "backend", workspace=workspace, runtime=runtime,
        voice=False, preload_providers=False, background_updates=False, background_work=False)
    backend = app["service"]
    runtime.app_bridge, runtime.home = backend.app_bridge, tmp_path / "backend"
    backend.server_config["agentInstance"] = {
        "id": "urn:fixture:U", "label": "U", "workspace": str(workspace), "bundle": str(bundle)}
    http = await aiohttp_client(app)
    credential = tmp_path / "backend-token"
    credential.write_text(app["control_token"])
    credential.chmod(0o600)
    source = AppService(tmp_path / "source", SourceRuntime(), workspace=workspace)
    source.server_config = {"public_origins": ["https://fixture-source.example"], "externalAgents": [
        {"id": "U", "label": "U", "command": sys.executable,
         "args": ["-I", "-B", "-m", "amplifier_web", "--data-dir", str(tmp_path / "relay"),
                  "--workspace", str(workspace), "acp", "--bundle", str(bundle),
                  "--server", str(http.make_url("")).rstrip("/"), "--token-file", str(credential)]},
        {"id": "X", "label": "X", "command": sys.executable,
         "args": [str(Path(__file__).parent / "fixtures/shared_acp_agent.py"),
                  str(tmp_path / "X/peer"), "beta"]}]}
    try:
        rooms = []
        for number, agents in enumerate((["U", "X"], ["U"])):
            receipt = await source.dispatch("session.create", {
                "workspace": str(workspace), "externalAgentIds": agents}, command_id=f"room-{number}")
            rooms.append(source._session(receipt["result"]["sessionId"]))
        first, second = rooms
        for room, text in zip(rooms, ("First room private source 漢🙂", "Second room distinct source")):
            await source.dispatch("conversation.send", {"sessionId": room["id"], "text": text},
                                  command_id="input-" + room["id"])
        async with asyncio.timeout(60):
            while not all((tmp_path / ("entered-" + row["id"])).exists() for row in rooms):
                failed = [row.get("error") or row.get("externalAgent") for row in rooms if row.get("status") == "error"]
                assert not failed, failed
                await asyncio.sleep(.02)
        assert len(runtime.workers) == 2
        assert len({row["process"].pid for row in runtime.workers.values()}) == 2
        assert all(row.get("status") == "working" for row in rooms)
        assert any(row.get("agentId") == "X" for row in first["messages"])
        public_ids = [next(row for row in bindings(room) if row["id"] == "U")["publicAgentId"] for room in rooms]
        assert public_ids == ["urn:fixture:U", "urn:fixture:U"]
        native_ids = [next(row for row in bindings(room) if row["id"] == "U")["sessionId"] for room in rooms]
        assert len(set(native_ids)) == 2
        (tmp_path / "release").touch()
        await asyncio.gather(*(idle(source, room["id"]) for room in rooms))
        async with asyncio.timeout(10):
            while second["id"] in source.external_conversations.turns:
                await asyncio.sleep(.02)
        for room, expected in zip(rooms, ("First room private source 漢🙂", "Second room distinct source")):
            replies = [row["text"] for row in room["messages"] if row.get("agentId") == "U"]
            assert replies == ["U public reply: " + expected], replies
        audits = [json.loads(line) for line in audit.read_text().splitlines()]
        human_contexts = [row["context"] for row in audits if row["kind"] == "room-input"
                          and row["context"]["trigger_id"] == row["context"]["messages"][0]["id"]]
        assert {row["conversation_id"] for row in human_contexts} == {row["id"] for row in rooms}
        for context in human_contexts:
            other = "Second room distinct source" if context["conversation_id"] == first["id"] else "First room private source"
            assert all(other not in row["text"] for row in context["messages"])
        assert source.runtime.started == source.runtime.sent == []
        # Closing observation and scanning pending work does not replay either
        # settled public input or alter the saved source pair/native identity.
        for room in rooms:
            participant = source.external_conversations.participants[(room["id"], "U")]
            await participant.close()
        original_count = len(audits)
        await source.external_conversations.resume_pending()
        assert [next(row for row in bindings(room) if row["id"] == "U")["sessionId"]
                for room in rooms] == native_ids
        assert len([json.loads(line) for line in audit.read_text().splitlines()]) == original_count
        evidence = {"status": "passed", "graph": graph, "publicAgent": "urn:fixture:U",
                    "sourceRooms": [row["id"] for row in rooms], "nativeSessions": native_ids,
                    "nativeWorkerPids": [row["process"].pid for row in runtime.workers.values()],
                    "audits": audits, "sourceRuntime": "Controlled source-only host; execution is actual backend Core workers",
                    "limits": ["Deterministic provider and SDK X", "No live model/network service", "Not full Work bundle"]}
        output = os.environ.get("AMPLIFIER_TEST_ARTIFACTS")
        if output:
            target = Path(output) / "native-two-rooms"
            target.mkdir(parents=True, exist_ok=True)
            (target / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        (tmp_path / "release").touch()
        await source.close()
        await http.close()