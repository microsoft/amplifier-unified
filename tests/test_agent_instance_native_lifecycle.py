"""Finite real local host/Worker/Core/profile lifecycle journeys.

Run with the locked dev graph. Only provider decisions are scripted. A host
shutdown here closes the actual service and native process, then constructs a
new AppService and RuntimeManager; it is not a process-crash survival claim.
"""
import asyncio
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

from fixtures.peer_return_worker_probe import qualification_runtime
from test_acp_native_execution import NativePeer


async def until(predicate):
    async with asyncio.timeout(60):
        while not predicate():
            await asyncio.sleep(.02)


def verify_modules(rows, fixture):
    for row in rows:
        for name, observed in row["modules"].items():
            path = fixture.resolve() if name == "fixture-provider" else Path(importlib.import_module(name).__file__).resolve()
            assert observed == {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


async def stream_source(tmp_path, workspace, bundle, backend, runtime, client, scenario, events):
    from amplifier_web.agent_instance import room_source
    from amplifier_web.browser_detail import project
    from amplifier_web.external_conversation import bindings
    from amplifier_web.service import AppService
    from test_service import Runtime as SourceRuntime
    from test_shared_conversation import idle
    # The source has no native runtime: its real ACP consumer reaches the backend's
    # actual Worker/Core. No fake SDK server supplies U's content or completion.
    source = AppService(tmp_path / "source", SourceRuntime(), workspace=workspace)
    credential = tmp_path / "source-token"
    credential.write_text(client.app["control_token"])
    credential.chmod(0o600)
    source.server_config = {"public_origins": ["https://fixture-source.example"], "externalAgents": [{
        "id": "alpha", "label": "U", "command": sys.executable,
        "args": ["-I", "-B", "-m", "amplifier_web", "--data-dir", str(tmp_path / "relay"),
                 "--workspace", str(workspace), "acp", "--bundle", str(bundle),
                 "--server", str(client.make_url("")).rstrip("/"), "--token-file", str(credential)]},
        {"id": "beta", "label": "SDK silent peer", "command": sys.executable,
         "args": ["-B", str(Path(__file__).parent / "fixtures/shared_acp_agent.py"),
                  str(tmp_path / "sdk-peer"), "beta"]}]}
    cleanup = {}
    try:
        receipt = await source.dispatch("session.create", {
            "workspace": str(workspace), "externalAgentIds": ["alpha", "beta"]}, command_id="stream-room")
        room = source._session(receipt["result"]["sessionId"])
        backend.server_config["agentInstance"]["sources"] = [
            room_source(room["id"], "https://fixture-source.example")]
        await source.dispatch("conversation.send", {"sessionId": room["id"], "text": "silent-empty"},
                              command_id="stream-input")
        prefix = "[[PA" if scenario == "stream-pass" else "Public 漢"
        await until(lambda: any(row.get("text") == prefix for row in room.get("externalStreams", {}).values()))
        participant = source.external_conversations.participants[(room["id"], "alpha")]
        assert participant.process.returncode is None
        during = [source.browser_state(room["id"]), source.session_state(room["id"]), project(room)]
        # Inspect all three real public projections while the real provider
        # stream is held between chunks, not just the settled final transcript.
        if scenario == "stream-pass":
            assert all("[[PA" not in json.dumps(row) for row in during), during
        else:
            assert all(prefix in json.dumps(row, ensure_ascii=False) for row in during), during
        (tmp_path / "release").touch()
        await idle(source, room["id"])
        assert not room.get("externalStreams")
        replies = [row["text"] for row in room["messages"] if row.get("agentId") == "alpha"]
        assert replies == ([] if scenario == "stream-pass" else ["Public 漢🙂 reply"]), replies
        binding = next(row for row in bindings(room) if row["id"] == "alpha")
        assert binding["publicAgentId"] == "urn:fixture:lifecycle"
        sid = binding["sessionId"]
        assert sid in runtime.workers
        worker = runtime.workers[sid]
        delivered = Counter(payload["inputId"] for kind, payload in events
                            if kind == "runtime.status" and payload.get("event") == "input.delivered")
        terminals = Counter(identity for kind, payload in events if kind == "runtime.generation"
            and payload.get("event") == "generation.finished" for identity in payload.get("input_ids", []))
        assert len(delivered) == 1 and list(delivered.values()) == [1] and terminals == delivered
        assert source.runtime.started == source.runtime.sent == []
        return {"status": "passed", "nativeSession": sid, "sourceRoom": room["id"],
                "sourceRuntime": "Controlled source only; real ACP consumer and backend native worker; beta is SDK silent control",
                "sourceProcessCleanup": cleanup,
                "publicDuringStream": during, "publicReplies": replies,
                "nativeInputs": dict(delivered), "nativeTerminals": dict(terminals),
                "workerPids": [worker["process"].pid]}
    finally:
        (tmp_path / "release").touch()
        participants = [row.process for row in source.external_conversations.participants.values()
                        if row.process is not None]
        await source.close()
        cleanup["processes"] = [{"pid": row.pid, "returncode": row.returncode} for row in participants]
        assert all(row.returncode is not None for row in participants), cleanup


@pytest.mark.parametrize("scenario", ["host-restart", "lost-response", "failure", "stream-pass", "stream-reply"])
async def test_native_profile_lifecycle(tmp_path, monkeypatch, aiohttp_client, scenario):
    graph, paths = qualification_runtime()
    from amplifier_web.agent_instance import NAMESPACE, room_source, session_metadata, prompt_metadata
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.session_identity import native_id

    for variable, directory in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
            ("AMPLIFIER_WEB_HOME", "app"), ("AMPLIFIER_SESSION_STATE_HOME", "ownership")):
        path = tmp_path / directory
        path.mkdir(mode=0o700)
        monkeypatch.setenv(variable, str(path))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    (tmp_path / "shared/settings.yaml").write_text("bundle:\n  app: []\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = tmp_path / "provider/amplifier_module_provider_acp_lifecycle_fixture"
    provider.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures/acp_lifecycle_provider.py"
    (provider / "__init__.py").write_text(
        "from importlib.util import spec_from_file_location,module_from_spec\n"
        f"_spec=spec_from_file_location('acp_lifecycle_fixture',{str(fixture.resolve())!r})\n"
        "_module=module_from_spec(_spec)\n_spec.loader.exec_module(_module)\n"
        "Provider=_module.Provider\nmount=_module.mount\n")
    audit = tmp_path / "provider.jsonl"
    bundle = tmp_path / "native.yaml"
    bundle.write_text(
        f"bundle:\n  name: native-lifecycle-fixture\n  version: {SNAPSHOT_VERSION}\n"
        "session:\n  orchestrator:\n    module: loop-live\n"
        f"    source: {json.dumps(str(paths['amplifier_module_loop_live']))}\n"
        f"    config:\n      use_streaming: {str(scenario.startswith('stream-')).lower()}\n"
        "  context:\n    module: context-simple\n"
        f"    source: {json.dumps(str(paths['amplifier_module_context_simple']))}\n"
        "providers:\n  - module: provider-acp-lifecycle-fixture\n"
        f"    source: {json.dumps(str(provider.parent))}\n"
        f"    config:\n      audit: {json.dumps(str(audit))}\n      scenario: {scenario}\n")
    source = room_source("lifecycle-room", "https://fixture-source.example")
    session_binding = session_metadata("urn:fixture:lifecycle", source)
    bindings = [prompt_metadata("urn:fixture:lifecycle", source, f"original-{turn}",
        actor="fixture-person", audience=["conversation"], reply_to=source["conversationId"],
        interruption_revision=0) for turn in ("first", "second")]
    processes, clients, events, workers, runtimes = [], [], [], [], []
    evidence = {"scenario": scenario, "graph": graph, "source": source, "status": "failed",
        "limits": ["Offline deterministic provider; no paid API or live account",
                   "loop-live/context-simple, not full Work bundle",
                   "Graceful isolated host/service shutdown, not OS host restart or process crash",
                   "No automatic replay or forged UI/human origin",
                   "Scripted provider stream hook contract, not vendor SDK streaming qualification"]}

    async def host():
        runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                                 startup_timeout=45)
        runtimes.append(runtime)
        app = await create_app(tmp_path / "app", workspace=workspace, runtime=runtime,
            preload_providers=False, voice=False, background_updates=False, background_work=False)
        service = app["service"]
        runtime.app_bridge, runtime.home = service.app_bridge, tmp_path / "app"
        service.server_config["agentInstance"] = {
            "id": "urn:fixture:lifecycle", "label": "Lifecycle fixture", "workspace": str(workspace),
            "bundle": str(bundle), "sources": [source]}
        original = service._execution_observers.original

        async def observe(kind, payload):
            result = await original(kind, payload)
            events.append((kind, dict(payload)))
            return result

        async def batch(rows):
            result = await original.batch(rows)
            events.extend((kind, dict(payload)) for kind, payload in rows)
            return result

        service._execution_observers.original = type(original)(observe, batch)
        client = await aiohttp_client(app)
        clients.append(client)
        return service, runtime, client

    async def launch(client):
        credential = tmp_path / f"token-{len(processes)}"
        credential.write_text(client.app["control_token"])
        credential.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-m", "amplifier_web",
            "--data-dir", str(tmp_path / "relay"), "--workspace", str(workspace),
            "acp", "--bundle", str(bundle), "--server", str(client.make_url("")).rstrip("/"),
            "--token-file", str(credential), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        peer = NativePeer(process)
        initialized = await peer.call("initialize", protocolVersion=1, _meta={NAMESPACE: {"version": 1}})
        assert initialized["result"]["_meta"][NAMESPACE]["agentId"] == "urn:fixture:lifecycle", initialized
        return peer

    def audit_rows():
        return [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []

    async def inspect(peer, binding):
        value = await peer.call("_amplifier/instance", operation="inspectInput", sessionId=sid,
                                binding=binding[NAMESPACE])
        assert "result" in value, value
        return value["result"]

    async def read(peer):
        value = await peer.call("_amplifier/instance", operation="read", sessionId=sid,
                                binding=session_binding[NAMESPACE])
        assert "result" in value, value
        return value["result"]

    def effects(service):
        return [json.loads(row[0]) for row in service.db.execute(
            "SELECT receipt FROM commands WHERE id LIKE 'lifecycle-title-%' ORDER BY id")]

    try:
        service, runtime, client = await host()
        if scenario.startswith("stream-"):
            evidence.update(await stream_source(tmp_path, workspace, bundle, service, runtime, client, scenario, events))
            evidence["status"] = "failed"
            workers.extend(runtime.workers.values())
            rows = [row for row in audit_rows() if row["kind"] == "provider-stream"]
            assert len(rows) == 1, rows
            verify_modules(rows, fixture)
            evidence["status"] = "passed"
            return
        first = await launch(client)
        created = await first.call("session/new", cwd=str(workspace), mcpServers=[], _meta=session_binding)
        sid = created["result"]["sessionId"]
        evidence["nativeSession"] = sid
        access_before = service.access_mode.snapshot(service._session(sid))
        assert access_before["mode"] == "ask", access_before
        response_id = await first.send("session/prompt", sessionId=sid,
            prompt=[{"type": "text", "text": "Lifecycle input first"}], _meta=bindings[0])
        if scenario == "lost-response":
            await until(lambda: (tmp_path / "after-effect").exists())
            inspector = await launch(client)
            resumed = await inspector.call("session/resume", sessionId=sid, cwd=str(workspace),
                                           mcpServers=[], _meta=session_binding)
            assert "result" in resumed, resumed
            before = await inspect(inspector, bindings[0])
            assert before["delivery"] == "accepted", before
            await inspector.eof()
            assert len(effects(service)) == 1 and effects(service)[0]["accepted"]
            # EOF destroys only this ACP observer, while the native provider is
            # held after its actual tool receipt and before its final response.
            await first.eof()
            (tmp_path / "release").touch()
            await until(lambda: any(kind == "runtime.generation" and payload.get("event") == "generation.finished"
                                   for kind, payload in events))
            evidence["lostResponse"] = {"requestId": response_id, "readResult": False,
                                        "afterEffectInspection": before}
        else:
            response = await first.result(response_id)
            evidence["firstResponse"] = response
            assert ("error" in response) is (scenario == "failure"), response
            if scenario == "failure":
                # ACP deliberately sanitizes exception text. Check the real
                # failed-generation receipt and the provider-side cause instead.
                receipt = response["error"]["data"]["inputReceipt"]
                assert receipt["status"]["phase"] == "failed", receipt
                assert receipt["status"]["failure"]["effects"] == "not_rolled_back", receipt
                assert sum(row["kind"] == "provider-failure" for row in audit_rows()) == 1
            else:
                assert response["result"]["stopReason"] == "end_turn", response
            await first.eof()
        workers.append(runtime.workers[sid])
        observer = await launch(client)
        resumed = await observer.call("session/resume", sessionId=sid, cwd=str(workspace),
                                      mcpServers=[], _meta=session_binding)
        assert "result" in resumed, resumed
        original = await inspect(observer, bindings[0])
        assert original["delivery"] == "accepted" and original["replayed"] is False, original
        work_before = await read(observer)
        task_before = work_before["work"]["task"]
        assert task_before and original["work"]["taskId"] == task_before["id"], work_before
        assert len(effects(service)) == 1
        evidence["before"] = {"input": original, "work": work_before, "effects": effects(service),
                              "generation": json.loads(json.dumps(service._session(sid).get("collaborationGeneration"))),
                              "status": service._session(sid).get("status")}
        if scenario == "host-restart":
            await observer.eof()
            await client.close()
            assert workers[0]["process"].returncode is not None
            service, runtime, client = await host()
            assert not runtime.workers
            observer = await launch(client)
            resumed = await observer.call("session/resume", sessionId=sid, cwd=str(workspace),
                                          mcpServers=[], _meta=session_binding)
            assert "result" in resumed, resumed
            assert not runtime.workers, "Attachment/inspection must not start another native writer"
        retained = await inspect(observer, bindings[0])
        assert retained["commandId"] == original["commandId"] and retained["delivery"] == "accepted", retained
        work_after = await read(observer)
        assert work_after["work"]["task"] == task_before, work_after
        if scenario == "host-restart":
            assert not runtime.workers, "Reconciliation must not replay native work"
        assert service._session(sid)["title"] == "Lifecycle effect first"
        assert len(effects(service)) == 1
        assert [row["turn"] for row in audit_rows() if row["kind"] == "provider"] == ["first"]
        evidence["reconnected"] = {"input": retained, "work": work_after, "effects": effects(service)}
        response = await observer.call("session/prompt", sessionId=sid,
            prompt=[{"type": "text", "text": "Lifecycle input second"}], _meta=bindings[1])
        evidence["secondResponse"] = response
        assert response.get("result", {}).get("stopReason") == "end_turn", response
        workers.append(runtime.workers[sid])
        assert native_id(service._session(sid)) == sid == workers[-1]["runtime_id"]
        assert service._session(sid)["title"] == "Lifecycle effect second"
        assert len(effects(service)) == 2 and all(row["accepted"] for row in effects(service))
        rows = audit_rows()
        foreground = [row for row in rows if row["kind"] == "provider"]
        assert [row["turn"] for row in foreground] == ["first", "second"], rows
        assert sum(row["kind"] == "receipt" and row["commandId"] == "lifecycle-title-first" for row in rows) == 1
        verify_modules(foreground, fixture)
        assert [row["content"].rsplit("Lifecycle input ", 1)[-1].strip()
                for row in foreground[-1]["messages"]] == ["first", "second"]
        pids = [worker["process"].pid for worker in workers]
        if scenario in {"host-restart", "failure"}:
            assert pids[0] != pids[1]
        else:
            assert pids[0] == pids[1], "Observation loss must not replace the native writer"
        messages = [row for row in service._session(sid)["messages"] if row["role"] == "user"]
        assert len(messages) == 2 and all(row["inputOrigin"] == "acp" for row in messages), messages
        delivered = Counter(payload["inputId"] for kind, payload in events
                            if kind == "runtime.status" and payload.get("event") == "input.delivered")
        terminal = Counter(identity for kind, payload in events if kind == "runtime.generation"
            and payload.get("event") in {"generation.finished", "generation.failed"}
            for identity in payload.get("input_ids", []))
        second = await inspect(observer, bindings[1])
        assert delivered == Counter({original["commandId"]: 1, second["commandId"]: 1}), delivered
        assert terminal == delivered, (terminal, delivered)
        starts = [payload for kind, payload in events if kind == "runtime.generation"
                  and payload.get("event") == "generation.started"]
        assert len(starts) == 2 and len({row["generation_id"] for row in starts}) == 2, starts
        access_after = service.access_mode.snapshot(service._session(sid))
        assert access_after == access_before, (access_before, access_after)
        retained_after = await inspect(observer, bindings[0])
        assert retained_after["commandId"] == original["commandId"]
        if scenario == "failure":
            assert retained_after["status"]["phase"] == "failed", retained_after
        evidence.update(status="passed", after={"input": second, "work": await read(observer),
                        "originalInput": retained_after, "effects": effects(service),
                        "access": access_after}, accessBefore=access_before, nativeInputs=dict(delivered),
                        nativeTerminals=dict(terminal), workerPids=pids, provider=rows)
        await observer.eof()
    finally:
        (tmp_path / "release").touch()
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
        for runtime in runtimes:
            for worker in runtime.workers.values():
                if not any(worker is observed for observed in workers):
                    workers.append(worker)
        for client in reversed(clients):
            await client.close()
        evidence["cleanup"] = {"relayExitCodes": [p.returncode for p in processes],
                               "workerExitCodes": [row["process"].returncode for row in workers]}
        settled = all(code is not None for values in evidence["cleanup"].values() for code in values)
        evidence["cleanup"]["settled"] = settled
        if not settled:
            evidence["status"] = "failed"
        evidence["provider"] = audit_rows()
        evidence["fixtureInputs"] = {
            str(path.relative_to(Path(__file__).parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), fixture, fixture.with_name("acp_native_provider.py"))}
        output = os.environ.get("AMPLIFIER_TEST_ARTIFACTS")
        if output:
            target = Path(output) / ("native-lifecycle-" + scenario)
            target.mkdir(parents=True, exist_ok=True)
            (target / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
            (target / "events.json").write_text(json.dumps(events, indent=2) + "\n")
        assert settled, evidence["cleanup"]