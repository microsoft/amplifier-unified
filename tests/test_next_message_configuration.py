"""The public configuration path must work with no conversation runtime."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from amplifier_web.management import Management
from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.management = Management(service)
    await service.dispatch('session.create', {'bundle': 'anchors'})
    yield service
    await service.close()


def choices(app, revision=0, **patch):
    return {'sessionId': app._session()['id'], 'expectedRevision': revision, **patch}


async def test_saved_choices_do_not_hydrate_prepare_or_mount(app, monkeypatch):
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('canonical hydration')))
    monkeypatch.setattr(app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('runtime refresh')))
    monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock(side_effect=AssertionError('worker start')))
    before = deepcopy(app._session()['messages'])
    selection = {'instance': 'one', 'model': 'chosen', 'effort': 'high'}
    result = await app.dispatch('conversation.configure', choices(app, bundle='work', selection=selection), command_id='save-choice')
    session = app._session()
    assert result['result']['revision'] == 1
    assert session['bundle'] == 'anchors'  # Saved intent is not the mounted root.
    assert session['pendingBundleSelection']['bundle'] == 'work'
    assert session['pendingModelSelection'] == selection
    assert session['messages'] == before and app.runtime is None
    duplicate = await app.dispatch('conversation.configure', choices(app, bundle='work', selection=selection), command_id='save-choice')
    assert duplicate['duplicate'] is True
    assert session['nextMessageRevision'] == 1
    with pytest.raises(AppError, match='changed'):
        await app.dispatch('conversation.configure', choices(app, bundle='anchors'), command_id='stale-browser')
    assert session['pendingBundleSelection']['bundle'] == 'work'
    home, workspace, sid = app.data_dir, session['workspace'], session['id']
    await app.close()
    restored = AppService(home, workspace=workspace)
    try:
        saved = restored._session(sid)
        assert saved['nextMessageRevision'] == 1
        assert saved['pendingBundleSelection']['bundle'] == 'work'
        assert saved['pendingModelSelection'] == selection
    finally:
        await restored.close()


async def test_preparation_does_not_disable_saved_choices_but_execution_does(app):
    session = app._session()
    session.update(status='starting', preparation={'status': 'preparing'})
    await app.dispatch('conversation.configure', choices(app, bundle='work'))
    session['status'] = 'working'
    with pytest.raises(AppError, match='active work'):
        await app.dispatch('conversation.configure', choices(app, 1, bundle='anchors'))
    session['status'] = 'idle'
    session['pendingConfiguration'] = {'phase': 'error', 'effect': 'unknown'}
    with pytest.raises(AppError, match='configuration'):
        await app.dispatch('conversation.configure', choices(app, 1, bundle='anchors'))
    assert session['pendingBundleSelection']['bundle'] == 'work'


async def test_model_catalog_skips_execution_preflight_and_does_not_wait_for_resolver(app, monkeypatch):
    from amplifier_web import draft_defaults
    entered, release = asyncio.Event(), asyncio.Event()
    async def resolve(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'providers': [], 'catalogs': {}}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=AssertionError('canonical hydration')))
    monkeypatch.setattr(app, 'refresh_configuration', AsyncMock(side_effect=AssertionError('runtime refresh')))
    sid = app._session()['id']
    await app.dispatch('runtime.control', {'sessionId': sid, 'operation': 'configuration.catalog'})
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(app.dispatch('session.rename', {'id': sid, 'title': 'Manual name'}), 2)
    await asyncio.wait_for(app.dispatch('conversation.configure', choices(app, bundle='work')), 2)
    assert app._session()['title'] == 'Manual name'
    assert app.runtime is None
    release.set()
    await asyncio.gather(*app.management.conversation_catalog_requests.values())
