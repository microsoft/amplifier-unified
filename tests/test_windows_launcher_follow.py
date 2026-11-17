"""Portable host protocol checks. No Scheduler, registry, process or network."""
import asyncio
import base64
import copy
from contextlib import contextmanager
import os
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import amplifier_web
from amplifier_web import update_drain
from amplifier_web import windows_launcher_follow as follow


class Refused(RuntimeError):
    pass


class Manager:
    def __init__(self, home):
        self.home = home
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.closed = False
        self.check_task = self.readiness_task = None
        self.busy = False
        self.published = []
        self.saved = []
        self.service = SimpleNamespace(data_dir=home, lock=asyncio.Lock(), instance_id='host',
            state={'settings': {'updates': {'autoCheck': True, 'autoInstall': True}},
                'updates': {'phase': 'idle', 'currentStatus': self.health()}},
            _save_changes=lambda **kw: self.saved.append(copy.deepcopy(self.state)))

    @staticmethod
    def health(status='current'):
        return {'status': status, 'title': 'Health title', 'detail': 'Health detail.',
            'actionRequired': status == 'failed', 'action': None, 'component': 'health'}

    @property
    def state(self):
        return self.service.state['updates']

    async def publish(self, **values):
        async with self.service.lock:
            self.state.update(copy.deepcopy(values))
            self.state['currentStatus'] = self.health(self.state.get('healthStatus', 'current'))
            follow.project(self)
            self.published.append(copy.deepcopy(values))

    def app_busy(self):
        return self.busy

    @staticmethod
    def independent_tool_results(state):
        return all(row.get('failure') or row.get('runnerSource') for row in state['pendingSmartTools'])


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(follow, '_WINDOWS', True)
    monkeypatch.setattr(follow, '_POLL_SECONDS', 0)
    now = [1000.]
    monkeypatch.setattr(follow, 'time', SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))
    manager = Manager(tmp_path)
    spec = SimpleNamespace(registration_id='registration', python=str(tmp_path / 'launcher/Scripts/pythonw.exe'))
    run = {'schema': 1, 'registration': 'registration', 'run': 'host-run',
        'pid': os.getpid(), 'created': 123., 'image': 'app-python'}
    target = {'version': '2.0.0', 'revision': 'a' * 40, 'generation': 'b' * 32,
        'folder': tmp_path / 'generation', 'prefix': tmp_path / 'generation/tools/amplifier-unified'}
    target['folder'].mkdir()
    (target['folder'] / 'validated.json').write_text('{"version": "2.0.0"}')
    records, calls, helper_lock = {}, [], [False]
    config = SimpleNamespace(owner=spec, target=target, old_version='1.0.0', differs=True,
        recovery={'status': 'acknowledged', 'restart': {'target': target}}, layout=None,
        task_running=False, remove=True, stop=None, plan='refresh', task_error=False, shell_error=False)

    def read(home, name):
        calls.append(('read', name))
        return copy.deepcopy(records.get(name))

    def write(home, name, value):
        calls.append(('write', name, copy.deepcopy(value), update_drain.held(manager.service)))
        records[name] = copy.deepcopy(value)

    @contextmanager
    def lock(home):
        if helper_lock[0]:
            yield False
        else:
            helper_lock[0] = True
            try:
                yield True
            finally:
                helper_lock[0] = False

    def journal_kind(home, registration):
        journal = records.get('journal')
        if not journal or journal.get('done'):
            return 'none'
        if 'registration' not in journal:
            return 'manual'
        return 'live' if journal['registration'] == registration else 'stale'

    def stale(home):
        calls.append(('stale', helper_lock[0]))
        records['stale'] = records.pop('journal')
        return 'stale'

    def started(home, request):
        record = records.get('record', {})
        return journal_kind(home, request['registration']) == 'live' or (
            record.get('request') == request['id'] and record.get('state') in {'stopped', 'waiting'})

    def plan(service, home):
        calls.append(('plan', follow.fenced(manager)))
        if isinstance(config.plan, Exception):
            raise config.plan
        return {'action': config.plan}

    def ensure(*args):
        calls.append(('ensure', *args))
        if any(row[0] == 'plan' for row in calls):
            assert manager.state['launcherRefresh']['attempt']
        if config.task_error:
            raise Refused('foreign task')

    def start(home):
        calls.append(('start',))

    def remove(home):
        calls.append(('remove', helper_lock[0]))
        return config.remove

    def owner(home):
        calls.append(('owner',))
        return config.owner

    def main_read(home):
        calls.append(('main',))
        return {}, config.owner

    def shell(operation, *args, **kwargs):
        calls.append((operation, *args))
        if config.shell_error:
            raise PermissionError()
        return {'status': 'settled'}

    refresh = SimpleNamespace(REQUEST='request', JOURNAL='journal', RECORD='record', Refused=Refused,
        read_json=read, write_json=write, try_lock=lock, journal_kind=journal_kind, set_stale_aside=stale,
        started=started, run_identity=lambda row: copy.deepcopy(row), plan=plan, TaskService=lambda home: spec,
        launcher_facts=lambda prefix: {'version': config.old_version,
            'identity': {'prefix': str(prefix), 'digest': 'digest'}},
        _consumer=lambda *a: config.layout)
    modules = {
        'windows_launcher_refresh': refresh,
        'windows_launcher_task': SimpleNamespace(ensure=ensure, start=start, running=lambda home: config.task_running,
            remove_task=remove),
        'windows_service': SimpleNamespace(_read=main_read, update_owner=owner),
        'windows_task_runtime': SimpleNamespace(read=lambda spec: copy.deepcopy(run),
            stop_requested=lambda spec, row, name='stop': config.stop == name, RECOVERY_STOP='recovery-stop'),
        'windows_app_recovery': SimpleNamespace(read=lambda spec: config.recovery),
        'application_generations': SimpleNamespace(active=lambda home: config.target),
        'windows_launcher_files': SimpleNamespace(differs=lambda *a: {'differs': config.differs}),
        'windows_consumer_shell': SimpleNamespace(settle_version=lambda *a, **kw: shell('settle', *a, **kw),
            set_version=lambda *a, **kw: shell('version', *a, **kw)),
    }
    for name, module in modules.items():
        monkeypatch.setattr(amplifier_web, name, module, raising=False)
        monkeypatch.setitem(sys.modules, 'amplifier_web.' + name, module)

    released = []

    async def finish(m, reason):
        released.extend(copy.deepcopy((m.state.get('drain') or {}).get('messages', [])))
        m.state['drain']['phase'] = 'released'
        calls.append(('release', reason, helper_lock[0]))

    monkeypatch.setattr(update_drain, 'finish', finish)
    follow.at_start(manager)
    return SimpleNamespace(m=manager, spec=spec, run=run, target=target, records=records, calls=calls,
        config=config, lock=helper_lock, refresh=refresh, now=now, released=released)


def saved(rig):
    return rig.m.state.get('launcherRefresh') or {}


def request(rig, *, started=False, mine=False, opened=True):
    m = rig.m
    value = {'schema': 1, 'id': 'c' * 32, 'registration': rig.spec.registration_id,
        'launcher': {'prefix': 'launcher', 'digest': 'old'}, 'target': follow._target(rig.target),
        'status': 'open', 'result': None, 'hold': rig.run if mine else None, 'repair': False, 'at': rig.now[0]}
    rig.records['request'] = value
    f = m._launcher_follow
    f.request, f.fence, f.opened_here = copy.deepcopy(value), True, opened
    f.spec, f.run, f.phase = rig.spec, copy.deepcopy(rig.run), 'waiting'
    if mine:
        follow._hold(m, f)
    if started:
        rig.records['journal'] = {'registration': rig.spec.registration_id, 'request': value['id'],
            'old': value['launcher'], 'pointer': base64.b64encode(
                json.dumps(follow._target(rig.target)).encode()).decode()}
    return value


def record(rig, state, **values):
    rig.records['record'] = {'request': rig.records['request']['id'], 'state': state,
        'run': copy.deepcopy(rig.run), **values}


async def two_passes(rig):
    await follow.consider(rig.m)
    await follow.consider(rig.m)


@pytest.mark.asyncio
async def test_begin_fences_before_plan_and_saves_attempt_before_external_effects(rig):
    await two_passes(rig)
    assert ('plan', True) in rig.calls
    assert follow.fenced(rig.m)
    assert rig.records['request']['hold'] is None
    assert saved(rig)['attempt']['result'] is None
    assert saved(rig)['attempt']['generation'] == rig.target['generation']
    assert any(row[0] == 'start' for row in rig.calls)
    assert not update_drain.held(rig.m.service)
    assert rig.m._launcher_follow.owner_tries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['owner', 'target', 'equal-version', 'older-app', 'files',
    'no-confirmation', 'prepared', 'restoring', 'restored', 'wrong-revision', 'wrong-generation',
    'autoCheck', 'autoInstall', 'counted', 'journal', 'manual-journal', 'retryAfter', 'wrong-requested'])
async def test_each_offer_condition_blocks_begin(rig, condition):
    c = rig.config
    if condition == 'owner':
        c.owner = None
    elif condition == 'target':
        c.target = None
    elif condition in {'equal-version', 'older-app'}:
        c.old_version = '2.0.0' if condition == 'equal-version' else '3.0.0'
    elif condition == 'files':
        c.differs = False
    elif condition == 'no-confirmation':
        c.recovery = None
    elif condition in {'prepared', 'restoring', 'restored'}:
        c.recovery = {**c.recovery, 'status': condition}
    elif condition in {'wrong-revision', 'wrong-generation'}:
        key = condition[6:]
        c.recovery = {'status': 'acknowledged', 'restart': {'target': {**rig.target, key: 'other'}}}
    elif condition in {'autoCheck', 'autoInstall', 'wrong-requested'}:
        rig.m.service.state['settings']['updates']['autoInstall' if condition == 'wrong-requested' else condition] = False
        if condition == 'wrong-requested':
            rig.m.state['launcherRefresh'] = {'requested': {'generation': 'other'}}
    elif condition == 'counted':
        rig.m.state['launcherRefresh'] = {'attempt': {'generation': rig.target['generation'], 'result': 'refused'}}
    elif condition in {'journal', 'manual-journal'}:
        rig.records['journal'] = {} if condition == 'manual-journal' else {'registration': rig.spec.registration_id}
        rig.records['journal']['done'] = False
    elif condition == 'retryAfter':
        rig.m.state['launcherRefresh'] = {'retryAfter': rig.now[0] + 1}
    await two_passes(rig)
    assert 'request' not in rig.records
    assert not any(row[0] == 'plan' for row in rig.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['busy', 'lock', 'check', 'readiness', 'checking', 'staging',
    'validating', 'staged', 'app-staged', 'activating', 'pendingApp', 'pendingRelease', 'pendingRestart',
    'pendingRollback', 'pendingReplacement', 'pendingSmartTools', 'waiting', 'draining', 'replacing', 'releasing'])
async def test_each_idle_condition_blocks_begin(rig, condition):
    m = rig.m
    if condition == 'busy':
        m.busy = True
    elif condition == 'lock':
        await m.lock.acquire()
    elif condition in {'check', 'readiness'}:
        setattr(m, condition + '_task', SimpleNamespace(done=lambda: False))
    elif condition.startswith('pending'):
        m.state[condition] = [{}] if condition == 'pendingSmartTools' else {'pending': True}
    elif condition in {'waiting', 'draining', 'replacing', 'releasing'}:
        m.state['drain'] = {'phase': condition}
    else:
        m.state['phase'] = condition
    await two_passes(rig)
    assert 'request' not in rig.records
    assert not follow.fenced(m)
    if condition == 'lock':
        m.lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['phase', 'stage', 'nextStage', 'lastCheck', 'lastAttempt', 'installedAt'])
async def test_changed_snapshot_requires_another_pass(rig, field):
    await follow.consider(rig.m)
    if field in {'stage', 'nextStage'}:
        rig.m.state['sequence'] = {field: 'changed'}
    else:
        rig.m.state[field] = 'changed'
    await follow.consider(rig.m)
    assert 'request' not in rig.records
    await follow.consider(rig.m)
    assert 'request' in rig.records


@pytest.mark.asyncio
async def test_failed_tool_results_do_not_block_and_stable_phase_is_not_republished(rig):
    rig.m.busy = True
    rig.m.state['pendingSmartTools'] = [{'failure': True}]
    await follow.consider(rig.m)
    count = len(rig.m.published)
    await follow.consider(rig.m)
    assert len(rig.m.published) == count
    rig.m.busy = False
    await follow.consider(rig.m)
    assert 'request' in rig.records


@pytest.mark.asyncio
async def test_owner_is_retried_ten_times_but_fixed_facts_are_read_once(rig):
    rig.config.owner = None
    for _ in range(12):
        await follow.consider(rig.m)
    assert rig.calls.count(('owner',)) == 10
    assert not rig.m._launcher_follow.facts


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['none', 'refused', 'task'])
async def test_plan_and_task_refusals_count_once_and_unfence(rig, outcome):
    if outcome == 'task':
        rig.config.task_error = True
    else:
        rig.config.plan = Refused() if outcome == 'refused' else 'none'
    await two_passes(rig)
    assert saved(rig)['attempt']['result'] == 'refused'
    assert saved(rig)['attempt']['step'] == ('task' if outcome == 'task' else None)
    assert not follow.fenced(rig.m)
    count = sum(row[0] == 'plan' for row in rig.calls)
    await follow.consider(rig.m)
    assert sum(row[0] == 'plan' for row in rig.calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['busy', 'setting'])
async def test_go_withdraws_without_count_and_backs_off_even_when_helper_holds_lock(rig, reason):
    await two_passes(rig)
    record(rig, 'ready')
    if reason == 'busy':
        rig.m.busy = True
    else:
        rig.m.service.state['settings']['updates']['autoInstall'] = False
    rig.lock[0] = True
    await follow.consider(rig.m)
    assert rig.records['request']['result'] == 'withdrawn'
    assert rig.records['request']['hold'] is None
    assert follow.fenced(rig.m)
    rig.lock[0] = False
    await follow.consider(rig.m)
    assert saved(rig)['attempt']['result'] == 'withdrawn'
    assert saved(rig)['retryAfter'] == rig.now[0] + 300
    assert not follow.fenced(rig.m)
    rig.m.busy = False
    rig.m.service.state['settings']['updates']['autoInstall'] = True
    await two_passes(rig)
    assert rig.records['request']['status'] == 'closed'
    rig.now[0] += 300
    await follow.consider(rig.m)
    assert rig.records['request']['status'] == 'open'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong', ['request', 'run', 'pid', 'created', 'image', 'registration'])
async def test_go_requires_ready_for_exact_request_and_run(rig, wrong):
    await two_passes(rig)
    record(rig, 'ready')
    if wrong == 'request':
        rig.records['record']['request'] = 'other'
    else:
        rig.records['record']['run'][wrong] = 'other'
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] is None
    assert not update_drain.held(rig.m.service)


@pytest.mark.asyncio
async def test_go_closes_admission_and_publishes_before_hold(rig):
    await two_passes(rig)
    record(rig, 'ready')
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] == rig.run
    assert saved(rig)['phase'] == 'restarting'
    write = [row for row in rig.calls if row[0] == 'write'][-1]
    assert write[3] is True
    assert follow.fenced(rig.m)


def test_at_start_without_request_reads_only_two_small_files(rig):
    assert rig.calls == [('read', 'request'), ('read', 'journal')]
    assert not rig.m.saved


@pytest.mark.parametrize('status', ['starting', 'other-process'])
def test_at_start_requires_this_task_process(rig, status):
    request(rig, started=True)
    if status == 'other-process':
        rig.run['pid'] = -1
    else:
        rig.config.owner = None
    follow.at_start(rig.m)
    assert not follow.fenced(rig.m)
    assert rig.records['request']['hold'] is None


@pytest.mark.parametrize('setting', [True, False])
def test_at_start_started_holds_before_save_even_with_setting_off(rig, setting):
    request(rig, started=True)
    rig.m.service.state['settings']['updates']['autoInstall'] = setting
    follow.at_start(rig.m)
    assert follow.fenced(rig.m)
    assert rig.records['request']['hold'] == rig.run
    assert rig.m.saved[-1]['drain']['phase'] == 'replacing'
    assert [row for row in rig.calls if row[0] == 'write'][-1][3]
    assert saved(rig)['phase'] == 'finishing'


@pytest.mark.asyncio
async def test_start_in_preparation_releases_inherited_hold_and_saved_message(rig):
    value = request(rig)
    rig.m.state['drain'] = {'launcher': 'launcher-' + value['id'], 'phase': 'replacing',
        'messages': [['chat', 'message']], 'workers': {}}
    rig.m.service.state['settings']['updates']['autoCheck'] = False
    follow.at_start(rig.m)
    assert rig.records['request']['hold'] is None
    assert follow.fenced(rig.m) and update_drain.held(rig.m.service)
    await follow.consider(rig.m)
    assert rig.records['request']['result'] == 'withdrawn'
    assert rig.released == [['chat', 'message']]
    assert not follow.fenced(rig.m)
    assert saved(rig)['retryAfter'] == rig.now[0] + 300


@pytest.mark.parametrize('status', ['prepared', 'restoring'])
def test_start_during_handover_never_holds(rig, status):
    request(rig, started=True)
    rig.config.recovery = {'status': status}
    follow.at_start(rig.m)
    assert follow.fenced(rig.m)
    assert rig.records['request']['hold'] is None
    assert not update_drain.held(rig.m.service)


def test_void_request_is_closed(rig):
    request(rig)
    rig.records['request']['registration'] = 'previous-install'
    follow.at_start(rig.m)
    assert rig.records['request']['status'] == 'closed'
    assert rig.records['request']['result'] == 'void'
    assert not follow.fenced(rig.m)


@pytest.mark.parametrize('locked', [True, False])
def test_stale_journal_is_only_set_aside_under_free_lock(rig, locked):
    request(rig, started=True)
    rig.records['journal']['registration'] = 'previous-install'
    rig.lock[0] = locked
    follow.at_start(rig.m)
    assert ('stale' in rig.records) is not locked
    if not locked:
        assert ('stale', True) in rig.calls
    assert rig.records['request']['hold'] is None


def test_orphan_automatic_journal_opens_repair_but_manual_journal_does_not(rig):
    value = request(rig, started=True)
    rig.records.pop('request')
    follow.at_start(rig.m)
    assert rig.records['request']['id'] == value['id']
    assert rig.records['request']['launcher'] == value['launcher']
    assert rig.records['request']['repair'] is True
    assert rig.records['request']['hold'] == rig.run
    rig.records.pop('request')
    rig.records['journal']['request'] = None
    follow.at_start(rig.m)
    assert 'request' not in rig.records
    assert not follow.fenced(rig.m)


@pytest.mark.asyncio
@pytest.mark.parametrize('barrier', ['lock', 'stop', 'recovery-stop', 'journal'])
async def test_finish_does_not_reopen_until_safe(rig, barrier):
    request(rig, mine=True)
    record(rig, 'settled', result='refreshed')
    if barrier == 'lock':
        rig.lock[0] = True
    elif barrier == 'journal':
        rig.records['journal'] = {'registration': rig.spec.registration_id}
    else:
        rig.config.stop = barrier
    await follow.consider(rig.m)
    assert follow.fenced(rig.m)
    assert update_drain.held(rig.m.service)
    assert rig.records['request']['status'] == 'open'
    assert not rig.released


@pytest.mark.asyncio
async def test_finish_releases_lock_before_publish_and_task_removal_and_retries_removal(rig):
    request(rig, mine=True)
    record(rig, 'settled', result='rolled-back', step='installing the launcher')
    rig.config.remove = False
    await follow.consider(rig.m)
    assert not rig.lock[0]
    assert ('remove', False) in rig.calls
    assert saved(rig)['attempt']['result'] == 'rolled-back'
    assert saved(rig)['attempt']['step'] == 'installing the launcher'
    assert saved(rig)['phase'] == 'failed'
    assert not follow.fenced(rig.m)
    rig.config.remove = True
    await follow.consider(rig.m)
    assert rig.calls.count(('remove', False)) == 2
    assert not rig.m._launcher_follow.remove_pending


@pytest.mark.asyncio
async def test_helper_ended_before_journal_counts_not_started(rig):
    request(rig, mine=True)
    await follow.consider(rig.m)
    assert rig.records['request']['result'] == 'not-started'
    assert saved(rig)['attempt']['result'] == 'not-started'
    assert not follow.fenced(rig.m)


@pytest.mark.asyncio
async def test_preparation_deadline_counts_not_ready(rig):
    request(rig)
    rig.now[0] += 299
    await follow.consider(rig.m)
    assert rig.records['request']['status'] == 'open'
    rig.now[0] += 1
    await follow.consider(rig.m)
    assert rig.records['request']['result'] == 'not-ready'
    assert saved(rig)['attempt']['result'] == 'not-ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('barrier', ['none', 'lock', 'stop', 'recovery-stop'])
async def test_bound_reopens_only_with_lock_and_no_stop_and_keeps_fence(rig, barrier):
    request(rig, mine=True, started=True)
    record(rig, 'waiting')
    rig.now[0] += 599
    await follow.consider(rig.m)
    assert update_drain.held(rig.m.service)
    rig.lock[0] = barrier == 'lock'
    rig.config.stop = barrier
    rig.now[0] += 1
    await follow.consider(rig.m)
    assert follow.fenced(rig.m)
    assert rig.records['request']['status'] == 'open'
    assert update_drain.held(rig.m.service) is (barrier != 'none')
    if barrier == 'none':
        assert rig.records['request']['hold'] is None
        assert saved(rig)['phase'] == 'needs-repair'
        assert not rig.lock[0]
        assert 'attempt' not in saved(rig)


@pytest.mark.asyncio
async def test_hourly_repair_is_idle_only_and_go_ignores_setting_but_not_busy(rig):
    request(rig, mine=True, started=True)
    record(rig, 'waiting')
    rig.now[0] += 600
    await follow.consider(rig.m)
    rig.m.service.state['settings']['updates']['autoInstall'] = False
    rig.now[0] += 3599
    await follow.consider(rig.m)
    assert not any(row[0] == 'start' for row in rig.calls)
    rig.now[0] += 1
    rig.m.busy = True
    await follow.consider(rig.m)
    assert not any(row[0] == 'start' for row in rig.calls)
    rig.m.busy = False
    await follow.consider(rig.m)
    assert sum(row[0] == 'start' for row in rig.calls) == 1
    record(rig, 'ready')
    rig.m.busy = True
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] is None
    assert rig.records['request']['status'] == 'open'
    rig.m.busy = False
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] == rig.run
    assert saved(rig)['phase'] == 'restarting'


@pytest.mark.asyncio
async def test_running_helper_is_not_started_and_minute_retry_is_bounded(rig):
    request(rig, mine=True, started=True)
    record(rig, 'waiting')
    rig.config.task_running = True
    await follow.consider(rig.m)
    rig.config.task_running = False
    rig.now[0] += 59
    await follow.consider(rig.m)
    assert not any(row[0] == 'start' for row in rig.calls)
    rig.now[0] += 1
    # A recovery request has no new attempt in this host.
    rig.m.state['launcherRefresh']['attempt'] = {'id': rig.records['request']['id']}
    await follow.consider(rig.m)
    assert sum(row[0] == 'start' for row in rig.calls) == 1


@pytest.mark.asyncio
async def test_button_consumes_one_press_and_can_go_with_setting_off(rig):
    rig.m.service.state['settings']['updates']['autoInstall'] = False
    await follow.consider(rig.m)
    assert saved(rig)['phase'] == 'ready'
    assert await follow.request_by_button(rig.m)
    assert not await follow.request_by_button(rig.m)
    await follow.consider(rig.m)
    assert saved(rig)['requested'] is None
    assert saved(rig)['attempt']['automatic'] is False
    record(rig, 'ready')
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] == rig.run
    assert not await follow.request_by_button(rig.m)


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['health', 'pendingApp', 'request'])
async def test_button_not_for_launcher_is_not_consumed(rig, condition):
    rig.m.service.state['settings']['updates']['autoInstall'] = False
    await follow.consider(rig.m)
    if condition == 'health':
        rig.m.state['currentStatus'] = Manager.health('failed')
        follow.project(rig.m)
    elif condition == 'pendingApp':
        rig.m.state['pendingApp'] = {'generation': 'other'}
    else:
        request(rig)
    assert not await follow.request_by_button(rig.m)
    assert not saved(rig).get('requested')


def test_restart_hold_retains_only_same_unreleased_launcher_messages(rig):
    service = rig.m.service
    for launcher, phase, expected in [('launcher-id', 'replacing', [['chat', 'input']]),
            ('other', 'replacing', []), ('launcher-id', 'released', [])]:
        rig.m.state['drain'] = {'launcher': launcher, 'phase': phase,
            'messages': [['chat', 'input']], 'workers': {'old': {'withdrawn': True}, 'other': {}}}
        update_drain.hold_for_restart(service, 'launcher-id', {'generation': 'g'})
        drain = rig.m.state['drain']
        assert drain['messages'] == expected
        assert drain['workers'] == {'old': {'withdrawn': True}}
        assert drain['attemptId'] == drain['launcher'] == 'launcher-id'
        assert drain['phase'] == 'replacing' and drain['hostInstanceId'] == 'host'


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['confirmed-layout', 'unconfirmed', 'not-layout', 'failure', 'equal-files'])
async def test_apps_list_pass_only_for_confirmed_setup_once_and_failure_is_nonfatal(rig, condition):
    rig.config.layout = rig.m.home
    if condition == 'unconfirmed':
        rig.config.recovery = None
    elif condition == 'not-layout':
        rig.config.layout = None
    elif condition == 'failure':
        rig.config.shell_error = True
    elif condition == 'equal-files':
        rig.config.differs = False
    rig.m.busy = True
    await two_passes(rig)
    ops = [row[0] for row in rig.calls if row[0] in {'settle', 'version'}]
    assert ops == ([] if condition in {'unconfirmed', 'not-layout'} else
        ['settle'] if condition == 'failure' else ['settle', 'version'])
    result = rig.m._launcher_follow.shell_result
    if condition in {'unconfirmed', 'not-layout'}:
        assert result is None and 'appsList' not in saved(rig)
    else:
        # The result is saved with the launcher state, for diagnostics.
        assert saved(rig)['appsList'] == result == (
            {'errorType': 'PermissionError'} if condition == 'failure' else
            {'settled': {'status': 'settled'}, 'result': {'status': 'settled'}})
    if condition == 'failure':
        assert saved(rig)['phase'] == 'waiting'


@pytest.mark.asyncio
async def test_non_cancel_exception_keeps_fence_and_is_retried(rig):
    rig.config.plan = PermissionError()
    await two_passes(rig)
    assert follow.fenced(rig.m)
    assert saved(rig)['error'] == 'PermissionError'
    rig.config.plan = 'refresh'
    await follow.consider(rig.m)
    assert rig.records['request']['status'] == 'open'


@pytest.mark.asyncio
async def test_poll_budget_wakes_loop_and_cancellation_propagates(rig, monkeypatch):
    request(rig)
    monkeypatch.setattr(follow, '_POLL_SECONDS', 45)
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        rig.now[0] += seconds
    monkeypatch.setattr(follow.asyncio, 'sleep', sleep)
    await follow.consider(rig.m)
    assert len(sleeps) == 45
    assert rig.m.wakeup.is_set()

    async def cancel(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(follow, '_poll_once', cancel)
    with pytest.raises(asyncio.CancelledError):
        await follow.consider(rig.m)
    assert follow.fenced(rig.m)


@pytest.mark.asyncio
async def test_off_windows_every_entry_is_inert_without_even_a_manager(rig, monkeypatch):
    monkeypatch.setattr(follow, '_WINDOWS', False)
    assert follow.at_start(None) is None
    assert follow.project(None) is None
    assert not follow.fenced(None)
    assert await follow.consider(None) is None
    assert not await follow.request_by_button(None)
    assert rig.calls == [('read', 'request'), ('read', 'journal')]


# Independent literals catch a wording regression, not just a changed table key.
TEXTS = {
    'waiting': ('waiting', 'Amplifier needs one more restart',
        'One part of the update remains: the program that starts Amplifier still has version 1.0.0. '
        'Amplifier waits until your work is finished. Then it restarts. This can take a few minutes.', None),
    'ready': ('available', 'One part of the update is ready',
        'The program that starts Amplifier still has version 1.0.0. To update it, select Update Amplifier. '
        'Amplifier then restarts when your work is finished. This can take a few minutes.', 'install'),
    'restarting': ('working', 'Amplifier is restarting',
        'Amplifier is replacing the program that starts it. This can take a few minutes. During that time '
        'this page can show “Unable to reconnect to Amplifier”. The page keeps trying to reconnect.', None),
    'finishing': ('working', 'Amplifier is finishing the update',
        'Amplifier is checking the program that starts it. Amplifier can restart once more. '
        'New work waits until this check is done.', None),
    'failed': ('available', 'Amplifier is running. One part of the update did not finish.',
        'The program that starts Amplifier still has version 1.0.0. Amplifier put the earlier program back.', 'install'),
    'failed-unchanged': ('available', 'Amplifier is running. One part of the update did not start.',
        'The program that starts Amplifier still has version 1.0.0. It was not replaced.', 'install'),
    'repairing': ('working', 'Amplifier is repairing the program that starts it',
        'An update of that program did not finish. Amplifier is trying to put the earlier program back. '
        'Amplifier can restart once more. New work waits until this is done.', None),
    'needs-repair': ('action-required', 'Amplifier is running, but one part of the update did not finish',
        'The program that starts Amplifier still needs a repair. Amplifier tries the repair about once an hour, '
        'when no work runs. Until the repair is done, Amplifier does not check for updates.', None),
}


def projection(rig, phase, health='current', *, open_request=False):
    m = rig.m
    m._launcher_follow.phase = phase
    if open_request:
        request(rig)
        m._launcher_follow.phase = phase
    m.state['launcherRefresh'] = {'phase': phase, 'launcher': {'version': '1.0.0'},
        'app': follow._target(rig.target)}
    m.state['currentStatus'] = m.health(health)
    follow.project(m)
    return m.state['currentStatus']


@pytest.mark.parametrize('phase', list(TEXTS))
@pytest.mark.parametrize('automatic', [True, False])
@pytest.mark.parametrize('health,open_request', [('current', False), ('unchecked', False), ('failed', True)])
def test_each_exact_text_and_setting_and_open_request_precedence(rig, phase, automatic, health, open_request):
    rig.m.service.state['settings']['updates']['autoInstall'] = automatic
    row = projection(rig, phase, health, open_request=open_request)
    status, title, detail, action = TEXTS[phase]
    if phase in {'failed', 'failed-unchanged'}:
        detail += (' Amplifier tries again after the next app update. To try now, select Update Amplifier.'
            if automatic else ' To try again, select Update Amplifier.')
    assert row == {'status': status, 'title': title, 'detail': detail, 'action': action,
        'actionRequired': phase == 'needs-repair', 'component': None}
    follow.project(rig.m)
    assert rig.m.state['currentStatus'] == row


@pytest.mark.parametrize('phase', ['waiting', 'ready', 'failed', 'failed-unchanged'])
@pytest.mark.parametrize('health', ['available', 'failed', 'action-required', 'working'])
def test_other_health_status_wins_and_only_receives_short_suffix(rig, phase, health):
    row = projection(rig, phase, health)
    expected = 'Health detail. The program that starts Amplifier still has version 1.0.0.'
    if phase in {'failed', 'failed-unchanged'}:
        expected += ' Its update did not finish.'
    assert row == {**Manager.health(health), 'detail': expected}
    assert rig.m._launcher_follow.button_generation is None
    follow.project(rig.m)
    assert rig.m.state['currentStatus'] == row


@pytest.mark.parametrize('health', ['current', 'unchecked', 'failed', 'available'])
def test_done_keeps_health_title_and_adds_sentence_only_when_current(rig, health):
    row = projection(rig, 'done', health)
    assert row == {**Manager.health(health), **({'detail':
        'Health detail. The program that starts Amplifier now has version 2.0.0.'} if health == 'current' else {})}


def test_projection_does_no_io_and_retracts_a_previous_offer(rig):
    calls = list(rig.calls)
    projection(rig, 'ready')
    rig.m._launcher_follow.phase = None
    follow.project(rig.m)
    assert rig.m.state['currentStatus'] == Manager.health()
    assert rig.calls == calls


@pytest.mark.asyncio
@pytest.mark.parametrize('result,phase', [('refreshed', 'done'), ('rolled-back', 'failed'), ('refused', 'failed-unchanged')])
async def test_counted_result_does_not_depend_on_remaining_file_difference(rig, result, phase):
    rig.config.differs = False
    rig.m.state['launcherRefresh'] = {'attempt': {'generation': rig.target['generation'], 'result': result}}
    await follow.consider(rig.m)
    assert saved(rig)['phase'] == phase
    assert 'request' not in rig.records


@pytest.mark.asyncio
async def test_button_retries_counted_failure_once(rig):
    rig.m.state['launcherRefresh'] = {'attempt': {'generation': rig.target['generation'], 'result': 'refused'}}
    await follow.consider(rig.m)
    assert await follow.request_by_button(rig.m)
    await follow.consider(rig.m)
    assert rig.records['request']['status'] == 'open'
    assert saved(rig)['attempt']['automatic'] is False
    assert saved(rig)['requested'] is None


@pytest.mark.asyncio
async def test_stale_journal_before_offer_is_kept_and_does_not_block_new_installation(rig):
    rig.records['journal'] = {'registration': 'old-install', 'old': {'digest': 'not-used'}}
    await two_passes(rig)
    assert 'stale' in rig.records
    assert 'request' in rig.records
    assert ('stale', True) in rig.calls


@pytest.mark.asyncio
async def test_request_write_failure_keeps_same_identity_and_resumes(rig, monkeypatch):
    write = rig.refresh.write_json
    failures = [True]

    def flaky(*args):
        if failures[0]:
            failures[0] = False
            raise PermissionError()
        return write(*args)
    monkeypatch.setattr(rig.refresh, 'write_json', flaky)
    await two_passes(rig)
    assert follow.fenced(rig.m)
    identity = saved(rig)['attempt']['id']
    assert saved(rig)['error'] == 'PermissionError'
    await follow.consider(rig.m)
    assert rig.records['request']['id'] == identity
    assert sum(row[0] == 'plan' for row in rig.calls) == 1


@pytest.mark.asyncio
async def test_missing_host_run_does_not_make_an_empty_hold_match(rig):
    rig.run['pid'] = -1
    await two_passes(rig)
    assert 'request' not in rig.records
    assert not follow.fenced(rig.m)


@pytest.mark.asyncio
async def test_real_update_manager_methods_honor_launcher_fence_during_plan(rig):
    from amplifier_web.updates import UpdateManager
    m = rig.m
    m.awaiting_restart = lambda: UpdateManager.awaiting_restart(m)
    original = rig.refresh.plan

    def plan(*args):
        assert m.awaiting_restart()
        return original(*args)
    rig.refresh.plan = plan
    await two_passes(rig)
    before = copy.deepcopy(m.state)
    # These are the real method bodies, with the light manager fixture.
    await UpdateManager.command(m, 'install')
    await UpdateManager.check(m)
    await UpdateManager.install(m)
    await UpdateManager._activate(m)
    await UpdateManager.tick(m)
    assert m.state == before
    assert follow.fenced(m)


@pytest.mark.asyncio
async def test_real_install_command_routes_a_launcher_button_without_app_install(rig):
    from amplifier_web.updates import UpdateManager
    m = rig.m
    m.awaiting_restart = lambda: UpdateManager.awaiting_restart(m)
    m.diagnostics = SimpleNamespace(state={})
    rig.m.service.state['settings']['updates']['autoCheck'] = False
    await follow.consider(m)
    await UpdateManager.command(m, 'install')
    assert saved(rig)['requested'] == {'generation': rig.target['generation']}
    assert m.wakeup.is_set()


@pytest.mark.asyncio
async def test_real_publish_and_configuration_hooks_reapply_projection(rig, monkeypatch):
    from amplifier_web.updates import UpdateManager
    from amplifier_web import update_health, update_restart
    m = rig.m
    projection(rig, 'ready')
    m.requires_idle = lambda state: False
    m.service._publish_changes = lambda **kw: None
    m._update_health_seen_revision = 1
    m.service.state['configurationRevision'] = 2
    monkeypatch.setattr(update_health, 'publish', lambda manager: manager.state.update(currentStatus=Manager.health()))
    monkeypatch.setattr(update_restart, 'target', lambda manager: None)
    await UpdateManager.publish(m, launcherRefresh=m.state['launcherRefresh'])
    assert m.state['currentStatus']['action'] == 'install'
    assert UpdateManager.configuration_changed(m)
    assert m.state['currentStatus']['action'] == 'install'


@pytest.mark.asyncio
@pytest.mark.parametrize('where', ['go', 'start'])
async def test_failed_hold_write_retries_without_losing_closed_admission(rig, monkeypatch, where):
    if where == 'go':
        await two_passes(rig)
        record(rig, 'ready')
    else:
        request(rig, started=True)
    write = rig.refresh.write_json
    fail = [True]

    def flaky(home, name, value):
        if name == 'request' and value.get('hold') and fail[0]:
            fail[0] = False
            raise PermissionError()
        write(home, name, value)
    monkeypatch.setattr(rig.refresh, 'write_json', flaky)
    if where == 'go':
        await follow.consider(rig.m)
    else:
        follow.at_start(rig.m)
    assert update_drain.held(rig.m.service) and follow.fenced(rig.m)
    assert rig.m._launcher_follow.hold_pending
    await follow.consider(rig.m)
    assert rig.records['request']['hold'] == rig.run
    assert not rig.m._launcher_follow.hold_pending
    assert update_drain.held(rig.m.service)


@pytest.mark.asyncio
async def test_bound_release_failure_retries_under_lock_and_stays_fenced(rig, monkeypatch):
    request(rig, mine=True, started=True)
    record(rig, 'waiting')
    finish = update_drain.finish
    fail = [True]

    async def flaky(*args):
        if fail[0]:
            fail[0] = False
            raise PermissionError()
        await finish(*args)
    monkeypatch.setattr(update_drain, 'finish', flaky)
    rig.now[0] += 600
    await follow.consider(rig.m)
    assert rig.m._launcher_follow.reopen_pending
    assert follow.fenced(rig.m) and update_drain.held(rig.m.service)
    await follow.consider(rig.m)
    assert not rig.m._launcher_follow.reopen_pending
    assert saved(rig)['phase'] == 'needs-repair'
    assert follow.fenced(rig.m) and not update_drain.held(rig.m.service)
    assert not rig.lock[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['missing', 'different'])
async def test_changed_request_is_not_overwritten(rig, changed):
    request(rig)
    record(rig, 'ready')
    if changed == 'missing':
        rig.records.pop('request')
    else:
        rig.records['request']['id'] = 'another-request'
    before = copy.deepcopy(rig.records)
    await follow.consider(rig.m)
    assert rig.records == before
    assert follow.fenced(rig.m)
    assert saved(rig)['error'] == 'ValueError'


@pytest.mark.asyncio
async def test_finish_does_not_relabel_an_attempt_from_another_generation(rig):
    request(rig)
    rig.m.state['launcherRefresh'] = {'attempt': {
        'id': 'old', 'generation': 'old-generation', 'result': 'refreshed'}}
    record(rig, 'settled', result='rolled-back')
    await follow.consider(rig.m)
    assert saved(rig)['attempt']['generation'] == rig.target['generation']
    assert saved(rig)['attempt']['id'] == rig.records['request']['id']


@pytest.mark.asyncio
async def test_manual_journal_is_not_treated_as_an_unchanged_finished_request(rig):
    request(rig, mine=True)
    rig.records['journal'] = {'done': False}
    await follow.consider(rig.m)
    assert follow.fenced(rig.m) and update_drain.held(rig.m.service)
    assert rig.records['request']['status'] == 'open'


@pytest.mark.asyncio
async def test_real_loop_considers_launcher_after_tick(rig, monkeypatch):
    from amplifier_web.updates import UpdateManager
    m, calls = rig.m, []
    m.diagnostics = SimpleNamespace(state={})

    async def tick():
        calls.append('tick')
        m.closed = True

    async def consider(manager):
        calls.append('launcher')

    async def wait(delay):
        pass
    m.tick, m.wait_for_work = tick, wait
    monkeypatch.setattr(follow, 'consider', consider)
    await UpdateManager.loop(m)
    assert calls == ['tick', 'launcher']


@pytest.mark.asyncio
async def test_real_loop_considers_launcher_when_tick_fails(rig, monkeypatch):
    # A tick that fails on every pass must not starve an open launcher request.
    from amplifier_web.updates import UpdateManager
    m, calls, latest = rig.m, [], {}

    def record(kind, status, **facts):
        latest.update(id='event', status=status, phase=kind)
    m.diagnostics = SimpleNamespace(state={'latest': latest}, record=record)

    async def tick():
        calls.append('tick')
        m.closed = True
        raise RuntimeError('fixture: the tick failed')

    async def consider(manager):
        calls.append('launcher')

    async def wait(delay):
        pass
    m.tick, m.wait_for_work = tick, wait
    monkeypatch.setattr(follow, 'consider', consider)
    await UpdateManager.loop(m)
    assert calls == ['tick', 'launcher']
    assert latest['status'] == 'failed' and m.published[-1]['phase'] == 'error'


@pytest.mark.asyncio
async def test_go_with_real_publish_saves_drain_and_phase_before_hold_without_deadlock(rig, monkeypatch):
    from amplifier_web.updates import UpdateManager
    from amplifier_web import update_health, update_restart
    m = rig.m
    m.requires_idle = lambda state: False
    published = []
    m.service._publish_changes = lambda **kw: published.append(copy.deepcopy(m.state))
    monkeypatch.setattr(update_health, 'publish', lambda manager: manager.state.update(currentStatus=Manager.health()))
    monkeypatch.setattr(update_restart, 'target', lambda manager: None)
    m.publish = lambda **values: UpdateManager.publish(m, **values)
    await two_passes(rig)
    record(rig, 'ready')
    await asyncio.wait_for(follow.consider(m), timeout=1)
    assert rig.records['request']['hold'] == rig.run
    assert published[-1]['drain']['phase'] == 'replacing'
    assert published[-1]['launcherRefresh']['phase'] == 'restarting'