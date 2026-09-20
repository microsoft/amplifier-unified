"""Usage evidence follows shared runtime settings without treating history as errors."""
import copy
import json
from pathlib import Path

import pytest

from test_updates_cache import cached, repository, service
from amplifier_web.host import config
from amplifier_web.shared_settings import settings_paths
from amplifier_web.updates import configured_sources


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


async def test_retained_missing_workspaces_and_sessions_do_not_raise_usage_warning(
    repository, service, tmp_path,
):
    root = cached(service, repository, 'historical')
    missing = tmp_path/'unavailable'
    service.state['workspaces'].append({'id': 'missing', 'path': str(missing), 'available': False})
    service.state['workspaces'].extend([
        {'id': 'unknown-native-project', 'path': None, 'available': False},
        {'id': 'unresolved-native-project', 'available': False},
        {'id': 'empty-native-project', 'path': '', 'available': False},
    ])
    service.state['sessions'].append({'id': 'old-session', 'workspace': str(missing), 'bundle': 'old-alias'})
    before = copy.deepcopy(service.state)
    _, incomplete = configured_sources(service)
    assert not incomplete
    rows = await service.update_manager.inventory_sources()
    assert len(rows) == 1
    assert rows[0]['usage'] == 'unknown' and rows[0]['eligible']
    assert service.state == before
    assert root.is_dir() and not missing.exists()


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


def test_unresolved_local_name_still_reports_incomplete_configuration(service, tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    service.state['settings'].update(workspace=str(workspace), bundle='missing-lane')
    _, incomplete = configured_sources(service)
    assert incomplete
