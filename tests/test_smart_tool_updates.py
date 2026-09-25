"""Managed updates must keep the old executable usable until replacement is ready."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import shlex
from types import SimpleNamespace
import pytest
from test_smart_tools import Service, FIXTURE
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.smart_tool_updates import retarget

@pytest.fixture
async def managed(tmp_path):
    manager = SmartToolsManager(Service(tmp_path))
    for identity in ('old', 'new'):
        base = tmp_path / identity
        (base / 'bin').mkdir(parents=True)
        executable = base / 'bin/python'
        executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' \"$@\"\n')
        executable.chmod(0o700)
        (base / 'source').mkdir()
        manager.state['installations'].append({'id': identity, 'name': 'Example', 'repository': 'https://example.invalid/tool',
            'ref': 'main', 'commit': ('a' if identity == 'old' else 'b') * 40,
            'binDir': str(base / 'bin'), 'sourceDir': str(base / 'source'),
            **({'stagedFrom': 'old'} if identity == 'new' else {})})
    await manager.configure({'id': 'board', 'name': 'My board', 'command': str(tmp_path / 'old/bin/python'),
        'args': [str(FIXTURE), str(tmp_path / 'data')], 'env': {}, 'installationId': 'old'})
    yield manager
    await manager.close()

async def test_activation_and_rollback_preserve_settings_and_never_call_tools(managed, tmp_path):
    await managed.connect('board')
    before = copy.deepcopy(managed._server('board'))
    old = managed.connections['board']
    count = len(managed.service.published)
    await managed.activate_update('old', 'new')
    current = managed._server('board')
    assert current['installationId'] == 'new'
    assert old.task.done()
    assert current['args'] == before['args'] and current['env'] == before['env']
    assert current['name'] == 'My board' and current['catalogState'] == 'current'
    assert not (tmp_path / 'data').exists()  # No board_set or other tool execution.
    snapshots = managed.service.published[count:]
    assert len(snapshots) == 1
    snapshot = snapshots[0]['smartTools']
    assert snapshot['servers'][0]['installationId'] == 'new'
    assert snapshot['installations'][0]['supersededBy'] == 'new'
    assert snapshot['installations'][1]['previousInstallationId'] == 'old'
    assert [r['installationId'] for r in managed.update_sources()] == ['new']
    await managed.activate_update('new', 'old')
    assert managed._server('board')['command'] == before['command']
    assert [r['installationId'] for r in managed.update_sources()] == ['old']

async def test_failed_discovery_keeps_old_connection_and_saved_settings(managed, monkeypatch):
    await managed.connect('board')
    old = managed.connections['board']
    before = copy.deepcopy(managed.state)
    async def fail(connection): raise ValueError('invalid tool catalog')
    monkeypatch.setattr(managed, '_discover_catalog', fail)
    with pytest.raises(ValueError, match='invalid tool catalog'):
        await managed.activate_update('old', 'new')
    assert managed.state == before and managed.connections['board'] is old
    assert not old.task.done()
    assert (await managed.call_tool('board', 'view_only', {}, origin='app'))['structuredContent']['view'] == 'compact'

async def test_concurrent_configuration_change_aborts_before_switch(managed, monkeypatch):
    original = managed._discover_catalog
    async def change(connection):
        result = await original(connection)
        managed._server('board')['args'].append('--changed')
        return result
    monkeypatch.setattr(managed, '_discover_catalog', change)
    with pytest.raises(ValueError, match='connection changed'):
        await managed.activate_update('old', 'new')
    assert managed._server('board')['installationId'] == 'old'
    assert managed._server('board')['args'][-1] == '--changed'

async def test_missing_binary_does_not_replace_registration(managed, tmp_path):
    (tmp_path / 'new/bin/python').unlink()
    with pytest.raises(ValueError, match='no longer provides'):
        await managed.activate_update('old', 'new')
    assert managed._server('board')['installationId'] == 'old'

async def test_update_keeps_disconnected_tools_disconnected(managed):
    await managed.activate_update('old', 'new')
    assert managed._server('board')['installationId'] == 'new'
    assert managed._server('board')['status'] == 'disconnected'
    assert 'board' not in managed.connections and 'board' not in managed.schemas
    await managed.connect('board')
    assert managed._server('board')['status'] == 'connected'

async def test_inventory_preserves_pins_and_external_servers(managed):
    managed.state['installations'][0]['ref'] = 'a' * 40
    managed.state['servers'].append({'id': 'remote', 'name': 'External', 'transport': 'streamable-http', 'url': 'https://example.invalid/mcp'})
    items = managed.update_sources()
    assert len(items) == 2
    assert not any(row['eligible'] for row in items)
    assert items[0]['status'] == 'pinned'
    assert items[1]['usageEvidence'] == ['External MCP connection']

async def test_stage_preserves_tracking_policy_and_extras(managed, monkeypatch):
    managed.state['installations'][0]['extras'] = ['mcp']
    async def install(args):
        assert args['ref'] == 'b' * 40 and args['extras'] == ['mcp']
        assert args['_update_from'] == 'old'
        return managed.state['installations'][1]
    monkeypatch.setattr(managed, 'install', install)
    await managed.stage_update({'installationId': 'old', 'latest': 'b' * 40})
    assert managed.state['installations'][1]['ref'] == 'main'
    assert managed._server('board')['installationId'] == 'old'

def test_retarget_only_owned_paths():
    previous = {'binDir': '/old/bin', 'sourceDir': '/old/src'}
    target = {'id': 'new', 'binDir': str(Path(sys.executable).parent), 'sourceDir': '/new/src'}
    row = {'command': '/old/bin/' + Path(sys.executable).name, 'args': ['/old/src/file', '/old/src-not-mine', '--literal'], 'cwd': '/old/src', 'env': {'KEY':'SAVED_KEY'}}
    updated = retarget(row, previous, target)
    assert updated['args'] == ['/new/src/file', '/old/src-not-mine', '--literal']
    assert updated['cwd'] == '/new/src' and updated['env'] == row['env']
    with pytest.raises(ValueError, match='custom executable'):
        retarget({**row, 'command': 'external'}, previous, target)

async def test_tools_do_not_satisfy_included_bundle_dependencies(managed, tmp_path, monkeypatch):
    from amplifier_web import update_sequence
    monkeypatch.setattr(update_sequence, 'included_sources', lambda: (set(), [{'url': 'https://example.invalid/tool', 'ref': 'main'}]))
    rows = update_sequence.classify(tmp_path, managed.update_sources())
    assert rows[0]['kind'] == 'smart tool' and rows[0]['updateTier'] == 'other'
    assert rows[1]['kind'] == 'included source' and rows[1]['missing']
