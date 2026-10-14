"""Project coverage and session facts have separate invalidation owners."""
from copy import deepcopy
import time

import pytest

from amplifier_web.native_catalog import NativeCatalog, CatalogRows, CatalogPatchRows
from amplifier_web.performance_diagnostics import PerformanceDiagnostics
from amplifier_web.shell_resources import scope_for
from test_shell_modules import service


def project(path, *, identity='project', rows=()):
    workspace = dict(id=identity, nativeProject=identity, path=str(path), name=identity,
                     available=True, sessionCount=len(rows), workerSessionCount=0, internalSessionCount=0)
    return {'workspace': workspace, 'sessions': list(rows)}


def delta(catalog, before):
    return {key: value - before[key] for key, value in catalog.generations().items()}


def test_empty_project_coverage_changes_preserve_session_generations(tmp_path):
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    current = {}
    empty = project(tmp_path / 'workspace')
    for following in ({'project': empty},
                      {'project': {**empty, 'workspace': {**empty['workspace'], 'id': 'moved',
                                      'path': str(tmp_path / 'moved'), 'available': False}}}, {}):
        before = catalog.generations()
        assert catalog.save(following, current, {}, ()) == {'sessions': [], 'removed': []}
        assert delta(catalog, before) == dict(revision=1, observation_sessions=0,
                                             observation_membership=0, navigation=0)
        assert catalog.count() == 0
        assert {key: value['workspace'] for key, value in catalog.load_projects().items()} == {
            key: value['workspace'] for key, value in following.items()}
        current = following
    before = catalog.generations()
    catalog.save({}, {}, {}, ())
    assert catalog.generations() == before


@pytest.mark.parametrize('kind', ['root', 'worker', 'internal'])
def test_first_and_last_row_still_change_all_session_generations(tmp_path, kind):
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    empty = project(tmp_path / 'workspace')
    catalog.save({'project': empty}, {}, {}, ())
    row = dict(id='saved', nativeIdentity='saved', nativeProject='project', title='Saved',
               sessionKind=kind, workspaceId='project', workspace=str(tmp_path / 'workspace'))
    full = {**empty, 'sessions': [row]}
    before = catalog.generations()
    catalog.save({'project': full}, {'project': empty}, {}, ())
    assert delta(catalog, before) == dict(revision=1, observation_sessions=1,
                                         observation_membership=1, navigation=1)
    before = catalog.generations()
    assert catalog.save({'project': empty}, {'project': full}, {}, ())['removed'] == [('project', 'saved')]
    assert delta(catalog, before) == dict(revision=1, observation_sessions=1,
                                         observation_membership=1, navigation=1)
    assert catalog.count() == 0


@pytest.mark.parametrize('missing_previous', [False, True])
def test_workspace_changes_use_actual_saved_membership_not_empty_patch(tmp_path, missing_previous):
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    row = dict(id='saved', nativeIdentity='saved', nativeProject='project', title='Saved', sessionKind='root',
               workspaceId='project', workspace=str(tmp_path / 'workspace'))
    original = project(tmp_path / 'workspace', rows=[row])
    catalog.save({'project': original}, {}, {}, ())
    prior = {**original, 'sessions': CatalogRows(catalog, 'project')}
    changed = {'workspace': {**original['workspace'], 'id': 'relocated', 'path': str(tmp_path / 'moved'),
                            'available': False, 'sessionCount': 0},
               'sessions': CatalogPatchRows(prior['sessions'], [])}
    before = catalog.generations()
    catalog.save({'project': changed}, {} if missing_previous else {'project': prior}, {}, ())
    assert delta(catalog, before) == dict(revision=1, observation_sessions=1,
                                         observation_membership=1, navigation=1)
    with catalog.connect() as db:
        assert db.execute('SELECT workspace_id,workspace_path,available FROM native_rows').fetchone() == (
            'relocated', str(tmp_path / 'moved'), 0)
    before = catalog.generations()
    catalog.save({}, {'project': changed}, {}, ())
    assert delta(catalog, before) == dict(revision=1, observation_sessions=1,
                                         observation_membership=1, navigation=1)


def test_repeated_absence_keeps_repair_receipt_without_changing_committed_facts(tmp_path, monkeypatch):
    diagnostics = PerformanceDiagnostics()
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home', diagnostics=diagnostics)
    empty = project(tmp_path / 'workspace')
    catalog.save({'project': empty}, {}, {}, ())
    row = dict(id='saved', nativeIdentity='saved', nativeProject='project', title='Saved', sessionKind='root',
               workspaceId='project', workspace=str(tmp_path / 'workspace'))
    full = {**empty, 'sessions': [row]}
    catalog.save({'project': full}, {'project': empty}, {}, ())
    prior = {**full, 'sessions': CatalogRows(catalog, 'project')}
    removal = {**empty, 'sessions': CatalogPatchRows(prior['sessions'], [], ['saved'])}
    before = catalog.generations()
    first = catalog.save({'project': removal}, {'project': prior}, {}, ())
    assert delta(catalog, before) == dict(revision=1, observation_sessions=1,
                                         observation_membership=1, navigation=1)
    before = catalog.generations()
    repeated = catalog.save({'project': removal}, {'project': prior}, {}, ())
    assert repeated == first == {'sessions': [], 'removed': [('project', 'saved')]}
    assert delta(catalog, before) == dict(revision=1, observation_sessions=0,
                                         observation_membership=0, navigation=0)
    assert diagnostics.snapshot()['counters'] == {'catalog.write.project.add.empty': 1,
        'catalog.write.row.upsert': 1, 'catalog.write.row.remove': 1, 'catalog.write.row.absent': 1}
    # A failed save must not expose metadata or count uncommitted decisions.
    import amplifier_web.native_catalog as module
    def fail(*args): raise ValueError('synthetic scalar failure')
    monkeypatch.setattr(module, 'row_scalars', fail)
    before = catalog.generations()
    counters = diagnostics.snapshot()['counters']
    with pytest.raises(ValueError, match='synthetic scalar failure'):
        catalog.save({'project': full}, {'project': empty}, {}, ())
    assert catalog.generations() == before and catalog.count() == 0
    assert diagnostics.snapshot()['counters'] == counters


def test_repeated_probe_metadata_notifications_do_not_invalidate_chat_facts(tmp_path, monkeypatch):
    from amplifier_web.native_history import NativeHistory
    from amplifier_web.session_files import project_slug
    from test_native_history import session, write_json
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    session(home, workspace, 'real-chat', {'working_dir': str(workspace), 'bundle': 'work'})
    probe = session(home, workspace, 'runtime-probe', {'working_dir': str(workspace)}, transcript=False)
    cache = tmp_path / 'native.sqlite3'
    initial = NativeHistory(home, cache_path=cache)
    initial.scan()
    initial.close()
    diagnostics = PerformanceDiagnostics()
    index = NativeHistory(home, cache_path=cache, directory=True, watch=True, diagnostics=diagnostics)
    try:
        monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
        index._reconcile_at = time.monotonic() + 900
        token, _ = index.scan_changes()
        before = index._catalog.generations()
        slug = project_slug(workspace)
        monkeypatch.setattr(index, '_invalidations', lambda: (True, {(slug, 'runtime-probe', 'metadata')}))
        for number in range(3):
            write_json(probe / 'metadata.json', {'working_dir': str(workspace), 'probe_sequence': number})
            token, changed = index.scan_changes(since=token)
            assert changed['sessions'] == []
            assert changed['removed'] == [(slug, 'runtime-probe')]
        assert delta(index._catalog, before) == dict(revision=3, observation_sessions=0,
                                                    observation_membership=0, navigation=0)
        assert index._catalog.count() == 1
        assert diagnostics.snapshot()['counters'] == {'catalog.write.row.absent': 3}
    finally:
        index.close()


async def test_empty_project_changes_reuse_root_pages_but_registered_workspace_changes_refresh(service, tmp_path, monkeypatch):
    assert service.history.index.diagnostics is service.performance_diagnostics
    catalog = NativeCatalog(tmp_path / 'native.sqlite3', tmp_path / 'home')
    service._save_changes(sessions={'alpha', 'beta'})
    service.directory.bind(catalog)
    service.clients.attach('project-pages')
    calls = []
    original = service.shell.navigation
    def observed(*args, **kwargs):
        calls.append(frozenset(kwargs.get('navigation_resources', ())))
        return original(*args, **kwargs)
    monkeypatch.setattr(service.shell, 'navigation', observed)
    with service.clients.bind('project-pages'):
        owner = service.shell.resources
        owner.register('project-pages', 1, ['recentNavigation', 'pinnedNavigation', 'workspaceShortcuts',
                                          'workspaceExplorer'], scope_for(service.state))
        first = owner.snapshot('project-pages')
        frozen = deepcopy(first)
        prior = {}
        empty = project(tmp_path / 'new-workspace')
        absent = {**empty, 'sessions': CatalogPatchRows(CatalogRows(catalog, 'project'), [], ['absent'])}
        for following in ({'project': empty}, {}, {'project': empty}, {'project': absent}, {'project': absent}):
            calls.clear()
            catalog.save(following, prior, {}, ())
            service._publish_changes(globals={'sharedHistory'})
            assert owner.snapshot('project-pages') is first
            assert not any(calls), 'empty native coverage must not build navigation pages'
            assert first == frozen
            prior = following
        # Workspace discovery has its own actual changed-global owner. Keeping
        # session facts stable must not hide a newly registered empty folder.
        calls.clear()
        service.state['workspaces'].append(deepcopy(empty['workspace']))
        service._publish_changes(globals={'workspaces'})
        following = owner.snapshot('project-pages')
        assert following != first
        assert any('workspaceExplorer' in call for call in calls)
        assert (following['instances']['workspaces']['resources']['workspaceExplorer']['value'] !=
                first['instances']['workspaces']['resources']['workspaceExplorer']['value'])
        assert first == frozen
