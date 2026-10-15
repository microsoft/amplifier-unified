"""Busy roots cannot starve a query whose effective canonical rows did not change."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import time

import pytest

from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.shell_query_model import ShellQueryModel
from amplifier_web.history_projection_process import ProcessProjector
from test_shell_modules import service
from test_shell_query_owner import snapshot, settle, started


class HeldModel(ShellQueryModel):
    def project(self, inputs, *, check_budget):
        after = (self.home / 'hold-after').exists()
        result = super().project(inputs, check_budget=check_budget) if after else None
        (self.home / 'query-started').touch()
        deadline = time.monotonic() + 8
        while not (self.home / 'query-release').exists():
            check_budget()
            if time.monotonic() > deadline:
                raise TimeoutError()
            time.sleep(.005)
        return result if after else super().project(inputs, check_budget=check_budget)


def held(home, **kwargs):
    kwargs['_command'] = (sys.executable, str(Path(__file__).resolve()), 'worker')
    return ProcessProjector(home, **kwargs)


def differences(left, right, path=()):
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys():
        return [item for key in left for item in differences(left[key], right[key], (*path, key))]
    if isinstance(left, (list, tuple)) and type(left) is type(right) and len(left) == len(right):
        return [item for i, (a, b) in enumerate(zip(left, right)) for item in differences(a, b, (*path, i))]
    return [(path, left, right)]


def setup(service, tmp_path, *, managed=True, status='working'):
    workspace = deepcopy(service._state['workspaces'][0])
    workspace['nativeProject'] = 'proof'
    roots = [{'id': f'busy-{i}', 'nativeIdentity': f'busy-{i}', 'nativeProject': 'proof',
        'sessionKind': 'root', 'title': f'Root {i}', 'workspaceId': workspace['id'],
        'workspace': workspace['path'], 'createdAt': 1, 'recentActivityAt': 10+i} for i in range(12)]
    project = {'workspace': workspace, 'sessions': roots}
    catalog = NativeCatalog(tmp_path/'native.sqlite3', tmp_path/'native-home')
    catalog.save({'proof': project}, {}, {}, ())
    template = service._state['sessions'][0]
    service._state['sessions'] = [{**deepcopy(template), **row, 'historyManaged': managed,
        'status': status, 'navigationActivityAt': row['recentActivityAt'], 'messages': []} for row in roots]
    service._state.update(selectedSessionId='busy-0', pinnedSessionIds=[row['id'] for row in roots])
    service.directory.bind(catalog)
    service._save_changes(sessions={row['id'] for row in roots}, globals={'workspaces', 'pinnedSessionIds'})
    service.clients.attach('browser').update(selectedSessionId='busy-0', selectedWorkspaceId=workspace['id'])
    return catalog, project


def oracle(service):
    from amplifier_web.history_directory_query import pinned_navigation
    return pinned_navigation(service.directory, service.state, [])


@pytest.mark.parametrize('after', [False, True])
@pytest.mark.parametrize('managed', [False, True])
async def test_twelve_busy_roots_settle_first_query_before_and_after_worker_read(service, tmp_path, after, managed):
    catalog, project = setup(service, tmp_path, managed=managed)
    owner = service.shell.resources
    owner.queries.factory = held
    if after:
        (service.data_dir/'hold-after').touch()
    expected = deepcopy(oracle(service))
    first = snapshot(service, wanted=('pinnedNavigation',))
    await started(service)
    jobs = list(owner.queries.jobs)
    fence = owner.queries._fence('pinned_navigation')
    keys = {name: deepcopy(values['pinnedNavigation']) for name, values in owner.clients['browser']['keys'].items()}
    for i in range(12):
        changed = deepcopy(project)
        changed['sessions'][i]['recentActivityAt'] += 100
        catalog.save({'proof': changed}, {'proof': project}, {}, ())
        project = changed
        service._publish_changes(globals={'sharedHistory'})
        snapshot(service, wanted=('pinnedNavigation',))
        current_keys = {name: values['pinnedNavigation'] for name, values in owner.clients['browser']['keys'].items()}
        assert current_keys == keys, differences(keys, current_keys)
        assert owner.queries._fence('pinned_navigation') == fence
        assert list(owner.queries.jobs) == jobs
    assert oracle(service) == expected
    (service.data_dir/'query-release').touch()
    complete = await settle(service, wanted=('pinnedNavigation',))
    assert not any(entry['pending'] for entry in complete['instances'].values())
    assert service.performance_diagnostics.counters['shell.query.completed'] == len(jobs)
    assert not service.performance_diagnostics.counters.get('shell.query.retry', 0)
    assert service.performance_diagnostics.counters['shell.query.queued'] == len(jobs)
    for entry in complete['instances'].values():
        assert entry['resources']['pinnedNavigation']['value']['pinnedNavigation'] == expected


@pytest.mark.parametrize('change', ['title', 'idle_recent', 'dormant_recent', 'membership', 'workspace', 'unknown'])
async def test_effective_receipt_preserves_real_and_unknown_invalidations(service, tmp_path, change):
    catalog, project = setup(service, tmp_path, status='idle' if change == 'idle_recent' else 'working')
    if change == 'dormant_recent':
        # A native-only root has no local overlay to hide its latest recency.
        row = deepcopy(project['sessions'][0]); row.update(id='dormant', nativeIdentity='dormant')
        updated = deepcopy(project); updated['sessions'].append(row)
        catalog.save({'proof': updated}, {'proof': project}, {}, ()); project = updated
    queries = service.shell.resources.queries
    before = queries._fence('pinned_navigation')
    updated = deepcopy(project)
    if change == 'title': updated['sessions'][0]['title'] = 'Changed title'
    elif change in ('idle_recent', 'dormant_recent'):
        updated['sessions'][-1 if change == 'dormant_recent' else 0]['recentActivityAt'] += 100
    elif change == 'membership': updated['sessions'][0]['sessionKind'] = 'worker'
    elif change == 'workspace': updated['workspace']['available'] = False
    else:
        with catalog.connect() as db:
            db.execute('UPDATE native_revisions SET root_navigation=root_navigation+1 WHERE namespace=?', (catalog.namespace,))
    if change != 'unknown': catalog.save({'proof': updated}, {'proof': project}, {}, ())
    assert queries._fence('pinned_navigation') != before
    if change == 'unknown':
        # A subsequent known no-op cannot bless the missed generation with a
        # previous effective identity, even if the raw counter is later reused.
        catalog.save({'proof': project}, {'proof': project}, {}, ())
        assert queries._fence('pinned_navigation') != before


async def test_duplicate_overlay_that_exposes_recent_prevents_discount(service, tmp_path):
    catalog, project = setup(service, tmp_path)
    duplicate = deepcopy(service._state['sessions'][0])
    duplicate.update(id='idle-alias', status='idle')
    service.directory.sync([duplicate], service._state)
    service.db.commit()
    queries = service.shell.resources.queries
    before = queries._fence('pinned_navigation')
    changed = deepcopy(project); changed['sessions'][0]['recentActivityAt'] += 100
    catalog.save({'proof': changed}, {'proof': project}, {}, ())
    assert queries._fence('pinned_navigation') != before


async def test_busy_to_idle_unmasks_latest_native_recency_and_restart_reuses_receipt(service, tmp_path):
    catalog, project = setup(service, tmp_path)
    queries = service.shell.resources.queries
    before = queries._fence('pinned_navigation')
    changed = deepcopy(project); changed['sessions'][0]['recentActivityAt'] += 100
    catalog.save({'proof': changed}, {'proof': project}, {}, ())
    assert queries._fence('pinned_navigation') == before
    with catalog.connect() as db:
        saved = db.execute('SELECT * FROM native_query_epochs').fetchall()
    catalog = NativeCatalog(catalog.path, catalog.home)
    catalog.save({'proof': changed}, {'proof': changed}, {}, ())
    with catalog.connect() as db:
        assert db.execute('SELECT * FROM native_query_epochs').fetchall() == saved
    assert queries._fence('pinned_navigation') == before
    service._state['sessions'][0]['status'] = 'idle'
    service._publish_changes(sessions={'busy-0'})
    assert queries._fence('pinned_navigation') != before


async def test_receipt_reads_only_admitted_points_with_large_unrelated_recency_table(service, tmp_path):
    from amplifier_web.native_query_receipts import receipt
    catalog, project = setup(service, tmp_path)
    service.directory.observation_revision  # Capture bounded admitted overlays.
    with catalog.connect() as db:
        epoch = db.execute('SELECT epoch FROM native_query_epochs').fetchone()[0]
        db.executemany('INSERT INTO native_query_recency VALUES(?,?,?,?,?)',
            ((catalog.namespace, 'other', str(i), epoch, 1) for i in range(27000)))
    steps = 0
    def progress():
        nonlocal steps
        steps += 100
        return int(steps > 5000)
    service.db.set_progress_handler(progress, 100)
    try:
        receipt(service.db, catalog.namespace, 'root_navigation', service.directory._live_values)
    finally:
        service.db.set_progress_handler(None, 0)
    assert steps < 5000


async def test_unchanged_observation_memo_does_not_repeat_admitted_receipt_lookups(service, tmp_path, monkeypatch):
    from amplifier_web import native_query_receipts
    setup(service, tmp_path)
    first = service.directory.observation_revision
    def forbidden(*args, **kwargs):
        raise AssertionError('Unchanged observation rebuilt effective native receipt')
    monkeypatch.setattr(native_query_receipts, 'receipt', forbidden)
    for _ in range(30):
        assert service.directory.observation_revision == first


async def test_failed_native_save_cannot_acknowledge_recency_receipts(service, tmp_path, monkeypatch):
    catalog, project = setup(service, tmp_path)
    queries = service.shell.resources.queries
    before = queries._fence('pinned_navigation')
    with catalog.connect() as db:
        raw = db.execute('SELECT * FROM native_revisions').fetchall()
        receipt = db.execute('SELECT * FROM native_query_epochs').fetchall()
    changed = deepcopy(project)
    changed['sessions'][0]['recentActivityAt'] += 100
    changed['sessions'][1]['recentActivityAt'] += 100
    calls = 0
    def stop():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise InterruptedError('Controlled native write interruption')
    monkeypatch.setattr(catalog, '_check_running', stop)
    with pytest.raises(InterruptedError):
        catalog.save({'proof': changed}, {'proof': project}, {}, ())
    with catalog.connect() as db:
        assert db.execute('SELECT * FROM native_revisions').fetchall() == raw
        assert db.execute('SELECT * FROM native_query_epochs').fetchall() == receipt
        assert db.execute('SELECT COUNT(*) FROM native_query_recency').fetchone()[0] == 0
    assert queries._fence('pinned_navigation') == before


if __name__ == '__main__':
    import argparse
    from amplifier_web.history_projection_worker import run
    from amplifier_web.shell_query_worker import INPUT_LIMIT, OUTPUT_LIMIT
    parser = argparse.ArgumentParser(); parser.add_argument('worker')
    parser.add_argument('--spool'); parser.add_argument('--cache-home'); parser.add_argument('--memory-limit', type=int)
    args = parser.parse_args()
    run(args.spool, args.cache_home, model_factory=HeldModel, memory_limit=args.memory_limit,
        input_limit=INPUT_LIMIT, output_limit=OUTPUT_LIMIT)
