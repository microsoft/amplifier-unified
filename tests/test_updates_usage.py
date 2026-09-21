"""Usage evidence follows shared runtime settings without treating history as errors."""
import copy
import asyncio
import json
from pathlib import Path
import threading

import pytest

from test_updates_cache import cached, repository, service
from amplifier_web.host import config
from amplifier_web.shared_settings import settings_paths
from amplifier_web.updates import configured_sources


@pytest.mark.parametrize('historical,read_only', [(True, True), (True, False), (False, True)])
async def test_only_explicit_read_only_native_history_skips_runtime_id_validation(service, tmp_path, historical, read_only):
    service.state['sessions'].append({
        'id': 'legacy-view', 'nativeIdentity': 'old-child_foundation:file-ops',
        'workspace': str(tmp_path), 'bundle': 'unregistered-historical-composition',
        'historyManaged': historical,
        'historyReadOnlyReason': 'Worker sessions are read-only.' if read_only else None,
    })
    before = copy.deepcopy(service.state)
    _, incomplete = configured_sources(service)
    assert incomplete == (not (historical and read_only))
    assert service.state == before


async def test_source_classification_leaves_event_loop_free_and_uses_detached_selection_snapshot(service, monkeypatch):
    main_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    before = service.state['settings']['bundle']
    service.state['sessions'].append({'id': 'original', 'workspace': '/original', 'bundle': 'original'})
    service.state['workspaces'].append(
        {'nativeProject': 'original', 'path': '/original', 'available': True}
    )
    captured = {}
    def classify(snapshot):
        entered.set()
        assert threading.get_ident() != main_thread
        assert release.wait(3), 'The event loop must remain available during settings reads'
        captured.update(snapshot.state)
        return {}, False
    monkeypatch.setattr('amplifier_web.updates.configured_sources', classify)
    pending = asyncio.create_task(service.update_manager.inventory_sources())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        service.state['settings']['bundle'] = 'changed'
        service.state['sessions'][-1]['bundle'] = 'changed'
        service.state['workspaces'][-1]['nativeProject'] = 'changed'
        service.state['workspaces'][-1]['path'] = '/changed'
        service.state['workspaces'][-1]['available'] = False
    finally:
        release.set()
        await pending
    assert captured['settings']['bundle'] == before
    assert captured['sessions'][-1]['bundle'] == 'original'
    assert captured['workspaces'][-1]['nativeProject'] == 'original'
    assert captured['workspaces'][-1]['path'] == '/original'
    assert captured['workspaces'][-1]['available'] is True


@pytest.mark.parametrize('identity_field', ['runtimeSessionId', 'nativeIdentity', 'id'])
async def test_usage_reads_shared_scopes_and_actual_runtime_session(
    repository, service, tmp_path, monkeypatch, identity_field,
):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    session_id = 'runtime-session'
    paths = settings_paths(workspace, session_id=session_id)
    names = ['global', 'project', 'local', 'session']
    for scope, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('sources:\n  modules:\n    tool-'+scope+': git+https://example.invalid/'+scope+'@main\n')
    # Obsolete app-private copies must not override the authoritative files.
    private = service.data_dir/'config/settings.yaml'
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text('sources:\n  modules:\n    tool-obsolete: git+https://example.invalid/obsolete@main\n')
    service.state['sessions'].append({
        'id': 'visible-session', 'nativeIdentity': 'native-fallback',
        'workspace': str(workspace), 'bundle': 'git+https://example.invalid/repo@main',
        identity_field: session_id,
    })
    if identity_field == 'id':
        service.state['sessions'][-1].pop('nativeIdentity')
    for name in names+['obsolete']:
        cached(service, repository, name)
    watched = [*paths.values(), private]
    before = {p: p.read_bytes() for p in watched}
    def forbidden(*args, **kwargs):
        pytest.fail('Usage inventory must not load keys or prepare registry caches')
    monkeypatch.setattr(config, '_load_keys', forbidden)
    monkeypatch.setattr(config, 'prepare_registry', forbidden)
    rows = {r['label'].split('/')[-1]: r for r in await service.update_manager.inventory_sources()}
    for name in names:
        assert rows[name]['usage'] == 'configured', name
        assert rows[name]['eligible']
    assert rows['obsolete']['usage'] == 'unknown'
    assert rows['obsolete']['eligible']
    assert before == {p: p.read_bytes() for p in watched}


async def test_pathless_native_workspace_placeholders_do_not_raise_usage_warning(
    repository, service, tmp_path,
):
    workspace = tmp_path/'configured-workspace'
    workspace.mkdir()
    settings = workspace/'.amplifier/settings.yaml'
    settings.parent.mkdir()
    settings.write_text('bundle:\n  active: git+https://example.invalid/configured@main\n')
    configured = cached(service, repository, 'configured')
    historical = cached(service, repository, 'historical')
    service.state['workspaces'].extend([
        {'id': 'configured', 'path': str(workspace), 'available': True},
        {'id': 'unknown-native-project', 'nativeProject': 'unknown-native-project',
         'path': None, 'available': False},
        {'id': 'unresolved-native-project', 'nativeProject': 'unresolved-native-project',
         'path': None, 'available': False},
    ])
    before = copy.deepcopy(service.state)
    _, incomplete = configured_sources(service)
    assert not incomplete
    rows = {row['label'].split('/')[-1]: row for row in await service.update_manager.inventory_sources()}
    assert 'Source configuration' not in rows
    assert rows['configured']['usage'] == 'configured' and rows['configured']['eligible']
    assert rows['historical']['usage'] == 'unknown' and rows['historical']['eligible']
    assert service.state == before
    assert configured.is_dir() and historical.is_dir()


@pytest.mark.parametrize('workspace', [
    {'id': 'missing-path', 'available': False},
    {'id': 'pathless-non-native', 'path': None, 'available': False},
    {'id': 'available-pathless', 'path': None, 'available': True},
    {'id': 'empty-path', 'path': '', 'available': False},
])
async def test_only_pathless_unavailable_native_workspace_sentinel_skips_source_configuration_warning(
    repository, service, workspace,
):
    cached(service, repository, 'historical')
    service.state['workspaces'].append(workspace)
    _, incomplete = configured_sources(service)
    assert incomplete
    rows = {row['label'].split('/')[-1]: row for row in await service.update_manager.inventory_sources()}
    assert rows['Source configuration']['status'] == 'check_failed'
    assert rows['historical']['usage'] == 'unknown' and rows['historical']['eligible']


async def test_returned_workspace_uses_filesystem_availability(repository, service, tmp_path):
    workspace = tmp_path/'returned-workspace'
    workspace.mkdir()
    settings = workspace/'.amplifier/settings.yaml'
    settings.parent.mkdir()
    settings.write_text('bundle:\n  active: git+https://example.invalid/returned@main\n')
    service.state['workspaces'].append({'id': 'returned', 'path': str(workspace), 'available': False})
    cached(service, repository, 'returned')
    row = (await service.update_manager.inventory_sources())[0]
    assert row['usage'] == 'configured'


def test_read_only_config_has_runtime_merge_rules(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    paths = settings_paths(tmp_path, session_id='session')
    for scope, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'config': {'providers': [{'module': 'provider-example', 'config': {scope: True, 'model': scope}}]}}))
    before = {p: p.read_bytes() for p in paths.values()}
    read = config.read_config(tmp_path, home=tmp_path/'app', session_id='session')
    loaded = config.load_config(tmp_path, home=tmp_path/'app', session_id='session')
    assert read.settings == loaded.settings
    assert read.providers[0]['config'] == {'global': True, 'project': True, 'local': True, 'session': True, 'model': 'session'}
    assert before == {p: p.read_bytes() for p in paths.values()}
    assert not (tmp_path/'app').exists()


@pytest.mark.parametrize('scope', ['project', 'user'])
@pytest.mark.parametrize('entry', ['lane/bundle.md', 'lane.md', 'lane.yaml', 'lane.yml'])
async def test_cli_local_bundles_resolve_for_inventory_and_preparation(
    service, tmp_path, scope, entry,
):
    from amplifier_web.host.session import load_root_bundle
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    shared = settings_paths(workspace)['global'].parent
    base = workspace/'.amplifier' if scope == 'project' else shared
    path = base/'bundles'/entry
    path.parent.mkdir(parents=True, exist_ok=True)
    document = 'bundle:\n  name: lane\n'
    path.write_text('---\n'+document+'---\nLocal instructions.\n' if path.suffix == '.md' else document)
    service.state['settings'].update(workspace=str(workspace), bundle='lane')
    service.state['workspaces'] = []
    before = path.read_bytes()
    sources, incomplete = configured_sources(service)
    assert not incomplete
    assert all('Selected bundle' not in evidence for evidence in sources.values())
    read = config.read_config(workspace, home=service.data_dir)
    registry, _, chosen = await load_root_bundle(read, 'lane')
    assert 'lane' in registry.list_registered()
    assert Path(chosen) == (path.parent if path.name == 'bundle.md' else path)
    assert path.read_bytes() == before


async def test_unresolved_local_name_still_reports_source_configuration_warning(
    repository, service, tmp_path,
):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    service.state['settings'].update(workspace=str(workspace), bundle='missing-lane')
    cached(service, repository, 'historical')
    _, incomplete = configured_sources(service)
    assert incomplete
    rows = {row['label'].split('/')[-1]: row for row in await service.update_manager.inventory_sources()}
    assert rows['Source configuration']['status'] == 'check_failed'
    assert rows['historical']['usage'] == 'unknown' and rows['historical']['eligible']


async def test_configuration_warning_identifies_affected_source_and_session(service, tmp_path):
    service.state['sessions'].append({'id':'broken-root','workspace':str(tmp_path),'bundle':'missing-work-bundle'})
    rows=await service.update_manager.inventory_sources()
    warning=next(row for row in rows if row['id']=='source-configuration')
    issue=next(row for row in warning['sourceIssues'] if row.get('sessionId')=='broken-root')
    assert issue['reference']=='missing-work-bundle'
    assert issue['workspace']==str(tmp_path)
    assert 'Choose an available bundle' in issue['reason']
    assert not warning['eligible']


async def test_old_unregistered_bundle_is_historical_until_selected(service, tmp_path):
    source={'id':'old-app-id','runtimeSessionId':'old-runtime-id','workspace':str(tmp_path),'bundle':'converge-w4','historyManaged':True}
    service.state['sessions'].append(source)
    rows=await service.update_manager.inventory_sources()
    assert not any(row['id']=='source-configuration' for row in rows)
    history=next(row for row in rows if row['kind']=='history')
    assert history['sourceIssues'][0]['appSessionId']=='old-app-id'
    assert history['sourceIssues'][0]['sessionId']=='old-runtime-id'
    assert history['status']=='historical' and not history['eligible']
    assert source['bundle']=='converge-w4'
    service.state['selectedSessionId']=source['id']
    rows=await service.update_manager.inventory_sources()
    assert any(row['id']=='source-configuration' for row in rows)
