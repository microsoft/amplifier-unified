"""Incremental Work stays byte/source fenced and equal to the reference reader."""
import copy
import json
from pathlib import Path

import pytest

from amplifier_web import history_read_model as models
from amplifier_web.event_log_view import EventIndex, EventLogView, event_path
from test_event_log_view import append, source


def inspect(model, session):
    result = model.project(copy.deepcopy(EventLogView.projection_input(session)))
    if result['status'] == 'complete':
        tree = models.materialize_result(session, result)
        if tree is not None:
            session['execution'] = tree
    return result


def family(session, path, count=12):
    for n in range(count):
        child = f'child-{n}'
        append(path, 'tool:pre', {'tool_call_id': f'delegate-{n}', 'tool_name': 'delegate',
                                'tool_input': {'agent': child}}, 10 + n)
        append(path, 'delegate:agent_spawned', {'tool_call_id': f'delegate-{n}',
                                             'sub_session_id': child, 'agent': child}, 11 + n)
        leaf = event_path(session, child)
        append(leaf, 'tool:pre', {'session_id': child, 'tool_call_id': 'leaf',
                                'tool_name': 'bash', 'tool_input': {'command': 'fixture'}}, 12 + n)
    return event_path(session, 'child-0')


@pytest.fixture
def model(tmp_path):
    value = models.SelectedHistoryReadModel(tmp_path / 'host')
    yield value
    value.close()


def test_cold_and_unchanged_equal_reference(source, model, monkeypatch):
    session, path = source
    family(session, path)
    expected = EventLogView(None).read(copy.deepcopy(session))
    result = inspect(model, session)
    assert result['status'] == 'complete'
    assert session['execution'] == expected
    monkeypatch.setattr(model.reader, 'read', lambda _: pytest.fail('unchanged tree reconstructed'))
    result = inspect(model, session)
    assert result['unchanged'] and 'tree' not in result
    assert session['execution'] == expected


def test_non_work_append_does_not_reload_descendants(source, model, monkeypatch):
    session, path = source
    child = family(session, path, 40)
    inspect(model, session)
    old = copy.deepcopy(session['execution'])
    append(path, 'context:compaction_progress', {'completed': 1}, 100)
    append(child, 'session:end', {'session_id': 'child-0'}, 101)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    monkeypatch.setattr(model.reader, 'read', lambda _: pytest.fail('unchanged descendants reconstructed'))
    monkeypatch.setattr(model.reader, '_new_index', lambda *_: pytest.fail('unchanged index restored'))
    result = inspect(model, session)
    assert result['unchanged'] and 'tree' not in result
    assert result['diagnostics']['counters']['events.model.source_only'] == 2
    assert session['execution'] == expected == old


@pytest.mark.parametrize('name,data', [
    ('tool:post', {'result': 'complete'}),
    ('tool:error', {'error': 'fixture failure'}),
    ('tool:post', {'result': {'success': False, 'message': 'kept failure'}}),
])
def test_child_completion_updates_only_changed_rows(source, model, monkeypatch, name, data):
    session, path = source
    child = family(session, path, 24)
    inspect(model, session)
    append(child, name, {'session_id': 'child-0', 'tool_call_id': 'leaf', **data}, 80)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    monkeypatch.setattr(model.reader, 'read', lambda _: pytest.fail('completion reconstructed full tree'))
    result = inspect(model, session)
    assert 'tree' not in result and len(result['delta']['nodes']) <= 2
    assert session['execution'] == expected
    assert session['execution']['aggregateUsage'] == expected['aggregateUsage']


def test_root_completion_without_transcript_keeps_complete_turn_timing(source, model, monkeypatch):
    session, path = source
    append(path, 'tool:pre', {'tool_call_id': 'one', 'tool_name': 'bash', 'tool_input': {'command': 'fixture'}}, 10)
    inspect(model, session)
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'complete'}, 80)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    monkeypatch.setattr(model.reader, 'read', lambda _: pytest.fail('simple completion reconstructed tree'))
    result = inspect(model, session)
    assert len(result['delta']['nodes']) == 1
    assert session['execution'] == expected


@pytest.mark.parametrize('name,data', [
    ('unknown:future', {}), ('prompt:submit', {'prompt': 'later'}),
    ('delegate:agent_spawned', {'tool_call_id': 'one', 'sub_session_id': 'new'}),
    ('llm:response', {'model': 'fixture', 'usage': {'input_tokens': 2}}),
    ('session:end', {'kind': 'llm', 'id': 'host-call', 'usage': {'totalTokens': 3}}),
])
def test_unknown_or_semantic_appends_take_reference_reader(source, model, name, data):
    session, path = source
    family(session, path, 2)
    inspect(model, session)
    append(path, name, data, 70)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    result = inspect(model, session)
    assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
    assert session['execution'] == expected


@pytest.mark.parametrize('change', ['rewrite_append', 'replace', 'truncate'])
def test_source_generation_changes_do_not_reuse_saved_tree(source, model, change):
    session, path = source
    append(path, 'tool:post', {'tool_call_id': 'one', 'tool_name': 'bash', 'result': 'before'}, 10)
    append(path, 'session:end', {}, 11)
    inspect(model, session)
    if change == 'rewrite_append':
        # Same inode, same earlier length, unchanged final line, followed by an
        # append: tail-only validation incorrectly retained the old tool body.
        path.write_text(path.read_text().replace('before', 'edited'))
        append(path, 'session:end', {}, 12)
    elif change == 'replace':
        replacement = path.with_suffix('.new')
        append(replacement, 'tool:post', {'tool_call_id': 'one', 'result': 'replacement'}, 20)
        replacement.replace(path)
    else:
        path.write_text('')
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    result = inspect(model, session)
    assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
    assert session['execution'] == expected
    if change == 'rewrite_append':
        assert session['execution']['nodes'][0]['output'] == 'edited'


def test_partial_final_line_does_not_become_a_committed_event(source, model):
    session, path = source
    family(session, path, 2)
    inspect(model, session)
    old = copy.deepcopy(session['execution'])
    with path.open('a') as stream:
        stream.write('{"event":"session:end","data":')
    assert inspect(model, session)['unchanged']
    with path.open('a') as stream:
        stream.write('{}}\n')
    assert inspect(model, session)['unchanged']
    assert session['execution'] == old


def test_requested_model_survives_worker_restart_without_replaying_logs(source, tmp_path, monkeypatch):
    session, path = source
    family(session, path, 4)
    home = tmp_path / 'host'
    first = models.SelectedHistoryReadModel(home)
    inspect(first, session)
    first.close()
    second = models.SelectedHistoryReadModel(home)
    try:
        append(path, 'session:end', {}, 100)
        monkeypatch.setattr(second.reader, 'read', lambda _: pytest.fail('valid requested model lost on restart'))
        result = inspect(second, session)
        assert result['unchanged']
        assert result['diagnostics']['counters']['events.model.cache_hit'] == 1
    finally:
        second.close()


def test_changes_during_projection_return_retry_without_committing_partial_view(source, model, monkeypatch):
    session, path = source
    family(session, path, 2)
    read = model.reader.read
    def changing(inputs):
        value = read(inputs)
        append(path, 'tool:post', {'tool_call_id': 'later', 'result': 'new'}, 100)
        return value
    monkeypatch.setattr(model.reader, 'read', changing)
    result = inspect(model, session)
    assert result == {'status': 'retry', 'reason': 'source_changed'}
    assert 'execution' not in session and not model.models


def test_input_changes_including_fork_rebuild_exact_owner(source, model):
    session, path = source
    family(session, path, 2)
    inspect(model, session)
    session['messages'].append({'id': 'second', 'role': 'user', 'createdAt': 9})
    assert inspect(model, session)['diagnostics']['counters']['events.model.rebuilt'] == 1
    session['id'] = 'forked'
    assert inspect(model, session)['diagnostics']['counters']['events.model.rebuilt'] == 1


def test_model_retention_is_bounded_and_does_not_remove_canonical_files(source, model, monkeypatch):
    session, path = source
    family(session, path, 2)
    monkeypatch.setattr(models, 'MAX_MODELS', 2)
    for n in range(4):
        current = {**session, 'id': f'view-{n}'}
        inspect(model, current)
    assert len(model.models) == 2
    assert len(model.reader.read_paths) <= 2
    assert len(model.reader.read_revisions) <= 2
    assert len(model.reader.read_proofs) <= 2
    assert model.store.db.execute('SELECT count(*) FROM models').fetchone()[0] == 2
    assert path.exists()


def test_append_checkpoint_rechecks_complete_prefix(source):
    session, path = source
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'before'}, 10)
    append(path, 'session:end', {}, 11)
    index = EventIndex(path, 'native')
    index.refresh()
    path.write_text(path.read_text().replace('before', 'edited'))
    append(path, 'session:end', {}, 12)
    index.refresh()
    assert index.nodes['tool:native:one']['output'] == 'edited'


@pytest.mark.parametrize('field,value', [('phase', 'failed'), ('endedAt', 30)])
def test_host_worker_metadata_remains_owner(source, model, field, value):
    session, path = source
    child = family(session, path, 3)
    inspect(model, session)
    worker = next(row for row in session['execution']['nodes'] if row['kind'] == 'worker' and row['sessionId'] == 'child-0')
    worker[field] = value
    inspect(model, session)
    append(child, 'tool:post', {'session_id': 'child-0', 'tool_call_id': 'leaf', 'result': 'complete'}, 80)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    inspect(model, session)
    assert session['execution'] == expected


def test_nested_completion_reference_equality(source, model):
    session, path = source
    child = family(session, path, 2)
    append(child, 'tool:pre', {'session_id': 'child-0', 'tool_call_id': 'delegate', 'tool_name': 'delegate'}, 20)
    append(child, 'delegate:agent_spawned', {'session_id': 'child-0', 'tool_call_id': 'delegate', 'sub_session_id': 'grandchild', 'agent': 'nested'}, 21)
    grandchild = event_path(session, 'grandchild')
    append(grandchild, 'tool:pre', {'session_id': 'grandchild', 'tool_call_id': 'leaf', 'tool_name': 'bash', 'tool_input': {'command': 'fixture'}}, 22)
    inspect(model, session)
    append(grandchild, 'tool:post', {'session_id': 'grandchild', 'tool_call_id': 'leaf', 'result': 'complete'}, 80)
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    inspect(model, session)
    assert session['execution'] == expected


async def test_async_owner_builds_and_validates_off_event_loop(source, model, tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from amplifier_web.performance_diagnostics import PerformanceDiagnostics
    session, path = source
    family(session, path, 3)
    published = []
    service = SimpleNamespace(_session=lambda _: session, lock=asyncio.Lock(), closed=False,
        _publish=lambda **kw: published.append(kw), performance_diagnostics=PerformanceDiagnostics())
    view = EventLogView(service)
    class Projector:
        async def project(self, inputs, *, key):
            return await asyncio.to_thread(model.project, inputs)
    view.projector = Projector()
    original = view.projection_input
    seen = []
    def checked(value):
        seen.append(threading.get_ident())
        assert not service.lock.locked()
        return original(value)
    monkeypatch.setattr(view, 'projection_input', checked)
    await view.refresh('app', passive=True)
    assert seen and all(identity != threading.get_ident() for identity in seen)
    assert len(published) == 2 and isinstance(view.projected['app'][0], str)
    assert not view._pending_inputs
    expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
    assert session['execution'] == expected
    append(path, 'session:end', {}, 90)
    await view.refresh('app', passive=True)
    assert len(published) == 2  # Receipt only, no browser publication.


@pytest.mark.parametrize('mutation', ['published', 'unpublished', 'replacement', 'source', 'retry'])
async def test_async_process_results_cannot_overwrite_newer_owner(source, model, mutation):
    import asyncio
    from types import SimpleNamespace
    session, path = source
    family(session, path, 2)
    rows = [session]
    published = []
    service = SimpleNamespace(_session=lambda _: rows[0], lock=asyncio.Lock(), closed=False,
                             _publish=lambda **kw: published.append(kw))
    view = EventLogView(service)
    class Projector:
        async def project(self, inputs, *, key):
            result = await asyncio.to_thread(model.project, inputs)
            if mutation in {'published', 'unpublished'}:
                session['messages'].append({'id': 'new', 'role': 'user', 'createdAt': 90})
                if mutation == 'published':
                    view.invalidate({'app'})
            elif mutation == 'replacement':
                rows[0] = {**session}
            elif mutation == 'source':
                append(path, 'tool:post', {'tool_call_id': 'new', 'result': 'later'}, 90)
            else:
                return {'status': 'retry', 'reason': 'queue_full'}
            return result
    view.projector = Projector()
    await view.refresh('app', passive=True)
    assert 'execution' not in rows[0]
    assert all('session_ids' in row for row in published)
    assert not view.projected and not view._pending_inputs


async def test_actual_worker_large_requested_family_is_incremental(source, tmp_path):
    import os
    import time
    from amplifier_web.history_projection_process import ProcessProjector, INPUT_LIMIT, OUTPUT_LIMIT
    session, path = source
    family(session, path, 160)
    for number in range(160):
        child = f'child-{number}'
        leaf = event_path(session, child)
        with leaf.open('a') as stream:
            for item in range(112):
                stream.write(json.dumps({'event': 'tool:post', 'timestamp': 30 + item,
                    'data': {'session_id': child, 'tool_call_id': f'old-{item}',
                             'tool_name': 'bash', 'result': 'complete'}}) + '\n')
    owner = ProcessProjector(tmp_path / 'process-cache')
    try:
        inputs = copy.deepcopy(EventLogView.projection_input(session))
        assert len(models.encoded(inputs)) < INPUT_LIMIT
        started = time.monotonic()
        first = await owner.project(inputs, key=session['id'])
        assert first['status'] == 'complete', first
        cold = time.monotonic() - started
        session['execution'] = models.materialize_result(session, first)
        assert len(session['execution']['nodes']) >= 18000
        assert len(models.encoded(first)) < OUTPUT_LIMIT
        append(event_path(session, 'child-0'), 'tool:post',
               {'session_id': 'child-0', 'tool_call_id': 'leaf', 'result': 'finished'}, 200)
        inputs = copy.deepcopy(EventLogView.projection_input(session))
        assert len(models.encoded(inputs)) < INPUT_LIMIT
        started = time.monotonic()
        second = await owner.project(inputs, key=session['id'])
        warm = time.monotonic() - started
        assert second['status'] == 'complete' and 'delta' in second, second
        assert len(second['delta']['nodes']) <= 2
        assert 'events.model.rebuilt' not in second['diagnostics']['counters']
        assert len(models.encoded(second)) < 128 * 1024
        session['execution'] = models.materialize_result(session, second)
        expected = EventLogView(None).read(copy.deepcopy(EventLogView.projection_input(session)))
        assert session['execution'] == expected
        print(json.dumps({'nodes': len(session['execution']['nodes']), 'childPid': owner._process.pid,
            'hostPid': os.getpid(), 'coldSeconds': round(cold, 3), 'appendSeconds': round(warm, 3),
            'inputBytes': len(models.encoded(inputs)), 'coldResultBytes': len(models.encoded(first)),
            'appendResultBytes': len(models.encoded(second)), 'diagnostics': second['diagnostics']['counters']}))
    finally:
        await owner.close()


async def test_same_owner_coalesces_but_other_cached_owner_is_not_blocked(source, model):
    import asyncio
    from types import SimpleNamespace
    session, path = source
    family(session, path, 2)
    warm = {**session, 'id': 'warm'}
    rows = {'app': session, 'warm': warm}
    service = SimpleNamespace(_session=lambda identity: rows[identity], lock=asyncio.Lock(), closed=False,
                             _publish=lambda **kw: None)
    view = EventLogView(service)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Projector:
        async def project(self, inputs, *, key):
            calls.append(key)
            if key == 'app':
                entered.set()
                await release.wait()
            return await asyncio.to_thread(model.project, inputs)
    view.projector = Projector()
    await view.refresh('warm', passive=True)
    first = asyncio.create_task(view.refresh('app', passive=True))
    await entered.wait()
    duplicate = asyncio.create_task(view.refresh('app', passive=True))
    await asyncio.wait_for(view.refresh('warm', passive=True), .5)
    assert calls == ['warm', 'app']
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    await duplicate
    assert session['execution'] and not view._pending_inputs
    assert len(calls) == 2


async def test_default_explicit_refresh_keeps_unpublished_alias_contract_after_start(source):
    import asyncio
    import threading
    from types import SimpleNamespace
    session, path = source
    family(session, path, 2)
    service = SimpleNamespace(_session=lambda _: session, lock=asyncio.Lock(), closed=False,
                             _publish=lambda **kw: pytest.fail('stale work published'))
    view = EventLogView(service)
    class Never:
        async def project(self, *_args, **_kwargs):
            pytest.fail('explicit reference read silently switched to passive policy')
    view.projector = Never()
    read = view.read
    entered, release = threading.Event(), threading.Event()
    def delayed(inputs):
        result = read(inputs)
        entered.set()
        assert release.wait(3)
        return result
    view.read = delayed
    await service.lock.acquire()
    task = asyncio.create_task(view.refresh('app'))
    assert await asyncio.to_thread(entered.wait, 3)
    release.set()
    await asyncio.sleep(.02)
    session['messages'].append({'id': 'late', 'role': 'user', 'createdAt': 100})
    service.lock.release()
    await task
    assert 'execution' not in session


def test_progress_survives_compact_projection(source):
    from amplifier_web.browser_detail import project
    session, _ = source
    session['workProjection'] = {'status': 'retrying', 'reason': 'timeout'}
    assert project(session)['workProjection'] == session['workProjection']


@pytest.mark.parametrize('platform,enabled', [('linux', True), ('darwin', False), ('win32', False)])
async def test_process_rollout_requires_qualified_platform(tmp_path, monkeypatch, platform, enabled):
    import sys
    from types import SimpleNamespace
    from amplifier_web import history_projection_process
    instances = []
    class Process:
        def __init__(self, _home):
            instances.append(self)
        async def close(self):
            pass
    monkeypatch.setattr(history_projection_process, 'ProcessProjector', Process)
    monkeypatch.setattr(sys, 'platform', platform)
    view = EventLogView(SimpleNamespace(data_dir=tmp_path, closed=True))
    view.start()
    assert bool(instances) is enabled
    await view.close()


async def test_retry_cannot_replace_newer_status_while_waiting_for_publication_lock(source):
    import asyncio
    from types import SimpleNamespace
    session, _ = source
    entered = asyncio.Event()
    service = SimpleNamespace(_session=lambda _: session, lock=asyncio.Lock(), closed=False,
                             _publish=lambda **kw: None)
    view = EventLogView(service)
    class Projector:
        async def project(self, *_args, **_kwargs):
            await service.lock.acquire()
            entered.set()
            return {'status': 'retry', 'reason': 'worker_stopped'}
    view.projector = Projector()
    task = asyncio.create_task(view.refresh('app', passive=True))
    await entered.wait()
    await asyncio.sleep(0)
    session['workProjection'] = {'status': 'ready'}
    view.invalidate({'app'})
    service.lock.release()
    await task
    assert session['workProjection'] == {'status': 'ready'}


def test_durable_host_restart_rebuilds_missing_projection_inputs(source, tmp_path):
    from amplifier_web.session_projection import stored_execution
    session, path = source
    family(session, path, 2)
    home = tmp_path / 'host'
    first = models.SelectedHistoryReadModel(home)
    inspect(first, session)
    first.close()
    session['execution'] = stored_execution(session['execution'])
    second = models.SelectedHistoryReadModel(home)
    try:
        result = inspect(second, session)
        assert result['diagnostics']['counters']['events.model.rebuilt'] == 1
        assert len(session['execution']['nodes']) == 6
    finally:
        second.close()
