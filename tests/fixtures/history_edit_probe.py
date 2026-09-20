"""Run the actual Worker/loop-live warm lifecycle with a local provider.

This fixture deliberately runs in the isolated runtime environment with a
caller-provided Foundation checkout.  It never uses a provider credential,
network model request, real home, or existing session state.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from amplifier_web.runtime_worker import Worker


async def main():
    root = Path(sys.argv[1]).resolve()
    workspace = root / "workspace"
    provider = root / "provider" / "amplifier_module_provider_fixture"
    workspace.mkdir(parents=True)
    provider.mkdir(parents=True)
    provider.joinpath("__init__.py").write_text(
        """from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class FixtureProvider:
    name = "fixture"
    def get_info(self): return ProviderInfo(id="fixture", display_name="Fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    async def complete(self, request, **kwargs): return ChatResponse(content=[TextBlock(text="fixture response")])
    def parse_tool_calls(self, response): return []
async def mount(coordinator, config=None): await coordinator.mount("providers", FixtureProvider(), name="fixture")
"""
    )
    from amplifier_module_context_simple import __file__ as context_module

    bundle = root / "fixture.md"
    bundle.write_text(
        f"""---
bundle:
  name: warm-fixture
  version: 0.0.1
session:
  orchestrator:
    module: loop-live
  context:
    module: context-simple
    source: {Path(context_module).parent.parent}
providers:
  - module: provider-fixture
    source: {provider.parent}
---
Reply with the fixture response.
"""
    )
    os.environ.update(
        AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
    )
    events = []
    import amplifier_web.runtime_worker as worker_module

    worker_module.publish = events.append
    worker = Worker()
    config = {"id": "warm-fixture", "workspace": str(workspace), "bundle": str(bundle)}
    cold_started = time.monotonic()
    await worker.start(config)
    cold_seconds = time.monotonic() - cold_started
    assert worker.session is not None, events
    first_session, first_execution = id(worker.session), id(worker.execution)
    await worker.command({"op": "send", "id": "one-request", "input_id": "one", "text": "one"})
    for _ in range(200):
        if worker.parked:
            break
        await asyncio.sleep(0.02)
    assert worker.parked, events
    warm_started = time.monotonic()
    await worker.command({"op": "send", "id": "two-request", "input_id": "two", "text": "two"})
    for _ in range(200):
        if worker.parked and any(event.get("type") == "generation.finished"
                                 and "two" in event.get("input_ids", []) for event in events):
            break
        await asyncio.sleep(0.02)
    assert worker.parked and any(event.get("type") == "generation.finished"
                                 and "two" in event.get("input_ids", []) for event in events), events
    warm_seconds = time.monotonic() - warm_started
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_web.session_files import sessions_dir
    checkpoint = {"messages": SessionHistoryStore(sessions_dir(workspace) / config["id"]).load_messages()}
    assert worker.shared_store.read() is None
    assert id(worker.session) == first_session
    assert id(worker.execution) == first_execution
    assert [event["text"] for event in events if event.get("type") == "assistant.message"] == [
        "fixture response", "fixture response"]
    assert [row["content"] for row in checkpoint["messages"] if row["role"] == "user"][-2:] == ["one", "two"]
    from amplifier_web.automatic_history import read_transcript
    from amplifier_web.session_files import project_slug
    source = {**config, 'status':'idle', 'nativeProject':project_slug(workspace), 'nativeIdentity':config['id'], 'messages':[]}
    saved_view = read_transcript(source)
    source.update(messages=saved_view['messages'], nativeRevision=saved_view['revision'])
    second = next(row for row in source['messages'] if row['role']=='user' and row['text']=='two')
    await worker.command({'op':'control', 'id':'edit-command', 'operation':'history.edit',
        'arguments':{'source':source, 'operationId':'edited-two', 'messageId':second['id'], 'text':'revised two', 'attachments':[]}})
    for _ in range(400):
        if worker.parked and any(event.get('type')=='generation.finished' and 'edited-two' in event.get('input_ids', []) for event in events):
            break
        await asyncio.sleep(.02)
    assert any(event.get('type')=='generation.finished' and 'edited-two' in event.get('input_ids', []) for event in events), events
    assert worker.parked and worker.shared_store.read() is None
    edited = SessionHistoryStore(sessions_dir(workspace) / config['id']).load_messages()
    assert [row['content'] for row in edited if row['role']=='user' and not (row.get('metadata') or {}).get('ephemeral')] == ['one', 'revised two']
    from amplifier_web.history_revision import receipt_path
    receipt = json.loads(receipt_path(root/'home',config['id'],'edited-two').read_text())
    assert receipt['phase']=='committed' and [row['content'] for row in receipt['contextBefore'] if row['role']=='user' and not (row.get('metadata') or {}).get('ephemeral')] == ['one','two']
    boundary = next(i for i, event in enumerate(events) if event.get('type')=='history.revised')
    admission = next(i for i, event in enumerate(events) if event.get('type')=='input.delivered' and event.get('input_id')=='edited-two')
    assert boundary < admission
    await worker.command({"op": "retire", "id": "retire-request"})
    assert worker.shutdown.is_set(), events
    await worker.run()
    restored = Worker()
    await restored.start(config)
    await restored.command({"op": "send", "id": "three-request", "input_id": "three", "text": "three"})
    for _ in range(200):
        if restored.parked:
            break
        await asyncio.sleep(.02)
    assert restored.parked, events
    messages = SessionHistoryStore(sessions_dir(workspace) / config["id"]).load_messages()
    assert [row['content'] for row in messages if (row.get('metadata') or {}).get('amplifier_input', {}).get('kind') == 'user'] == ['one', 'revised two', 'three']
    assert len([event for event in events if event.get('type') == 'assistant.message']) == 4
    print(json.dumps({
        "cold_prepare_seconds": round(cold_seconds, 6),
        "stamp_only_dispatch_seconds": round(warm_seconds, 6),
        "same_mounted_session": True,
        "two_authoritative_turns": True,
        "retired_safely": True,
        "resumed_without_replay": True,
        "current_edit_replaced_only_later_context": True,
    }))
    restored.shutdown.set()
    await restored.run()


asyncio.run(main())
