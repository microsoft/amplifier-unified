"""Slow-filesystem and reuse outcomes; scans never supply deletion evidence."""
import asyncio
import threading
import time
from pathlib import Path

import pytest

from amplifier_web import storage_catalog, storage_scan, storage_walk


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
    original = storage_walk.metadata
    visits = []
    def stat(entry):
        path = Path(entry.path) if hasattr(entry, 'path') else entry
        if path == file:
            visits.append(path)
        return original(entry)
    monkeypatch.setattr(storage_walk, 'metadata', stat)
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
    original = storage_walk.metadata
    visits = []
    def slow(entry):
        path = Path(entry.path) if hasattr(entry, 'path') else entry
        if path.parent == workspace:
            visits.append(path)
            time.sleep(.004)
        return original(entry)
    monkeypatch.setattr(storage_walk, 'metadata', slow)
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


async def test_slow_large_workspace_continues_to_exact_total_without_restarting(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    for index in range(100):
        with (workspace / str(index)).open('wb') as f:
            f.truncate(7_000_000_000)  # Logical content, without allocating 700 GB.
    original = storage_walk.metadata
    visits = []
    def slow(entry):
        path = Path(entry.path) if hasattr(entry, 'path') else entry
        if path.parent == workspace:
            visits.append(path)
            time.sleep(.004)
        return original(entry)
    monkeypatch.setattr(storage_walk, 'metadata', slow)
    scans = storage_scan.StorageScans()
    try:
        first = await scans.read(home, rows, {}, [], None)
        totals = []
        for _ in range(250):
            result = await scans.read(home, rows, {'scanId': first['scanId']}, [], None)
            totals.append(result['bytes'])
            if not result['scanning']:
                break
            await asyncio.sleep(.01)
        assert result['bytes'] == 700_000_000_000 and not result['incomplete']
        assert totals == sorted(totals)
        assert len(visits) == len(set(visits)) == 100
        second = await scans.read(home, rows, {'category': 'workspaces', 'fromScanId': first['scanId']}, [], None)
        category = await wait_scan(scans, home, rows, {'category': 'workspaces'}, second['scanId'])
        assert category['bytes'] == result['bytes'] and len(visits) == 100
    finally:
        await scans.close()


def test_resume_entry_budget_and_exclusions(tmp_path, monkeypatch):
    import os
    home, workspace, rows = layout(tmp_path, monkeypatch)
    child = workspace / 'nested'
    child.mkdir()
    for index in range(30):
        (child / str(index)).write_bytes(b'abc')
    os.link(child / '0', child / 'hardlink')
    (child / 'link').symlink_to(child / '1')
    observation = {}
    totals = []
    for _ in range(100):
        result = storage_catalog.inspect(home, rows, {}, limit=3, observation=observation)
        totals.append(result['bytes'])
        if not result['measuring']:
            break
    assert result['bytes'] == 90 and result['incomplete']  # Link excluded, no perpetual scan.
    assert totals == sorted(totals)
    assert not observation['walks']
    assert len(observation['inodes']) == 1  # Ordinary files need no retained inode set.


async def test_navigation_resumes_partial_cursor_and_back_updates_total(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    for index in range(100):
        (workspace / str(index)).write_bytes(b'x' * 17)
    original = storage_walk.metadata
    visits = []
    def slow(entry):
        path = Path(entry.path) if hasattr(entry, 'path') else entry
        if path.parent == workspace:
            visits.append(path)
            time.sleep(.004)
        return original(entry)
    monkeypatch.setattr(storage_walk, 'metadata', slow)
    scans = storage_scan.StorageScans()
    try:
        first = await scans.read(home, rows, {}, [], None)
        for _ in range(100):
            if len(visits) >= 10:
                break
            await asyncio.sleep(.005)
        await scans.read(home, rows, {'scanId': first['scanId'], 'cancel': True}, [], None)
        second = await scans.read(home, rows, {'category': 'workspaces', 'fromScanId': first['scanId']}, [], None)
        result = await wait_scan(scans, home, rows, {'category': 'workspaces'}, second['scanId'])
        assert result['bytes'] == 1700 and len(visits) == 100
        back = await scans.read(home, rows, {'fromScanId': second['scanId']}, [], None)
        result = await wait_scan(scans, home, rows, {}, back['scanId'])
        assert result['bytes'] == 1700 and len(visits) == 100
    finally:
        await scans.close()


def test_progress_totals_do_not_drop_between_slices(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    (home / 'runtime').mkdir()
    (home / 'runtime' / 'large').write_bytes(b'x' * 1000)
    for index in range(20):
        (workspace / str(index)).write_bytes(b'x')
    observation = {}
    totals = []
    for _ in range(30):
        result = storage_catalog.inspect(home, rows, {}, limit=2, observation=observation,
            progress=lambda value: totals.append(value['bytes']))
        totals.append(result['bytes'])
        if not result['measuring']:
            break
    assert result['bytes'] == 1020
    assert totals == sorted(totals)


def test_folder_cursor_progresses_when_subtree_cache_is_full(tmp_path, monkeypatch):
    home, workspace, rows = layout(tmp_path, monkeypatch)
    for index in range(12):
        child = workspace / str(index)
        child.mkdir()
        for file in range(4):
            (child / str(file)).write_bytes(b'abc')
    observation = {'subtrees': {tmp_path / f'cached-{i}': {'bytes': 0, 'files': 0,
        'incomplete': False, 'directory': False} for i in range(20000)}}
    root_id = storage_catalog.catalog(home, rows)[workspace]['id']
    args = {'category': 'workspaces', 'rootId': root_id}
    for _ in range(20):
        result = storage_catalog.inspect(home, rows, args, limit=1, observation=observation)
        if not result['measuring']:
            break
    assert result['bytes'] == 144 and not result['measuring']
    assert len(observation['subtrees']) <= 20000
