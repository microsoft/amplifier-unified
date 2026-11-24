import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from amplifier_web.status_protocol import Publisher, discover
from amplifier_web.status_protocol.hook import Observer, mount
from amplifier_web.status_protocol.schema import snapshot


def until(fn, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("Status observation did not arrive")


async def eventually(fn, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        await asyncio.sleep(.01)
    raise AssertionError("Status observation did not arrive")


def test_redaction_exact_plan_counts_and_explicit_text_policy(tmp_path):
    secret = "private prompt token=secret"
    value = {"where": {"sessionId": "id", "provider": "https://user:secret@host/path"},
             "now": {"status": "tool", "tools": [{"id": "c", "name": "bash", "arguments": secret}]},
             "planItems": [{"content": secret, "status": "in_progress"}], "error": secret,
             "messages": [secret], "draft": secret, "objective": secret, "usage": {"inputTokens": float('nan')}}
    clean = snapshot(value)
    assert secret not in json.dumps(clean)
    assert clean['where']['provider'] is None and clean['health']['error'] is False
    assert clean['plan']['counts'] == {'pending': 0, 'in_progress': 1, 'completed': 0}
    assert clean['plan']['currentSteps'] == [0]
    assert snapshot(value, include_text=True)['plan']['items'][0]['text'] == secret
    assert clean['usage']['inputTokens'] is None
    # Redaction also applies when rich input reaches the real default writer,
    # independently of the Unified adapter's already-textless status receipt.
    directory = tmp_path/'status'
    publisher = Publisher(directory, host='unified', version='test')
    try:
        assert publisher.submit('id', value)
        records = until(lambda: discover(directory)['sessions'])
        assert records[0]['plan'] == clean['plan']
        assert records[0]['textPolicy'] == 'redacted'
        assert secret not in json.dumps(records)
    finally:
        publisher.close()
    assert not publisher._thread.is_alive()
    files = list(publisher.directory.glob('*.json'))
    assert files and all(secret not in path.read_text() for path in files)


def test_atomic_private_coalesced_snapshots_stale_ended_and_identity(tmp_path):
    directory = tmp_path/'status'
    publisher = Publisher(directory, host='cli', version='test', coalesce=.08, heartbeat=.1, stale_after=.3)
    try:
        for n in range(1000):
            assert publisher.submit('../../escape', {'now': {'status': 'tool', 'tools': [
                {'id': 'call', 'name': 'bash', 'startedAt': time.time() - 1}]}, 'planItems': []})
        rows = until(lambda: discover(directory)['sessions'])
        assert rows[0]['where']['sessionId'] == '../../escape'
        assert rows[0]['now']['tools'][0]['elapsedSeconds'] >= 1
        assert publisher.writes < 10
        files = list(directory.glob('*/*.json'))
        assert len(files) == 1
        if os.name != 'nt':
            assert files[0].stat().st_mode & 0o777 == 0o600
            assert files[0].parent.stat().st_mode & 0o777 == 0o700
        assert discover(directory, now=time.time()+20)['sessions'][0]['freshness'] == 'stale'
        # Frequent overlapping readers never observe a partial JSON document.
        for n in range(200):
            assert discover(directory)['invalid'] == 0
        assert rows[0]['producer']['instanceId'] == publisher.instance_id
    finally:
        publisher.close()
    assert discover(directory)['sessions'][0]['freshness'] == 'ended'
    assert not publisher._thread.is_alive()


def test_failure_and_symlink_do_not_escape_or_raise(tmp_path):
    target = tmp_path/'foreign'; target.mkdir()
    link = tmp_path/'status'; link.symlink_to(target, target_is_directory=True)
    publisher = Publisher(link, host='cli', version='test', heartbeat=.05)
    try:
        assert publisher.submit('id', {'now': {'status': 'thinking'}})
        until(lambda: publisher.failures)
        assert not list(target.iterdir())
    finally:
        publisher.close()


def test_session_bound_terminal_retention_and_bounded_discovery(tmp_path):
    publisher = Publisher(tmp_path/'status', host='cli', version='test', max_sessions=2, coalesce=.01)
    try:
        assert publisher.submit('one', {}) and publisher.submit('two', {})
        assert not publisher.submit('three', {})
        for identity in ['one', 'two']:
            publisher.end(identity)
        until(lambda: len(publisher._retained) == 2)
        for identity in ['three', 'four']:
            assert publisher.submit(identity, {})
            publisher.end(identity)
            until(lambda: identity in publisher._retained)
        assert len(discover(tmp_path/'status')['sessions']) == 2
        assert discover(tmp_path/'status', limit=1)['truncated']
    finally:
        publisher.close()


@pytest.mark.parametrize(('operation', 'lifecycle'), [('end', 'ended'), ('suspend', 'unobserved')])
def test_terminal_write_retries_on_heartbeat_and_releases_capacity(tmp_path, monkeypatch, operation, lifecycle):
    from amplifier_web.status_protocol import publisher as writer
    original = writer.atomic
    attempts = []

    def fail_once(path, value):
        if value['lifecycle'] == lifecycle:
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise OSError('temporary write failure')
        return original(path, value)

    monkeypatch.setattr(writer, 'atomic', fail_once)
    directory = tmp_path/'status'
    publisher = Publisher(directory, host='cli', version='test', max_sessions=1, heartbeat=.05, coalesce=.01)
    try:
        assert publisher.submit('one', {})
        until(lambda: discover(directory)['sessions'])
        getattr(publisher, operation)('one')
        until(lambda: 'one' in publisher._retained)
        assert len(attempts) == 2 and publisher.failures == 1
        assert discover(directory)['sessions'][0]['lifecycle'] == lifecycle
        assert publisher.submit('two', {})
        until(lambda: any(row['where']['sessionId'] == 'two' for row in discover(directory)['sessions']))
    finally:
        publisher.close()
    assert not publisher._thread.is_alive()


def test_persistent_terminal_failure_retries_at_heartbeat_and_close_still_finishes(tmp_path, monkeypatch):
    from amplifier_web.status_protocol import publisher as writer
    attempts = []

    def fail(path, value):
        attempts.append(time.monotonic())
        raise OSError('unavailable status filesystem')

    monkeypatch.setattr(writer, 'atomic', fail)
    publisher = Publisher(tmp_path/'status', host='cli', version='test', heartbeat=.1, coalesce=.01)
    try:
        assert publisher.submit('one', {})
        publisher.end('one')
        until(lambda: len(attempts) >= 3)
        # Failure does not immediately requeue dirty work in a busy loop.
        assert attempts[2] - attempts[0] >= .1
    finally:
        publisher.close()
    assert not publisher._thread.is_alive()


def test_hook_only_accepts_successful_plan_and_never_serializes_tool_content(tmp_path):
    publisher = Publisher(tmp_path/'status', host='cli', version='test')
    observer = Observer(publisher, 'root')
    args = {'action': 'update', 'todos': [{'content': 'SECRET', 'status': 'in_progress'}]}
    observer.event('tool:pre', {'session_id': 'root', 'tool_name': 'todo', 'tool_call_id': 'a', 'tool_input': args})
    assert 'planItems' not in observer.value
    observer.event('tool:post', {'session_id': 'root', 'tool_name': 'todo', 'tool_call_id': 'a',
                                 'result': {'success': True, 'output': {'status': 'updated'}}})
    assert observer.value['planItems'] == [{'status': 'in_progress'}]
    observer.event('tool:post', {'session_id': 'child', 'tool_name': 'todo', 'tool_call_id': 'b', 'result': {'todos': []}})
    assert len(observer.value['planItems']) == 1
    observer.event('tool:error', {'session_id': 'root', 'tool_name': 'todo', 'tool_call_id': 'a'})
    assert observer.value['planItems'] is None
    observer.event('tool:post', {'session_id': 'root', 'tool_name': 'todo', 'tool_call_id': 'b', 'result': {'success': False}})
    assert observer.value['planItems'] is None
    publisher.close()
    assert 'SECRET' not in json.dumps(discover(tmp_path/'status'))


async def test_hook_defers_to_unified_and_discovers_contributed_events(tmp_path):
    from types import SimpleNamespace
    from amplifier_core import HookRegistry
    class Coordinator:
        config = {'application_host': 'Amplifier Web'}
        session = SimpleNamespace(session_id='root', parent_id=None)
        hooks = HookRegistry()
        capabilities = {}
        def get_capability(self, name): return self.capabilities.get(name)
        def register_capability(self, name, value): self.capabilities[name] = value
        async def collect_contributions(self, channel):
            assert channel == 'observability.events'
            return [['question:asked', 'untrusted:arbitrary']]
    coordinator = Coordinator()
    before = set(threading.enumerate())
    assert (await mount(coordinator, {'directory': str(tmp_path/'status')}))['status'] == 'host-owned'
    assert set(threading.enumerate()) == before and not (tmp_path/'status').exists()
    coordinator.config = {}
    await mount(coordinator, {'directory': str(tmp_path/'status')})
    assert set(threading.enumerate()) == before and not (tmp_path/'status').exists()
    observer = None
    try:
        await coordinator.hooks.emit('session:start', {'session_id': 'root'})
        observer = coordinator.capabilities['status.emitter']
        await coordinator.hooks.emit('question:asked', {'session_id': 'root', 'id': 'q', 'question': 'SECRET'})
        assert observer.value['now']['status'] == 'waiting_person'
        assert (await mount(coordinator))['status'] == 'already-mounted'
    finally:
        await coordinator.hooks.emit('session:end', {'session_id': 'root'})
    assert not observer.publisher._thread.is_alive()


async def test_unified_disabled_is_noop_enabled_coalesces_without_lazy_reads(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web.cold_display import ColdRecord
    monkeypatch.delenv('AMPLIFIER_STATUS_DIR', raising=False)
    before = set(threading.enumerate())
    app = AppService(tmp_path/'disabled', workspace=tmp_path)
    assert app.status_emitter is None and set(threading.enumerate()) == before
    await app.close()
    directory = tmp_path/'status'; monkeypatch.setenv('AMPLIFIER_STATUS_DIR', str(directory))
    app = AppService(tmp_path/'enabled', workspace=tmp_path)
    emitter = app.status_emitter
    try:
        session = app._new_session({'title': 'private title'})
        app.state['sessions'].append(session)
        app.projections.invalidate(state=app._state)
        sid = session['id']
        calls = []
        original = emitter._project
        def record(ids): calls.append(ids); original(ids)
        monkeypatch.setattr(emitter, '_project', record)
        def no_read(*args): raise AssertionError('Status cannot load cold history')
        monkeypatch.setattr('amplifier_web.cold_display.load', no_read)
        # Actual host runtime reducers, then ordinary canonical publication.
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'working'})
        await app.on_runtime_event('runtime.tool', {'sessionId': sid, 'phase': 'pre', 'callId': 'call', 'tool': 'bash'})
        for _ in range(1000): emitter.publish({sid})
        assert not calls
        rows = await eventually(lambda: [r for r in discover(directory)['sessions'] if r['where']['sessionId'] == sid])
        assert rows[0]['now']['status'] == 'tool' and rows[0]['now']['tools'][0]['name'] == 'bash'
        assert len(calls) == 1
        # A real cold record with poisoned history must not be hydrated by projection.
        cold = ColdRecord({'id': 'cold', 'status': 'idle', '_coldFields': {'execution': {'$resource': 'missing'}}}, app.db)
        app.state['sessions'].append(cold)
        app._state['selectedSessionId'] = 'cold'
        app.projections.invalidate(state=app._state)
        emitter.publish({'cold'})
        await eventually(lambda: any(r['where']['sessionId'] == 'cold' for r in discover(directory)['sessions']))
        app.state['sessions'].remove(cold)
    finally:
        await app.close()
    assert all(r['freshness'] == 'ended' for r in discover(directory)['sessions'])


def test_nonregular_discovery_and_exact_instance_are_bounded(tmp_path):
    import hashlib
    directory = tmp_path/'status'
    publisher = Publisher(directory, host='cli', version='test')
    try:
        publisher.submit('root', {})
        until(lambda: discover(directory)['sessions'])
        wrong = publisher.directory / ('a'*64+'.json')
        if hasattr(os, 'mkfifo'):
            os.mkfifo(wrong)
        else:
            wrong.mkdir()
        started = time.monotonic()
        report = discover(directory, instance=publisher.instance_id)
        assert time.monotonic()-started < .5
        assert report['invalid'] == 1 and not report['complete']
        assert len(report['sessions']) == 1
        for i in range(5):
            (directory/hashlib.sha256(str(i).encode()).hexdigest()[:32]).mkdir(mode=0o700)
        assert discover(directory, limit=1)['truncated']
        assert len(discover(directory, instance=publisher.instance_id)['sessions']) == 1
    finally:
        publisher.close()


def test_observed_pending_identity_counts_and_opted_in_plan_labels(tmp_path):
    publisher = Publisher(tmp_path/'status', host='cli', version='test', include_text=True)
    try:
        observer = Observer(publisher, 'root')
        for identity in ['q1', 'q2']:
            observer.event('question:asked', {'id': identity})
        observer.event('question:answered', {'id': 'q1'})
        assert observer.value['now']['status'] == 'waiting_person'
        assert observer.value['now']['pendingQuestions'] == 1
        observer.event('question:answered', {})
        assert observer.value['now']['pendingQuestions'] is None
        observer.event('tool:pre', {'tool_name': 'todo', 'tool_call_id': 'call',
            'tool_input': {'action': 'update', 'todos': [{'content': 'Explicit label', 'status': 'pending'}]}})
        observer.event('tool:post', {'tool_name': 'todo', 'tool_call_id': 'call',
            'result': {'success': True, 'output': {'status': 'updated'}}})
        assert observer.value['planItems'][0]['content'] == 'Explicit label'
    finally:
        publisher.close()


async def test_native_execution_observer_plan_crosses_process_normalization_without_payload(tmp_path, monkeypatch):
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.runtime import normalize_event
    from amplifier_web.service import AppService
    from amplifier_web.browser_detail import project
    from amplifier_core import ToolResult
    directory = tmp_path/'status'
    monkeypatch.setenv('AMPLIFIER_STATUS_DIR', str(directory))
    app = AppService(tmp_path/'app', workspace=tmp_path)
    emitter = app.status_emitter
    try:
        session = app._new_session({'title': 'Status boundary'})
        session['status'] = 'working'
        app._state['sessions'].append(session)
        sid = session['id']
        app._state['selectedSessionId'] = sid
        app.projections.invalidate(state=app._state)
        events = []
        observer = ExecutionEvents(sid, events.append)
        checklist = [{'content': 'SECRET', 'activeForm': 'SECRET active form', 'status': 'in_progress'}]
        observer.hook(sid, 'tool:pre', {'session_id': sid, 'tool_name': 'todo', 'tool_call_id': 'call',
            'tool_input': {'action': 'update',
                          'todos': [{**checklist[0], 'private': 'PRIVATE_RAW_SENTINEL'}]}})
        assert 'reportedPlan' not in events[-1]['event']
        assert 'SECRET' not in json.dumps(events)
        observer.hook(sid, 'tool:post', {'session_id': sid, 'tool_name': 'todo', 'tool_call_id': 'call',
            'result': ToolResult(success=True, output={'status': 'updated', 'private': 'PRIVATE_RAW_SENTINEL'})})
        normalized = [normalize_event(event, sid) for event in events]
        _, payload = normalized[-1]
        # WS3's authenticated chat display and the optional local-status
        # registry are different consumers of this worker observation.
        assert payload['reportedPlan'] == checklist
        assert payload['statusPlan'] == [{'status': 'in_progress'}]
        assert events[-1]['event']['reportedPlan'] == checklist
        for event in events:
            for key, value in event['event'].items():
                if key != 'reportedPlan':
                    assert 'SECRET' not in json.dumps(value)
            assert 'PRIVATE_RAW_SENTINEL' not in json.dumps(event)
        assert 'input' not in payload and 'output' not in payload and 'error' not in payload
        assert 'PRIVATE_RAW_SENTINEL' not in json.dumps(normalized)
        assert all('reportedPlan' not in row for row in observer.calls.values())
        assert all('reportedPlan' not in row for row in observer.nodes.values())
        for kind, value in normalized:
            await app.on_runtime_event(kind, value)
        assert project(session)['execution']['plan'] == {'items': checklist}
        assert emitter.plans[sid] == [{'status': 'in_progress'}]
        assert emitter.publisher.include_text is False

        def published():
            return next((row for row in discover(directory)['sessions']
                         if row['where']['sessionId'] == sid), None)

        expected_status = {'state': 'observed',
            'items': [{'index': 0, 'status': 'in_progress'}],
            'counts': {'pending': 0, 'in_progress': 1, 'completed': 0}, 'currentSteps': [0]}
        record = await eventually(lambda: (row if (row := published())
            and row['plan'] == expected_status else None))
        assert record['textPolicy'] == 'redacted'
        assert record['plan'] == expected_status
        files = list(emitter.publisher.directory.glob('*.json'))
        assert files
        for encoded in [json.dumps(record), *(path.read_text() for path in files)]:
            assert 'SECRET' not in encoded and 'PRIVATE_RAW_SENTINEL' not in encoded

        # Failed status receipts become unavailable in the status protocol,
        # while the chat keeps its last successful checklist.
        # A retained legacy execution.plan must not override an unavailable
        # status receipt or replace the independent last-reported chat Plan.
        session['execution']['plan'] = {'items': checklist}
        for call, result in (
            ('failed', ToolResult(success=False, output={'todos': []})),
            ('invalid', ToolResult(success=True, output={'todos': [{'status': 'invalid'}]})),
        ):
            observer.hook(sid, 'tool:post', {'session_id': sid, 'tool_name': 'todo', 'tool_call_id': call,
                'result': result})
            failed = normalize_event(events[-1], sid)
            assert failed[1]['statusPlan'] is None and 'reportedPlan' not in failed[1]
            await app.on_runtime_event(*failed)
            assert sid in emitter.plans and emitter.plans[sid] is None
            await eventually(lambda: (row := published()) and row['plan'] == {
                'state': 'unavailable', 'items': [], 'counts': {}})
            assert project(session)['execution']['plan'] == {'items': checklist}
            assert 'SECRET' not in json.dumps(emitter.plans)
        observer.hook(sid, 'tool:post', {'session_id': sid, 'tool_name': 'todo', 'tool_call_id': 'clear',
            'result': ToolResult(success=True, output={'todos': []})})
        cleared = normalize_event(events[-1], sid)
        assert cleared[1]['statusPlan'] == cleared[1]['reportedPlan'] == []
        await app.on_runtime_event(*cleared)
        await eventually(lambda: (row := published()) and row['plan'] == {
            'state': 'observed', 'items': [], 'currentSteps': [],
            'counts': {'pending': 0, 'in_progress': 0, 'completed': 0}})
        assert project(session)['execution']['plan'] == {'items': []}
    finally:
        await app.close()
    assert not emitter.publisher._thread.is_alive()
    for path in emitter.publisher.directory.glob('*.json'):
        assert 'SECRET' not in path.read_text() and 'PRIVATE_RAW_SENTINEL' not in path.read_text()


async def test_old_idle_catalog_does_not_fill_capacity_and_resume_is_observable(tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    monkeypatch.setenv('AMPLIFIER_STATUS_DIR', str(tmp_path/'status'))
    app = AppService(tmp_path/'app', workspace=tmp_path)
    emitter = app.status_emitter
    try:
        for i in range(300):
            app.state['sessions'].append({'id': 'old-'+str(i), 'status': 'idle'})
        active = app._new_session({'title': 'active'}); active['status'] = 'working'
        app.state['sessions'].append(active)
        app.projections.invalidate(state=app._state)
        emitter.publish({active['id']})
        rows = await eventually(lambda: discover(tmp_path/'status')['sessions'])
        assert any(row['where']['sessionId'] == active['id'] for row in rows)
        assert not any(row['where']['sessionId'].startswith('old-') for row in rows)
        emitter.publisher.end(active['id'])
        await eventually(lambda: active['id'] in emitter.publisher._retained)
        emitter.publish({active['id']})
        await eventually(lambda: any(row['where']['sessionId'] == active['id'] and row['freshness']=='live' for row in discover(tmp_path/'status')['sessions']))
        app.state['sessions'] = [s for s in app.state['sessions'] if not s['id'].startswith('old-')]
    finally:
        await app.close()


async def test_active_overflow_is_visible_and_rotates_without_false_end(tmp_path):
    from types import SimpleNamespace
    from amplifier_web.status_emitter import StatusEmitter
    rows = {f'active-{i}': {'id': f'active-{i}', 'status': 'working'} for i in range(300)}
    service = SimpleNamespace(instance_id='overflow', _state={},
        projections=SimpleNamespace(sessions=lambda _: SimpleNamespace(by_id=rows)),
        runtime=SimpleNamespace(workers=dict.fromkeys(rows), _preparations={}))
    emitter = StatusEmitter(service, tmp_path/'status')
    seen = set()
    try:
        for _ in range(5):
            emitter._project(None)
            if emitter.timer:
                emitter.timer.cancel(); emitter.timer = None
            await asyncio.sleep(.15)
            observed = discover(tmp_path/'status', limit=1024)['sessions']
            seen.update(row['where']['sessionId'] for row in observed)
        assert seen == set(rows)
        assert len(emitter.tracked) <= 256 and len(emitter.publisher._rows) <= 256
        assert all(not row['coverage']['complete'] for row in observed)
        assert all(row['coverage']['adapter']['candidateOmittedLowerBound'] == 44 for row in observed)
        assert any(row['freshness'] == 'unobserved' for row in observed)
        assert not any(row['freshness'] == 'ended' for row in observed)
    finally:
        await emitter.close()


def test_process_death_expires_observation_without_invented_completion(tmp_path):
    import subprocess
    import sys
    script = '''from amplifier_web.status_protocol import Publisher
import sys,time
p=Publisher(sys.argv[1],host='cli',version='fixture',heartbeat=.05,stale_after=.15)
p.submit('session', {'now': {'status':'thinking'}})
time.sleep(30)
'''
    proc = subprocess.Popen([sys.executable, '-c', script, str(tmp_path/'status')])
    try:
        until(lambda: discover(tmp_path/'status')['sessions'])
        proc.kill(); proc.wait(timeout=3)
        rows = until(lambda: [r for r in discover(tmp_path/'status')['sessions'] if r['freshness']=='stale'])
        assert rows[0]['now']['status'] == 'thinking'
        assert rows[0]['lifecycle'] == 'live'
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait(timeout=3)


async def test_settled_observation_has_explicit_thirty_second_grace(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import amplifier_web.status_emitter as adapter
    row = {'id': 'session', 'status': 'working'}
    service = SimpleNamespace(instance_id='grace', _state={},
        projections=SimpleNamespace(sessions=lambda _: SimpleNamespace(by_id={'session': row})),
        runtime=SimpleNamespace(workers={}, _preparations={}))
    clock = [100.0]
    monkeypatch.setattr(adapter, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    emitter = adapter.StatusEmitter(service, tmp_path/'status')
    def project():
        emitter._project({'session'})
        if emitter.timer:
            emitter.timer.cancel(); emitter.timer = None
    try:
        project()
        await eventually(lambda: discover(tmp_path/'status')['sessions'])
        row['status'] = 'idle'
        project()
        assert emitter.tracked['session'] == 130
        clock[0] = 129
        project()
        assert 'session' in emitter.tracked
        clock[0] = 131
        project()
        await eventually(lambda: any(r['freshness']=='ended' for r in discover(tmp_path/'status')['sessions']))
        assert 'session' not in emitter.tracked
    finally:
        await emitter.close()
