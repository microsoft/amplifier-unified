import asyncio
import json
import shutil

import pytest

from amplifier_web import updates
from test_updates import app, repo, prepare, git


async def copies(app, repo):
    manager, root = await prepare(app, repo)
    stage = app.data_dir / 'candidate'
    shutil.copytree(root.parent.parent, stage / 'foundation')
    rows = []
    for number in range(6):
        path = 'cache/repository-' + str(number)
        shutil.copytree(root, stage / 'foundation' / path)
        rows.append({**manager.inventory[0], 'id':str(number), 'path':path})
    manager.diagnostics.begin('ecosystem')
    return manager, root, stage, rows


async def test_independent_checkouts_overlap_with_a_bounded_limit(app, repo, monkeypatch):
    manager, root, stage, rows = await copies(app, repo)
    original = updates.process
    entered, release = asyncio.Event(), asyncio.Event()
    active = maximum = 0
    async def process(*args, **kwargs):
        nonlocal active, maximum
        if 'fetch' not in args:
            return await original(*args, **kwargs)
        active += 1
        maximum = max(maximum, active)
        if active == 4:
            entered.set()
        try:
            await release.wait()
            return await original(*args, **kwargs)
        finally:
            active -= 1
    monkeypatch.setattr(updates, 'process', process)
    task = asyncio.create_task(manager.stage_cache_sources(stage, rows))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert active == 4
        release.set()
        await task
    finally:
        release.set()
        await task
    assert maximum == 4
    assert git(root, 'rev-parse', 'HEAD') == repo[1]
    for row in rows:
        target = stage / 'foundation' / row['path']
        assert git(target, 'rev-parse', 'HEAD') == repo[2]
        assert json.loads((target / '.amplifier_cache_meta.json').read_text())['commit'] == repo[2]


async def test_failed_download_cancels_siblings_and_keeps_serving_sources(app, repo, monkeypatch):
    manager, root, stage, rows = await copies(app, repo)
    original = updates.process
    both = asyncio.Event()
    active = 0
    async def process(*args, **kwargs):
        nonlocal active
        if 'fetch' not in args:
            return await original(*args, **kwargs)
        active += 1
        if active == 2:
            both.set()
        try:
            await both.wait()
            if str(kwargs['cwd']).endswith('repository-0'):
                raise RuntimeError('fixture download failure')
            await asyncio.Event().wait()
        finally:
            active -= 1
    monkeypatch.setattr(updates, 'process', process)
    with pytest.raises(RuntimeError, match='fixture download failure'):
        await asyncio.wait_for(manager.stage_cache_sources(stage, rows[:2]), 5)
    assert active == 0
    assert git(root, 'rev-parse', 'HEAD') == repo[1]
    assert not (stage / 'validated.json').exists()
