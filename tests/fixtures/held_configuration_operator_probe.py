"""Real native operator discovery -> cancellation -> future choice, offline only.

Reuse maintained Core/Foundation/worker, fault injection and process containment.
The provider scripts real app_control calls; no fabricated native generation or
private operation ID is supplied to that operator. Local fixture bundle choices
are not evidence for the full work-amp-dev composition or live providers.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import queued_configuration_native_probe as base

MANAGER = r'''
        if users and "RECOVERY_MANAGER" in users[-1]:
            def output(identity):
                message = next((m for m in request.messages
                    if m.role == "tool" and m.tool_call_id == identity), None)
                if message is None: return None
                value = json.loads(text(message))
                while isinstance(value, dict) and ("output" in value or "result" in value):
                    value = value.get("output", value.get("result"))
                assert isinstance(value, dict), value
                return value
            def call(identity, action, args):
                return ChatResponse(content=[], tool_calls=[ToolCall(
                    id=identity, name="app_control", arguments={"operation":"dispatch",
                    "args":{"action":action, "id":identity, "args":args}})])
            final = output("manager-final")
            if final is not None:
                assert final["pendingBundleSelection"]["bundle"] == scenario["nextBundle"], final
                assert final["blockedReason"] is None and final["nextMessageRevision"] == 1, final
                return ChatResponse(content=[TextBlock(text="cancel-migration-finished")])
            if output("manager-save") is not None:
                return call("manager-final", "conversation.configuration", {"sessionId":scenario["sid"]})
            if output("manager-cancel") is not None:
                return call("manager-save", "conversation.configure", {
                    "sessionId":scenario["sid"], "expectedRevision":0, "bundle":scenario["nextBundle"]})
            discovered = output("manager-discover")
            if discovered is None:
                return call("manager-discover", "conversation.configuration", {"sessionId":scenario["sid"]})
            pending = discovered["pendingOperation"]
            assert pending["availability"] == "retained", discovered
            args = {"sessionId":scenario["sid"], "operationId":pending["operationId"],
                    "expectedRevision":pending["revision"]}
            checked = output("manager-read")
            if checked is None:
                return call("manager-read", "configuration.operation.read", {**args,"includeRecovery":True})
            assert checked["recovery"]["canCancel"], checked
            return call("manager-cancel", "configuration.operation.cancel", args)
'''


async def restart_and_consume(root):
    """A genuine fresh target input, never a replay of the abandoned operation."""
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService
    from amplifier_web.configuration_recovery import suppress_input
    from amplifier_web.runtime_controls import override_path
    report_path = root / 'evidence.json'
    report = json.loads(report_path.read_text())
    data = json.loads((root / 'scenario.json').read_text())
    sid = data['sid']
    original_command = RuntimeManager._command
    def prepared_command(self, *args, **kwargs):
        # Match the maintained production-probe lane: keep real resolver,
        # subprocess and qualification, selecting this offline installed graph.
        # The recovery worker wrapper has an extra root argument and is not a
        # valid draft_defaults_probe command prefix.
        return list(self.command or [sys.executable, '-u',
            str(base.SOURCE / 'amplifier_web/runtime_worker.py')])
    RuntimeManager._command = prepared_command
    runtime = RuntimeManager(command=[sys.executable, '-u', str(Path(base.__file__).resolve()),
        '--worker', str(root), '--targeted-saved'], startup_timeout=45,
        retention={'prewarm_on_select': False})
    app = AppService(root / 'app', workspace=root / 'workspace', runtime=runtime)
    app.management = Management(app)
    runtime.app_bridge, runtime.home = app.app_bridge, app.data_dir
    settings = override_path(sid).read_bytes()
    calls = root / 'provider-calls.jsonl'
    before_calls = len(calls.read_text().splitlines())
    processes = []
    try:
        view = (await app.dispatch('conversation.configuration', {'sessionId': sid},
            include_state=False))['result']
        assert view['pendingBundleSelection']['bundle'] == data['nextBundle']
        assert view['nextMessageRevision'] == 1 and view['blockedReason'] is None
        assert not runtime.workers and len(calls.read_text().splitlines()) == before_calls
        target = app._session(sid)
        old_request = {'acceptedAt': 1}
        assert suppress_input(target, old_request)
        receipt = await app.dispatch('conversation.send', {'sessionId': sid,
            'text': 'RECOVERY_NEXT', 'expectedConfigurationRevision': 1},
            command_id='fresh-input-after-cancel', include_state=False)
        assert receipt['accepted']
        await base.until(lambda: target.get('collaborationGeneration', {}).get('terminal'), seconds=60)
        await base.until(lambda: runtime.workers.get(sid, {}).get('parked'), seconds=60)
        processes = [row['process'] for row in runtime.workers.values()]
        turn = next(row for row in target['execution']['turns'] if row['id'] == 'fresh-input-after-cancel')
        assert turn['phase'] == 'completed', turn
        assert target['bundle'] == data['nextBundle'] and target['appliedConfigurationRevision'] == 1
        assert not target.get('pendingBundleSelection') and suppress_input(target, old_request)
        native_id = runtime.workers[sid]['runtime_id']
        history = SessionStore.for_app(app.data_dir, root / 'workspace').load(native_id)[0]
        ids = [(row.get('metadata') or {}).get('amplifier_input', {}).get('id') for row in history]
        assert ids.count('fresh-input-after-cancel') == 1
        assert ids.count('native-original-input') == 1
        assert override_path(sid).read_bytes() == settings
        report.update(restartFutureChoiceVerified=True, freshTurnCompleted=True,
            appliedRevision=1, nativeHistoryExactlyOnce=True, oldInputStillSuppressed=True)
    finally:
        processes.extend(row['process'] for row in runtime.workers.values() if row['process'] not in processes)
        await app.close()
        RuntimeManager._command = original_command
        report['restartCleanup'] = {'allExited': all(p.returncode is not None for p in processes),
                                    'runtimeRows': len(runtime.workers)}
        report_path.write_text(json.dumps(report, indent=2))
        assert report['restartCleanup'] == {'allExited': True, 'runtimeRows': 0}


async def cancel_migration(app, runtime, owner, action, root, bundle, scenario, calls,
                           record, processes, report, **kwargs):
    from amplifier_web.configuration_recovery import receipt_path
    from amplifier_web.runtime_controls import override_path
    sid = owner['id']
    assert record['state']['effect'] == 'saved_not_mounted'
    assert sid not in runtime.workers and owner['configurationBusy']
    before_messages = copy.deepcopy(owner['messages'])
    before_generation = copy.deepcopy(owner['collaborationGeneration'])
    before_task = copy.deepcopy(owner['task'])
    native = record['attempts'][0]['nativeSessionId']
    settings = override_path(native).read_bytes()
    sealed = receipt_path(record['attempts'][0], home=app.data_dir).read_bytes()
    before_calls = calls.read_bytes()
    candidate = root / 'candidate.yaml'
    candidate.write_bytes(bundle.read_bytes())
    data = json.loads(scenario.read_text())
    # Deliberately no operation ID. Discovery must work from the public target ID.
    data['nextBundle'] = str(candidate)
    scenario.write_text(json.dumps(data))
    await action('session.create', {'title':'Native cancellation operator',
        'bundle': str(bundle), 'select':False}, 'cancel-manager-create')
    manager = next(row for row in app.state['sessions'] if row['id'] != sid)
    access = (await action('access.get', {'sessionId':manager['id']}, 'cancel-manager-access'))['result']
    await action('access.set', {'sessionId':manager['id'], 'scope':'session',
        'mode':'full', 'expectedRevision':access['revision']}, 'cancel-manager-full')
    await action('conversation.send', {'sessionId':manager['id'], 'text':'RECOVERY_MANAGER'}, 'cancel-manager-input')
    try:
        await base.until(lambda: manager.get('collaborationGeneration', {}).get('terminal')
            or manager.get('status') == 'error', seconds=60)
        assert any(m.get('text') == 'cancel-migration-finished' for m in manager['messages']), manager.get('error')
    except BaseException:
        report['managerFailure'] = {'status': manager.get('status'), 'error': manager.get('error'),
                                   'messages': manager.get('messages', [])[-8:]}
        raise
    for row in runtime.workers.values():
        if row['process'] not in processes:
            processes.append(row['process'])
    current = app.management.configuration_record(sid, record['id'])
    assert current['state']['phase'] == 'cancelled' and current['state']['effect'] == 'saved_not_mounted'
    assert current['request'] == record['request'] and current['attempts'] == record['attempts']
    assert override_path(native).read_bytes() == settings
    assert receipt_path(record['attempts'][0], home=app.data_dir).read_bytes() == sealed
    assert owner['messages'] == before_messages and owner['collaborationGeneration'] == before_generation
    assert owner['task'] == before_task and sid not in runtime.workers
    assert owner['pendingBundleSelection']['bundle'] == str(candidate)
    assert owner['pendingBundleSelection']['authority']['chain'][0]['sessionId'] == manager['id']
    later = [json.loads(line) for line in calls.read_bytes()[len(before_calls):].splitlines()]
    assert later and all(row['pid'] == runtime.workers[manager['id']]['process'].pid for row in later)
    report.update(actualManagerTool=True, publicDiscovery=True, exactCancellation=True,
        savedWithoutStarting=True, noTargetInputOrContinuation=True, originalAuthorityPreserved=True,
        oneSettingsSave=True, nativeId=native, nextMessageRevision=owner['nextMessageRevision'])


if __name__ == '__main__':
    begin = base.PROVIDER.index('        if users and "RECOVERY_MANAGER" in users[-1]:')
    end = base.PROVIDER.index('        if scenario.get("continuation"):', begin)
    base.PROVIDER = base.PROVIDER[:begin] + MANAGER + base.PROVIDER[end:]
    base.targeted_saved = cancel_migration
    async def main():
        root = Path(sys.argv[1]).resolve()
        await base.bounded_probe(root, False, targeted=True)
        await restart_and_consume(root)
    asyncio.run(main())
