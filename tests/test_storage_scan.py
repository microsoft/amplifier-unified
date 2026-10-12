"""Slow-filesystem and reuse outcomes; scans never supply deletion evidence."""
import asyncio
import threading
import time
from pathlib import Path

import pytest

from amplifier_web import storage_catalog, storage_scan


def layout(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'cli'))
    return home, workspace, [{'id': 'one', 'path': str(workspace), 'name': 'One'}]


def test_overview_category_and_subtree_share_measurements(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    nested = workspace / 'nested'
    nested.mkdir()
    file = nested / 'content'
    file.write_bytes(b'x' * 123)
    observation = {}
    overview = storage_catalog.inspect(home, rows, {}, observation=observation)
    assert overview['bytes'] == 123 and not overview['incomplete']
    original = Path.lstat
    visits = []
    def stat(path, *args, **kwargs):
        if path == file:
            visits.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', stat)
    category = storage_catalog.inspect(home, rows, {'category': 'workspaces'}, observation=observation)
    assert category['bytes'] == 123 and not visits
    folder = {'category': 'workspaces', 'rootId': category['rows'][0]['id']}
    first = storage_catalog.inspect(home, rows, folder, observation=observation)
    second = storage_catalog.inspect(home, rows, {**folder, 'path': 'nested'}, observation=observation)
    assert first['bytes'] == second['bytes'] == 123 and not visits
    # A new observation is an explicit refresh and sees current contents.
    file.write_bytes(b'x' * 321)
    fresh = storage_catalog.inspect(home, rows, {}, observation={})
    assert fresh['bytes'] == 321 and visits


def test_slow_file_metadata_is_bounded_and_partial(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    for index in range(300):
        (workspace / str(index)).write_bytes(b'x')
    original = Path.lstat
    visits = []
    def slow(path, *args, **kwargs):
        if path.parent == workspace:
            visits.append(path)
            time.sleep(.004)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', slow)
    started = time.monotonic()
    result = storage_catalog.inspect(home, rows, {}, seconds=.2)
    assert time.monotonic() - started < .8
    assert 0 < result['bytes'] < 300 and result['incomplete']
    assert len(visits) < 100


async def wait_scan(scans, home, rows, args, scan_id):
    for _ in range(200):
        result = await scans.read(home, rows, {**args, 'scanId': scan_id}, [], None)
        if not result['scanning']:
            return result
        await asyncio.sleep(.01)
    pytest.fail('Bounded storage scan did not finish')


async def test_slow_scan_returns_categories_immediately_and_cancels(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    entered = threading.Event()
    def slow(*args, stopped=None, **kwargs):
        entered.set()
        stopped.wait(2)
        return {'rows': [], 'bytes': 0, 'incomplete': True}
    monkeypatch.setattr(storage_scan, 'inspect', slow)
    scans = storage_scan.StorageScans()
    try:
        result = await asyncio.wait_for(scans.read(home, rows, {'progressive': True}, [], None), .1)
        assert len(result['rows']) == 6 and result['scanning']
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.01)
        assert entered.is_set()
        cancelled = await scans.read(home, rows, {'scanId': result['scanId'], 'cancel': True}, [], None)
        assert cancelled['cancelled']
    finally:
        await scans.close()


async def test_followup_reuses_observation_and_stale_ids_do_not_rescan(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    (workspace / 'data').write_bytes(b'1234')
    scans = storage_scan.StorageScans()
    try:
        first = await scans.read(home, rows, {}, [], None)
        await wait_scan(scans, home, rows, {}, first['scanId'])
        # Reading the overview for a minute must not discard its paid-for work.
        scans.entries[first['scanId']].touched -= 60
        original = storage_catalog.measure
        def reused(*args, **kwargs):
            pytest.fail('A category must reuse the completed overview roots')
        monkeypatch.setattr(storage_catalog, 'measure', reused)
        second = await scans.read(home, rows, {'category': 'workspaces', 'fromScanId': first['scanId']}, [], None)
        result = await wait_scan(scans, home, rows, {'category': 'workspaces'}, second['scanId'])
        assert result['bytes'] == 4 and result['rows'][0]['label'] == 'One'
        with pytest.raises(ValueError, match='expired'):
            await scans.read(home, rows, {'scanId': 'unknown'}, [], None)
        monkeypatch.setattr(storage_catalog, 'measure', original)
    finally:
        await scans.close()


def test_folder_names_arrive_before_slow_child_measurements(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    for index in range(4):
        child = workspace / str(index)
        child.mkdir()
        (child / 'content').write_bytes(b'123')
    observation = {}
    roots = storage_catalog.catalog(home, rows)
    root_id = roots[workspace]['id']
    updates = []
    original = storage_catalog.measure
    def slow(*args, **kwargs):
        time.sleep(.04)
        return original(*args, **kwargs)
    monkeypatch.setattr(storage_catalog, 'measure', slow)
    result = storage_catalog.inspect(home, rows, {'category': 'workspaces', 'rootId': root_id},
                                     observation=observation, progress=updates.append)
    assert len(updates[0]['rows']) == 4
    assert all(row.get('pending') for row in updates[0]['rows'])
    assert result['bytes'] == 12 and not result['incomplete']
    assert not any(row.get('pending') for row in result['rows'])
