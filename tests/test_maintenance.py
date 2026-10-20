"""Durable maintenance ownership without native continuation implementation stubs."""
import asyncio
import copy
from contextvars import ContextVar
import json
import sqlite3
from types import SimpleNamespace

import pytest

from amplifier_web.maintenance import Maintenance, held, require_open
from amplifier_web.service import AppError, AppService
from amplifier_web.updates import work_paused


class Runtime:
    def __init__(self):
        self.inventory = [{'sessionId': 'root', 'runtimeId': 'root', 'status': 'waiting',
                           'generationId': 'generation', 'inputIds': ['original-input']}]
        self.calls = []
        self.effects = []
        self.pause_wait = None
        self.before_effect = None
        self.lose_resume = False

    def set_maintenance_observer(self, callback): self.observe = callback
    def set_maintenance_authorizer(self, callback): self.authorize = callback
    def set_maintenance_resume_guard(self, callback): self.guard = callback
    def set_maintenance_admission(self, callback): self.admit = callback
    def set_maintenance_loader(self, callback): self.load = callback
    def maintenance_snapshot(self): return copy.deepcopy(self.inventory)

    async def maintenance_pause(self, epoch):
        self.calls.append(('pause', epoch))
        if self.pause_wait:
            await self.pause_wait.wait()
        self.inventory = [{**row, 'epoch': epoch, 'status': 'suspended',
                           'checkpointId': 'checkpoint-' + row['sessionId']} for row in self.inventory]
        return copy.deepcopy(self.inventory)

    async def maintenance_resume(self, epoch, ids):
        self.calls.append(('resume', epoch, ids))
        result = []
        for sid in ids:
            proof = next(row for row in self.inventory if row['sessionId'] == sid)
            source, emit = await self.load(sid)
            assert source['id'] == sid and callable(emit)
            if not await self.authorize(sid, epoch, proof):
                continue
            # Authorizing a root is a once-only handoff, never a reusable grant.
            assert not await self.authorize(sid, epoch, proof)
            if self.before_effect:
                await self.before_effect()
            if not await self.guard(sid, epoch, proof):
                continue
            self.effects.append((sid, proof['checkpointId']))
            if self.lose_resume:
                raise OSError('Lost response after native consumption')
            result.append({**proof, 'status': 'resumed'})
        return result

    async def maintenance_stop(self, epoch):
        self.calls.append(('stop', epoch))
        return [{**row, 'epoch': epoch, 'status': 'stopped'} for row in self.inventory]


class Host:
    def __init__(self, path, runtime=None):
        self.data_dir = path
        self.db = sqlite3.connect(path / 'app.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT, receipt TEXT)')
        self.state = {'sessions': [{'id': 'root', 'workspace': str(path), 'status': 'working',
                                   'execution': {}, 'workers': []}]}
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.instance_id = 'host-instance'
        self.clients = SimpleNamespace(current=ContextVar('maintenance_test_client', default=None))
        self.assignments = SimpleNamespace(current=lambda session: None,
                                           admission=lambda session, row: {'allowed': True})
        self.tasks = set()
        self.published = []
        self.maintenance = Maintenance(self)

    def _session(self, sid):
        return next(row for row in self.state['sessions'] if row['id'] == sid)

    def _publish_changes(self, **kwargs): self.published.append(kwargs)
    async def on_runtime_event(self, event, payload): pass

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def settled(self):
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        self.db.close()


async def command(host, action, args=None, identity=None, origin='ui'):
    return await host.maintenance.dispatch('maintenance.' + action, args or {}, origin, identity)


async def paused(host):
    receipt = await command(host, 'pause', identity='pause')
    await host.settled()
    return receipt['result']['epoch']


async def test_hold_is_durable_before_async_pause_and_duplicate_is_passive(tmp_path):
    runtime = Runtime()
    runtime.pause_wait = asyncio.Event()
    host = Host(tmp_path, runtime)
    try:
        first = await command(host, 'pause', identity='pause')
        assert held(host) and runtime.calls == []
        with sqlite3.connect(tmp_path / 'app.sqlite3') as read:
            assert json.loads(read.execute('SELECT value FROM host_maintenance').fetchone()[0])['held']
        with pytest.raises(AppError) as blocked:
            runtime.admit('root', 'send')
        assert blocked.value.code == 'maintenance_paused'
        again = await command(host, 'pause', identity='pause')
        assert again['duplicate'] and again['result'] == first['result']
        runtime.pause_wait.set()
        await host.settled()
        assert len(runtime.calls) == 1
        assert host.maintenance.snapshot()['readyForRestart']
        assert host.maintenance.snapshot()['roots'][0]['proof']['inputIds'] == ['original-input']
    finally:
        await host.close()


async def test_restart_retains_pause_and_explicit_resume_consumes_once(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    epoch = await paused(host)
    before = copy.deepcopy(host.state['sessions'])
    await host.close()
    other = Host(tmp_path, runtime)
    try:
        assert held(other) and len(runtime.calls) == 1
        assert other.state['sessions'] == before
        await command(other, 'resume', {'epoch': epoch}, 'resume')
        await other.settled()
        assert runtime.effects == [('root', 'checkpoint-root')]
        assert not held(other)
        count = len(runtime.calls)
        assert (await command(other, 'resume', {'epoch': epoch}, 'resume'))['duplicate']
        assert len(runtime.calls) == count
        with pytest.raises(AppError, match='epoch changed'):
            await command(other, 'resume', {'epoch': epoch}, 'new-command')
        assert other.state['sessions'] == before
    finally:
        await other.close()


async def test_remount_checkpoint_observation_preserves_unconsumed_resume_intent(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        epoch = await paused(host)
        original = runtime.maintenance_resume
        async def remount_then_resume(epoch, identities):
            proof = runtime.inventory[0]
            assert host.maintenance._row('root')['phase'] == 'resume-requested'
            assert await host.maintenance.observe(proof)
            assert host.maintenance._row('root')['phase'] == 'resume-requested'
            assert not await host.maintenance.observe({**proof, 'checkpointId': 'replacement'})
            return await original(epoch, identities)
        runtime.maintenance_resume = remount_then_resume
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert runtime.effects == [('root', 'checkpoint-root')]
        assert host.maintenance._row('root')['phase'] == 'resumed'
    finally:
        await host.close()


@pytest.mark.parametrize('change', ['stop', 'child_stop', 'placement', 'edit', 'configuration',
                                  'task', 'ownership', 'assignment', 'budget'])
async def test_current_authority_wins_resume(tmp_path, change):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        epoch = await paused(host)
        session = host._session('root')
        if change == 'stop': session['interruptionRevision'] = 1
        elif change == 'child_stop': session['workers'] = [{'id': 'child', 'interruptionRevision': 1}]
        elif change == 'placement': session['executionRevision'] = 1
        elif change == 'edit': session['historyEdit'] = {'operationId': 'new-edit'}
        elif change == 'configuration': host.state['configurationRevision'] = 1
        elif change == 'ownership': session['ownership'] = {'status': 'yielded'}
        elif change == 'task':
            path = tmp_path / 'sessions/root/control-state.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'task': {'id': 'new-task', 'revision': 1, 'status': 'paused'}}))
        elif change == 'assignment':
            host.assignments.admission = lambda session, row: {'allowed': False, 'reasons': ['Assignment stopped']}
        elif change == 'budget':
            path = tmp_path / 'sessions/root/control-state.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'capacity': {'revision': 1, 'enabled': True, 'maxTotalTokens': 1},
                'capacityLastDenial': {'budgetRevision': 1, 'reasons': ['Budget exhausted']}}))
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert not runtime.effects
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'blocked'
    finally:
        await host.close()


async def test_later_stop_after_resume_claim_blocks_native_effect(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        epoch = await paused(host)
        async def stop():
            async with host.lock:
                host.maintenance.invalidate('root', 'Later Stop')
                host._session('root')['interruptionRevision'] = 1
                host.db.commit()
        runtime.before_effect = stop
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert not runtime.effects
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'blocked'
    finally:
        await host.close()


async def test_lost_resume_ack_never_returns_claim_to_resumable(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        epoch = await paused(host)
        runtime.lose_resume = True
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert held(host) and len(runtime.effects) == 1
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'uncertain'
        await command(host, 'resume', {'epoch': epoch}, 'explicit-reopen')
        await host.settled()
        assert not held(host) and len(runtime.effects) == 1
    finally:
        await host.close()


async def test_stale_proof_and_unproven_suspension_cannot_be_adopted(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        await paused(host)
        proof = host.maintenance.snapshot()['roots'][0]['proof']
        for patch in ({'epoch': 'old'}, {'inputIds': ['another-input']}, {'runtimeId': 'another-root'},
                      {'checkpointId': 'replacement'}):
            assert not await host.maintenance.observe({**proof, **patch})
        assert host.maintenance.snapshot()['roots'][0]['proof'] == proof
        assert not await host.maintenance.authorize_resume('root', proof['epoch'], proof)
    finally:
        await host.close()


async def test_emergency_stop_requires_current_host_target_and_keeps_hold(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        preview = (await command(host, 'stopPreview'))['result']
        args = {key: preview[key] for key in ('hostInstanceId', 'targetDigest')} | {'confirm': True}
        with pytest.raises(AppError, match='Review and confirm'):
            await command(host, 'stopNow', {**args, 'hostInstanceId': 'old-host'}, 'stale')
        assert not runtime.calls and not held(host)
        await command(host, 'stopNow', args, 'stop')
        await host.settled()
        assert held(host) and [row[0] for row in runtime.calls] == ['stop']
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'stopped'
        assert (await command(host, 'stopNow', args, 'stop'))['duplicate']
        assert len(runtime.calls) == 1
    finally:
        await host.close()


async def test_unsupported_runtime_never_claims_restart_readiness(tmp_path):
    host = Host(tmp_path, SimpleNamespace())
    try:
        await paused(host)
        assert held(host)
        assert not host.maintenance.snapshot()['inventoryComplete']
        assert not host.maintenance.snapshot()['readyForRestart']
    finally:
        await host.close()


async def test_nonhuman_and_missing_command_identity_cannot_change_host(tmp_path):
    host = Host(tmp_path, Runtime())
    try:
        for origin in ('agent', 'peer', 'scheduler'):
            with pytest.raises(AppError, match='explicit human'):
                await command(host, 'pause', identity=origin, origin=origin)
        with pytest.raises(AppError, match='stable command identity'):
            await command(host, 'pause')
        assert not held(host)
    finally:
        await host.close()


async def test_service_holds_new_send_and_creation_but_preserves_existing_receipt(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        created = await app.dispatch('session.create', {'title': 'Existing'}, command_id='existing')
        await app.dispatch('maintenance.pause', {}, command_id='pause')
        assert (await app.dispatch('session.create', {'title': 'Existing'}, command_id='existing'))['duplicate']
        with pytest.raises(AppError) as blocked:
            await app.dispatch('session.create', {'title': 'Must not create'}, command_id='new')
        assert blocked.value.code == 'maintenance_paused'
        with pytest.raises(AppError) as blocked:
            await app.dispatch('conversation.send', {'sessionId': created['sessionId'], 'text': 'Must not send'}, command_id='send')
        assert blocked.value.code == 'maintenance_paused'
        assert not app.db.execute('SELECT 1 FROM commands WHERE id IN (?,?)', ('new', 'send')).fetchone()
    finally:
        await app.close()


async def test_pause_overtakes_queued_history_read_before_send_admission(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    waiting, release = asyncio.Event(), asyncio.Event()
    pending = None
    try:
        created = await app.dispatch('session.create', {}, command_id='existing')
        original = app.history.ensure_loaded
        async def held_history(sid):
            waiting.set()
            await release.wait()
            return await original(sid)
        monkeypatch.setattr(app.history, 'ensure_loaded', held_history)
        pending = asyncio.create_task(app.dispatch('conversation.send', {
            'sessionId': created['sessionId'], 'text': 'Do not admit this queued input',
        }, command_id='queued'))
        await waiting.wait()
        await app.dispatch('maintenance.pause', {}, command_id='pause')
        release.set()
        with pytest.raises(AppError) as rejected:
            await pending
        assert rejected.value.code == 'maintenance_paused'
        assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', ('queued',)).fetchone()
        assert not app._session(created['sessionId'])['messages']
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await app.close()


async def test_emergency_stop_overtakes_waiting_pause_without_reviving_it(tmp_path):
    runtime = Runtime()
    runtime.pause_wait = asyncio.Event()
    host = Host(tmp_path, runtime)
    try:
        await command(host, 'pause', identity='pause')
        await asyncio.sleep(0)
        preview = (await command(host, 'stopPreview'))['result']
        args = {key: preview[key] for key in ('hostInstanceId', 'targetDigest')} | {'confirm': True}
        await command(host, 'stopNow', args, 'stop')
        await asyncio.sleep(0)
        runtime.pause_wait.set()
        await host.settled()
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'stopped'
        assert host.maintenance.value['phase'] == 'stopping' and held(host)
        assert not runtime.effects
    finally:
        await host.close()


class FailedWrite:
    """A storage failure at a selected real SQLite transaction boundary."""
    def __init__(self, db, stage):
        self.db, self.stage = db, stage

    def execute(self, sql, *args):
        if self.stage == 'capture' and sql.startswith('INSERT OR REPLACE INTO maintenance_roots'):
            raise sqlite3.OperationalError('disk full during capture')
        return self.db.execute(sql, *args)

    def commit(self):
        if self.stage == 'commit':
            raise sqlite3.OperationalError('disk full during commit')
        return self.db.commit()

    def close(self): self.db.close()


@pytest.mark.parametrize('stage', ['capture', 'commit'])
async def test_failed_pause_storage_holds_all_admission_without_runtime_effect(tmp_path, stage):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    host.db = FailedWrite(host.db, stage)
    try:
        with pytest.raises(sqlite3.OperationalError):
            await command(host, 'pause', identity='pause')
        assert held(host) and work_paused(host.state)
        assert not host.state['hostMaintenance']['readyForRestart']
        assert not runtime.calls and not host.tasks
    finally:
        await host.close()


async def test_failed_reopen_commit_cannot_open_any_admission_reader(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    try:
        await paused(host)
        host.db = FailedWrite(host.db, 'commit')
        host.maintenance.value.update(held=False, phase='open')
        with pytest.raises(sqlite3.OperationalError):
            host.maintenance._commit()
        assert held(host) and work_paused(host.state)
        assert not host.state['hostMaintenance']['readyForRestart']
    finally:
        await host.close()


async def test_no_runtime_no_work_can_pause_without_fabricated_continuation(tmp_path):
    host = Host(tmp_path)
    try:
        epoch = await paused(host)
        assert host.maintenance.snapshot()['readyForRestart']
        assert not host.maintenance.snapshot()['roots']
        host.state['maintenance'] = {'backup': 'unrelated existing maintenance result'}
        assert work_paused(host.state)
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert not held(host)
        assert host.state['maintenance']['backup']
    finally:
        await host.close()


async def test_schedule_worker_guard_honors_pause_after_preparation(tmp_path, monkeypatch):
    from test_schedules import fixture, schedule
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await schedule(app, sid, now[0])
        runtime.before_admission = lambda: app.maintenance._hold(epoch='pause', phase='paused')
        now[0] += 61
        await app.schedules.tick()
        assert not runtime.inputs
        assert app.schedules.store.runs(sid)[0]['phase'] == 'skipped'
    finally:
        await app.close()


@pytest.mark.parametrize('tick_delay', [0, 180])
async def test_resume_skips_missed_latest_schedule_without_disabling_future_runs(tmp_path, monkeypatch, tick_delay):
    from test_schedules import fixture, schedule
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        saved = await schedule(app, sid, now[0])
        now[0] += 61
        app.maintenance._hold(epoch='pause', phase='paused')
        await app.schedules.tick()
        assert not app.schedules.store.runs(sid)
        app.maintenance.value.update(held=False, phase='open', lastResumedAt=now[0])
        app.maintenance._commit()
        now[0] += tick_delay
        await app.schedules.tick()
        assert not runtime.inputs
        assert app.schedules.store.runs(sid)[0]['phase'] == 'skipped'
        current = app.schedules.store.get(sid, saved['id'])
        assert current['status'] == 'active'
        if not tick_delay:
            now[0] += 60
        await app.schedules.tick()
        assert len(runtime.inputs) == 1
    finally:
        await app.close()


def background_proofs(runtime, epoch):
    origin = {'generationId': 'generation', 'inputIds': ['original-input']}
    root = copy.deepcopy(runtime.inventory[0])
    root.update(ownedGenerations=[origin], jobOrigins=[{**origin, 'jobId': 'job', 'status': 'running'}])
    root['children'] = [{'sessionId': 'child', 'runtimeId': 'child', 'status': 'waiting',
                         'generationId': 'child-generation', 'inputIds': ['child-input'],
                         'parentSessionId': 'root', 'callId': 'call'}]
    suspended = copy.deepcopy(root)
    suspended.update(epoch=epoch, generationId=None, status='suspended',
                     checkpointId='checkpoint-root', rootGenerationTerminal=origin)
    suspended['jobOrigins'][0]['status'] = 'suspended'
    suspended['children'][0].update(status='suspended', checkpointId='checkpoint-child')
    return root, suspended


@pytest.mark.parametrize('finite_child', [False, True])
async def test_terminal_root_with_exact_owned_background_job_can_resume(tmp_path, finite_child):
    runtime = Runtime()
    initial, _ = background_proofs(runtime, 'unused')
    if finite_child:
        initial['children'][0].update(generationId=None, inputIds=[])
    runtime.inventory = [initial]
    host = Host(tmp_path, runtime)
    try:
        async def pause(epoch):
            runtime.calls.append(('pause', epoch))
            _, proof = background_proofs(runtime, epoch)
            if finite_child:
                proof['children'][0].update(generationId=None, inputIds=[])
            runtime.inventory = [proof]
            return [proof]
        runtime.maintenance_pause = pause
        epoch = await paused(host)
        saved = host.maintenance.snapshot()['roots'][0]
        assert saved['phase'] == 'suspended' and saved['proof'].get('generationId') is None
        assert host.maintenance.snapshot()['readyForRestart']
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert runtime.effects == [('root', 'checkpoint-root')]
    finally:
        await host.close()


@pytest.mark.parametrize('change', ['no_terminal', 'substitute_job', 'substitute_origin',
                                  'unfinished_child', 'missing_child_checkpoint'])
async def test_background_completion_cannot_weaken_generation_or_child_fences(tmp_path, change):
    runtime = Runtime()
    initial, _ = background_proofs(runtime, 'unused')
    runtime.inventory = [initial]
    runtime.pause_wait = asyncio.Event()
    host = Host(tmp_path, runtime)
    try:
        result = await command(host, 'pause', identity='pause')
        _, proof = background_proofs(runtime, result['result']['epoch'])
        if change == 'no_terminal': proof.pop('rootGenerationTerminal')
        elif change == 'substitute_job': proof['jobOrigins'][0]['jobId'] = 'another-job'
        elif change == 'substitute_origin':
            proof['inputIds'] = ['different-input']
            proof['ownedGenerations'][0]['inputIds'] = ['different-input']
            proof['jobOrigins'][0]['inputIds'] = ['different-input']
            proof['rootGenerationTerminal']['inputIds'] = ['different-input']
        elif change == 'unfinished_child': proof['children'][0]['status'] = 'waiting'
        elif change == 'missing_child_checkpoint': proof['children'][0].pop('checkpointId')
        await host.maintenance.observe(proof)
        assert not host.maintenance.snapshot()['readyForRestart']
        assert host.maintenance.snapshot()['roots'][0]['phase'] != 'suspended'
        assert not runtime.effects
    finally:
        await host.close()


async def test_pending_input_ids_are_preserved_without_inventing_a_generation(tmp_path):
    runtime = Runtime()
    runtime.inventory = [{
        'sessionId': 'root', 'runtimeId': 'root', 'status': 'waiting',
        'generationId': None, 'inputIds': ['accepted-queued'],
        'ownedGenerations': [], 'pendingInputIds': ['accepted-queued'],
        'rootGenerationTerminal': None, 'jobOrigins': [],
    }]
    host = Host(tmp_path, runtime)
    try:
        epoch = await paused(host)
        proof = host.maintenance.snapshot()['roots'][0]['proof']
        assert proof['pendingInputIds'] == ['accepted-queued']
        changed = {**proof, 'inputIds': ['replacement'], 'pendingInputIds': ['replacement']}
        assert not await host.maintenance.observe(changed)
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert runtime.effects == [('root', 'checkpoint-root')]
    finally:
        await host.close()


async def test_restart_does_not_replay_an_unconfirmed_consumed_resume_claim(tmp_path):
    runtime = Runtime()
    host = Host(tmp_path, runtime)
    epoch = await paused(host)
    async with host.lock:
        row = host.maintenance._row('root')
        row.update(phase='resuming', resumeCommandId='prior-process-command')
        host.maintenance._put(row)
        host.maintenance.value['phase'] = 'resuming'
        host.maintenance._commit()
    await host.close()
    restored = Host(tmp_path, runtime)
    try:
        assert held(restored)
        assert restored.maintenance.value['phase'] == 'uncertain'
        assert restored.maintenance.snapshot()['roots'][0]['phase'] == 'uncertain'
        await command(restored, 'resume', {'epoch': epoch}, 'reopen-only')
        await restored.settled()
        assert not held(restored) and not runtime.effects
    finally:
        await restored.close()


async def test_accepted_voice_receipt_stays_readable_while_new_voice_is_held(tmp_path):
    from test_service import Runtime as VoiceRuntime
    runtime = VoiceRuntime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app._session()['id']
        await app.voice_delegate('One original operation', 'voice:once', sid)
        await asyncio.gather(*tuple(app.tasks))
        sent = copy.deepcopy(runtime.sent)
        await app.dispatch('maintenance.pause', {}, command_id='pause')
        assert (await app.voice_delegate('One original operation', 'voice:once', sid))['duplicate']
        with pytest.raises(AppError) as blocked:
            await app.voice_delegate('New operation', 'voice:new', sid)
        assert blocked.value.code == 'maintenance_paused'
        assert runtime.sent == sent
    finally:
        await app.close()


async def test_admitted_root_finishing_during_pause_is_quiescent_not_resumable(tmp_path):
    runtime = Runtime()
    origin = {'generationId': 'generation', 'inputIds': ['original-input']}
    runtime.inventory[0]['ownedGenerations'] = [origin]
    host = Host(tmp_path, runtime)
    try:
        async def pause(epoch):
            return [{**runtime.inventory[0], 'epoch': epoch, 'generationId': None,
                     'status': 'quiescent', 'rootGenerationTerminal': origin}]
        runtime.maintenance_pause = pause
        epoch = await paused(host)
        assert host.maintenance.snapshot()['readyForRestart']
        assert host.maintenance.snapshot()['roots'][0]['phase'] == 'quiescent'
        await command(host, 'resume', {'epoch': epoch}, 'resume')
        await host.settled()
        assert not held(host) and not runtime.effects
    finally:
        await host.close()
