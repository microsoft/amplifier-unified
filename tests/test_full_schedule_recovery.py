"""Fresh system recovery of future targets does not restart stopped work."""
import copy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError
from amplifier_web.collaboration import BINDING, PRINCIPAL
from test_full_feedback import mode
from test_full_schedules import setup, preview, invoke


async def paused(tmp_path, monkeypatch):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'new_task')
    args = await preview(app, source, now[0], 'new_task')
    record = (await invoke(app, source, 'schedule.create', args, 'original'))['schedule']
    app.schedules.store.review(source['id'], record['id'], 'Owner stopped', now[0])
    source.update(status='stopped', interruptionRevision=1)
    source['collaborationGeneration']['terminal'] = True
    record = app.schedules.store.get(source['id'], record['id'])
    return app, runtime, now, source, record


@pytest.mark.parametrize('status', ['error', 'stopped'])
async def test_fresh_system_repairs_future_targets_without_waking_owner(tmp_path, monkeypatch, status):
    app, runtime, now, source, record = await paused(tmp_path, monkeypatch)
    try:
        source['status'] = status
        args = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': record['revision']}
        warm = app.management.ensure_runtime = AsyncMock()
        reviewed = await invoke(app, source, 'schedule.repairPreview', args, 'preview', 'system')
        assert reviewed['crossesStop'] and not reviewed['requiresStopAcknowledgement']
        confirm = {**args, 'previewHash': reviewed['previewHash']}
        result = await invoke(app, source, 'schedule.repairResume', confirm, 'fresh-repair', 'system')
        saved = result['schedule']
        provenance = saved['authorization']
        assert provenance['origin'] == 'system' and provenance['recoveryInitiation'] == 'fresh_system'
        assert provenance['crossedStop'] and 'acknowledgedStop' not in provenance
        assert provenance['authority']['interruptionRevision'] == 1
        assert not any(key in provenance['authority'] for key in ('generationId', 'runtimeSessionId', 'inputIds'))
        assert not warm.called and not runtime.inputs and not source['messages']
        assert source['status'] == status
        assert app.schedules.configuration(saved) == app.schedules.configuration(record)
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'accepted', run
        assert run['authorizationCommandId'] == 'fresh-repair'
        assert len(runtime.inputs) == 1 and runtime.inputs[0][0] != source['id']
        assert source['status'] == status
        assert all(call.args[0]['id'] != source['id'] for call in warm.call_args_list)
        await mode(app, source, 'ask')
        source['interruptionRevision'] += 1
        retained = await invoke(app, source, 'schedule.repairResume', confirm, 'fresh-repair', 'system')
        assert retained == {**result, 'duplicate': True}
        assert app.schedules.store.run(source['id'], run['id']) == run
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['stop', 'configuration', 'assignment', 'unknown', 'revoke', 'workspace'])
async def test_fresh_system_repair_rechecks_exact_preview_and_current_authority(tmp_path, monkeypatch, boundary):
    app, runtime, now, source, record = await paused(tmp_path, monkeypatch)
    try:
        args = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': record['revision']}
        reviewed = await invoke(app, source, 'schedule.repairPreview', args, 'preview', 'system')
        if boundary == 'stop': source['interruptionRevision'] += 1
        elif boundary == 'configuration': source['selection']['effort'] = 'low'
        elif boundary == 'assignment': app.assignments.blocked = lambda _: 'Assignment suspended.'
        elif boundary == 'unknown':
            app.schedules.store.put_run({'id': 'unknown-run', 'sessionId': source['id'],
                'scheduleId': record['id'], 'dueAt': now[0], 'phase': 'unknown', 'revision': 1})
        elif boundary == 'workspace': source['workingDirectory'] = str(tmp_path / 'missing-workspace')
        else: await mode(app, source, 'ask')
        with pytest.raises(AppError):
            await invoke(app, source, 'schedule.repairResume', {**args, 'previewHash': reviewed['previewHash']}, 'repair', 'system')
        assert app.schedules.store.get(source['id'], record['id']) == record
        assert not runtime.inputs
    finally:
        await app.close()


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_old_native_chain_cannot_relabel_itself_fresh_or_acknowledge_stop(tmp_path, monkeypatch, origin):
    app, runtime, now, source, record = await paused(tmp_path, monkeypatch)
    try:
        args = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': record['revision']}
        reviewed = await invoke(app, source, 'schedule.repairPreview', args, 'fresh-preview', 'system')
        # The trusted envelope still identifies the actual old native generation.
        principal = PRINCIPAL.set(source['id'])
        binding = BINDING.set({'_runtimeSessionId': source['id'], '_generationId': 'system-assigned-generation',
            '_inputBindings': [{'inputId': 'system-assignment'}]})
        try:
            with pytest.raises(AppError, match='generation|Stop|Stopped'):
                await app.dispatch('schedule.repairResume', {**args, 'previewHash': reviewed['previewHash'],
                    'acknowledgeStop': True}, origin=origin, caller_session_id=source['id'], command_id='old-chain')
        finally:
            BINDING.reset(binding)
            PRINCIPAL.reset(principal)
        assert app.schedules.store.get(source['id'], record['id']) == record
        assert not runtime.inputs
    finally:
        await app.close()


async def test_fresh_system_repair_cannot_target_another_owner_or_same_task(tmp_path, monkeypatch):
    app, runtime, now, source, record = await paused(tmp_path, monkeypatch)
    try:
        peer = app._new_session({'title': 'Different owner'})
        app.state['sessions'].append(peer)
        args = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': record['revision']}
        with pytest.raises(AppError):
            await app.dispatch('schedule.repairPreview', args, origin='system', caller_session_id=peer['id'])
        same = copy.deepcopy(record)
        same['destination'] = 'same_task'
        app.schedules.store.put(same)
        with pytest.raises(AppError, match='new-task'):
            await invoke(app, source, 'schedule.repairPreview', args, 'preview', 'system')
        assert not runtime.inputs
    finally:
        await app.close()


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on'])
async def test_fresh_system_repair_still_fences_due_admission(tmp_path, monkeypatch, boundary):
    app, runtime, now, source, record = await paused(tmp_path, monkeypatch)
    try:
        args = {'sessionId': source['id'], 'id': record['id'], 'expectedRevision': record['revision']}
        reviewed = await invoke(app, source, 'schedule.repairPreview', args, 'preview', 'system')
        await invoke(app, source, 'schedule.repairResume', {**args, 'previewHash': reviewed['previewHash']}, 'repair', 'system')
        original = runtime.scheduled_input
        async def held(sid, args, guard):
            if boundary == 'stop': source['interruptionRevision'] += 1
            else:
                await mode(app, source, 'ask')
                if boundary == 'off-on': await mode(app, source)
            return await original(sid, args, guard)
        runtime.scheduled_input = held
        now[0] += 61
        await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'skipped', run
        assert source['status'] == 'stopped'
    finally:
        await app.close()


async def test_fresh_system_can_reconcile_stopped_unknown_without_resuming(tmp_path, monkeypatch):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'same_task')
    try:
        args = await preview(app, source, now[0])
        record = (await invoke(app, source, 'schedule.create', args, 'original'))['schedule']
        runtime.failure = RuntimeError('Outcome was not acknowledged')
        now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(source['id'])[0]
        assert run['phase'] == 'unknown' and len(runtime.inputs) == 1
        source.update(status='stopped', interruptionRevision=1)
        source['collaborationGeneration']['terminal'] = True
        finding = {'sessionId': source['id'], 'runId': run['id'], 'expectedRevision': run['revision'],
            'resolution': 'abandoned', 'evidence': 'Inspected the native outcome; no input replay.'}
        result = await invoke(app, source, 'schedule.reconcile', finding, 'fresh-finding', 'system')
        assert result['run']['phase'] == 'abandoned'
        provenance = result['run']['reconciliation']['provenance']
        assert provenance['origin'] == 'system' and provenance['authority']['interruptionRevision'] == 1
        assert 'generationId' not in provenance['authority'] and 'messageId' not in provenance
        assert result['run']['authorizationCommandId'] == 'original'
        assert app.schedules.store.get(source['id'], record['id'])['status'] == 'needs_review'
        now[0] += 61
        await app.schedules.tick()
        assert len(runtime.inputs) == 1 and source['status'] == 'stopped'
    finally:
        await app.close()


async def test_system_monitor_evidence_is_labeled_as_system_claim(tmp_path, monkeypatch):
    app, runtime, now, source = await setup(tmp_path, monkeypatch, 'same_task')
    try:
        args = await preview(app, source, now[0])
        await invoke(app, source, 'schedule.create', args, 'original')
        now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(source['id'])[0]
        result = await invoke(app, source, 'schedule.report', {'sessionId': source['id'], 'runId': run['id'],
            'expectedRevision': run['revision'], 'outcome': 'unchanged', 'detail': 'Observed no difference.'},
            'system-report', 'system')
        assert result['run']['report']['source'] == 'system_report'
        assert result['run']['phase'] == 'accepted'
    finally:
        await app.close()
