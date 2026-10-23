"""Passive polling skips quiet graphs; explicit and dirty reads stay exact."""
import asyncio
import copy
import gc
import json
import threading
import weakref
from types import SimpleNamespace

import pytest

from amplifier_web.event_log_view import EventLogView, event_path
from amplifier_web.service import AppService
from test_automatic_history import app_factory


class Owner(dict):
    pass


def append(path, name, data, at=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps({'event': name, 'timestamp': at,
            'data': {'session_id': 'native', **data}}) + '\n')


@pytest.fixture
def observed(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    owner = Owner(id='app', runtimeSessionId='native', workspace=str(tmp_path / 'project'),
                  messages=[{'id': 'user', 'role': 'user', 'createdAt': 1}], workers=[])
    current = [owner]
    service = SimpleNamespace(_session=lambda _: current[0], lock=asyncio.Lock(), closed=False)
    view = EventLogView(service)
    service.event_log_view = view
    service._publish = lambda **kw: view.invalidate(kw.get('session_ids'))
    path = event_path(owner, 'native')
    append(path, 'tool:pre', {'tool_call_id': 'one', 'tool_name': 'bash'})
    return service, view, current, path


async def test_quiet_passive_poll_never_rebuilds_comparison_input(observed, monkeypatch):
    _, view, current, _ = observed
    # Preserve a substantial legacy tree as well as the canonical tool row.
    current[0]['execution'] = {'nodes': [{'id': f'old-{n}', 'kind': 'tool',
        'phase': 'completed', 'output': 'saved legacy body'} for n in range(3000)], 'turns': []}
    await view.refresh('app')
    expected = copy.deepcopy(current[0]['execution'])
    def forbidden(*args):
        raise AssertionError('quiet poll walked the selected execution tree')
    monkeypatch.setattr(view, 'projection_input', forbidden)
    for _ in range(20):
        await view.refresh('app', passive=True)
    assert current[0]['execution'] == expected


@pytest.mark.parametrize('change', ['append', 'replace', 'transcript', 'explicit-alias'])
async def test_source_and_explicit_alias_changes_remain_exact(observed, change):
    _, view, current, path = observed
    await view.refresh('app')
    old = view.projected['app']
    if change == 'append':
        append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'finished'}, 12)
    elif change == 'replace':
        replacement = path.with_suffix('.replacement')
        append(replacement, 'tool:post', {'tool_call_id': 'two', 'result': 'replacement'}, 15)
        replacement.replace(path)
    elif change == 'transcript':
        (path.parent.parent / 'transcript.jsonl').write_text(json.dumps({'role': 'user', 'content': 'hi'})+'\n')
    else:
        current[0]['execution']['nodes'].append({'id': 'legacy', 'kind': 'tool', 'output': {'text': 'unpublished'}})
    await view.refresh('app', passive=change != 'explicit-alias')
    assert view.projected['app'] is not old
    if change == 'append':
        assert current[0]['execution']['nodes'][0]['output'] == 'finished'
    elif change == 'explicit-alias':
        assert next(row for row in current[0]['execution']['nodes'] if row['id'] == 'legacy')['output']['text'] == 'unpublished'


async def test_deferred_progress_invalidates_before_state_revision_changes(observed):
    service, view, current, _ = observed
    await view.refresh('app')
    service.state = {'revision': 8}
    service._progress_publish_task = object()  # Already scheduled; no test task to leak.
    current[0]['execution']['nodes'].append({'id': 'owned-call', 'kind': 'llm',
        'phase': 'completed', 'usage': {'totalTokens': 17}, 'producerId': 'owned'})
    AppService._publish_progress(service, session_ids={'app'}, detail_only=True)
    assert service.state['revision'] == 8 and service._progress_dirty
    assert 'app' not in view._clean_inputs
    await view.refresh('app', passive=True)
    assert current[0]['execution']['aggregateUsage']['totalTokens'] == 17


async def test_failed_publication_does_not_leave_clean_receipt(observed):
    service, view, _, _ = observed
    await view.refresh('app')
    service.state = {'revision': 8}
    service._save = lambda: (_ for _ in ()).throw(OSError('fixture save failure'))
    with pytest.raises(OSError, match='fixture save failure'):
        AppService._publish(service, session_ids={'app'}, record_only=True)
    assert service.state['revision'] == 8 and service._progress_dirty
    assert 'app' not in view._clean_inputs


async def test_same_revision_saved_accounting_invalidates_without_publication(app_factory):
    from amplifier_web.execution import ingest
    app = app_factory()
    row = app._new_session({'title': 'Accounting owner'})
    app.state.update(sessions=[row], selectedSessionId=row['id'])
    app._save()
    await app.event_log_view.refresh(row['id'])
    assert row['id'] in app.event_log_view._clean_inputs
    revision = app.state['revision']
    ingest(row, {'id': 'admitted-call', 'kind': 'llm', 'phase': 'completed',
        'sessionId': row['id'], 'rootSessionId': row['id'], 'producerId': 'producer',
        'usage': {'totalTokens': 17}, 'liveObservation': True})
    # Capacity admission durably saves its receipt without publishing a frame.
    app._save_changes(sessions={row['id']})
    assert app.state['revision'] == revision
    assert row['id'] not in app.event_log_view._clean_inputs
    await app.event_log_view.refresh(row['id'], passive=True)
    assert row['execution']['aggregateUsage']['totalTokens'] == 17


async def test_scoped_and_unknown_publications_invalidate_only_their_receipts(observed):
    service, view, _, _ = observed
    await view.refresh('app')
    service._progress_publish_task = object()
    receipt = view._clean_inputs['app']
    AppService._publish_progress(service, session_ids={'other'})
    assert view._clean_inputs['app'] is receipt
    AppService._publish_progress(service)
    assert not view._clean_inputs


async def test_ignored_worker_activity_preserves_passive_receipt_and_session(observed):
    service, view, current, _ = observed
    await view.refresh('app')
    receipt = view._clean_inputs['app']
    before = copy.deepcopy(current[0])
    # Orphan activity has no lifecycle authority: reject it before mutation or
    # invalidation. The full stale/settled-run matrix lives in worker_lifecycle.
    await AppService.on_runtime_event(service, 'worker.updated',
        {'rootSessionId': 'app', 'id': 'missing', 'activityOnly': True})
    assert view._clean_inputs['app'] is receipt
    assert current[0] == before


async def test_current_worker_activity_invalidates_only_owner_before_deferred_publish(app_factory):
    app = app_factory()
    owner = app._new_session({'title': 'Activity owner'})
    other = app._new_session({'title': 'Unrelated owner'})
    app.state.update(sessions=[owner, other], selectedSessionId=owner['id'])
    await app.on_runtime_event('worker.updated', {
        'rootSessionId': owner['id'], 'id': 'child', 'runId': 'current', 'status': 'running'})
    view = app.event_log_view
    for row in (owner, other):
        await view.refresh(row['id'])
    owner_receipt = view._clean_inputs[owner['id']]
    other_receipt = view._clean_inputs[other['id']]
    other_before = copy.deepcopy(other)
    revision = app.state['revision']
    await app.on_runtime_event('worker.updated', {
        'rootSessionId': owner['id'], 'id': 'child', 'runId': 'current',
        'activityOnly': True, 'phase': 'retrying', 'detail': 'Retrying', 'updatedAt': 22})
    # Real ingress and progress batching, with no yield to its deferred publisher.
    assert app.state['revision'] == revision and app._progress_dirty
    assert app._progress_publish_task is not None and not app._progress_publish_task.done()
    assert owner['workers'][0]['phase'] == 'retrying'
    assert owner['workers'][0]['status'] == 'running'
    assert view._clean_inputs.get(owner['id']) is not owner_receipt
    assert owner['id'] not in view._clean_inputs
    assert view._clean_inputs[other['id']] is other_receipt
    assert other == other_before


async def test_new_owner_with_same_identity_cannot_reuse_or_be_pinned(observed, monkeypatch):
    _, view, current, _ = observed
    await view.refresh('app')
    old = weakref.ref(current[0])
    current[0] = Owner(copy.deepcopy(current[0]))
    gc.collect()
    assert old() is None  # Receipts must not defeat residency retirement.
    calls = []
    original = view.projection_input
    monkeypatch.setattr(view, 'projection_input', lambda row: (calls.append(True), original(row))[1])
    await view.refresh('app', passive=True)
    assert calls


async def test_invalidation_during_stat_cannot_reuse_prior_receipt(observed, monkeypatch):
    _, view, current, path = observed
    await view.refresh('app')
    entered, release = threading.Event(), threading.Event()
    original = type(path).stat
    def delayed(target, *args, **kwargs):
        if target == path:
            entered.set()
            assert release.wait(5)
        return original(target, *args, **kwargs)
    monkeypatch.setattr(type(path), 'stat', delayed)
    task = asyncio.create_task(view.refresh('app', passive=True))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        current[0]['execution']['nodes'].append({'id': 'late', 'kind': 'tool', 'output': 'late body'})
        view.invalidate({'app'})
        release.set()
        await task
        assert any(row['id'] == 'late' for row in view.projected['app'][0]['execution']['nodes'])
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_passive_rebuild_still_rejects_unpublished_mutation_during_read(observed, monkeypatch):
    _, view, current, path = observed
    await view.refresh('app')
    append(path, 'tool:post', {'tool_call_id': 'one', 'result': 'finished'}, 12)
    entered, release = threading.Event(), threading.Event()
    original = view.read
    def delayed(value):
        entered.set()
        assert release.wait(5)
        return original(value)
    monkeypatch.setattr(view, 'read', delayed)
    tree = current[0]['execution']
    task = asyncio.create_task(view.refresh('app', passive=True))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        tree['nodes'][0].update(phase='interrupted', endedAt=20)
        release.set()
        await task
        assert current[0]['execution'] is tree
        assert tree['nodes'][0]['phase'] == 'interrupted'
        assert 'app' not in view._clean_inputs
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('retire_projection', [False, True])
async def test_receipts_follow_projection_eviction_bound(observed, retire_projection):
    _, view, current, _ = observed
    for n in range(70):
        current[0] = Owner(id=f'app-{n}', workspace=current[0]['workspace'], messages=[], workers=[])
        await view.refresh(current[0]['id'])
        if retire_projection:
            view.projected.pop(current[0]['id'])
            view.read_paths.pop(current[0]['id'], None)
            view.read_revisions.pop(current[0]['id'], None)
    assert len(view._clean_inputs) == 64
    if not retire_projection:
        assert set(view._clean_inputs) == set(view.projected)
