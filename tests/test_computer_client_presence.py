"""Visible control navigation requires a live client, not a saved view record."""
import asyncio
from copy import deepcopy

import pytest
from amplifier_web.service import AppError, AppService


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path/'app', workspace=tmp_path)
    await service.dispatch('session.create', {})
    service.clients.attach('closed')
    service.clients.attach('live')
    with service.clients.bind('closed'):
        queue = service.subscribe(); service.browser_state(); service.unsubscribe(queue)
    with service.clients.bind('live'):
        service.subscribe(); service.browser_state()
    yield service
    for queue in list(service.queues): service.unsubscribe(queue)
    await service.close()


def sid(app): return app._state['selectedSessionId']


async def navigate(app, client=None):
    args = {'patch':{'runtimeDraft':{'section':'screen-source'}}}
    if client is not None: args['clientId'] = client
    return await app.app_bridge('dispatch', {'action':'view.update','args':args}, sid(app))


async def test_one_live_and_one_saved_browser_uses_live_and_reports_eligible_state(app):
    old = deepcopy(app.clients.records['closed'])
    overview = await app.app_bridge('get_state', {}, sid(app))
    state = overview['canvasContext']
    assert state['connectedClientIds'] == ['live']
    assert set(state['clientIds']) == {'live','closed'}  # Saved Canvas views stay intact.
    result = await navigate(app)
    assert result['accepted']
    assert app.clients.records['live']['view']['runtimeDraft']['tab'] == 'computer'
    assert app.clients.records['closed'] == old


@pytest.mark.parametrize('action', ['view.update', 'computer.visual.status', 'computer.visual.capture', 'computer.visual.revoke'])
@pytest.mark.parametrize('bound', [False, True])
async def test_disconnected_explicit_or_inherited_target_never_succeeds(app, action, bound):
    before = deepcopy(app.clients.records)
    args = {'patch':{'runtimeDraft':{'section':'capture'}}} if action == 'view.update' else {'sessionId':sid(app)}
    if not bound: args['clientId'] = 'closed'
    with app.clients.bind('closed' if bound else None), pytest.raises(AppError, match='connected'):
        await app.app_bridge('dispatch', {'action':action,'args':args}, sid(app))
    assert app.clients.records == before
    assert not app.computer_visual.clients and not app._session(sid(app))['messages']


async def test_two_live_clients_are_ambiguous_until_explicit_selection(app):
    with app.clients.bind('closed'): app.subscribe()
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='Multiple connected clients'): await navigate(app)
    assert app.clients.records == before
    await navigate(app,'closed')
    assert app.clients.records['closed']['view']['panel'] == 'runtime'
    assert app.clients.records['live'] == before['live']


async def test_no_live_client_does_not_fall_back_to_saved_selection(app):
    for queue in list(app.queues): app.unsubscribe(queue)
    before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='connected'): await navigate(app)
    assert app.clients.records == before
    state = await app.app_bridge('get_state', {'path':'/canvasContext/connectedClientIds'}, sid(app))
    assert state['items'] == [] and state['total'] == 0


async def test_disconnect_while_waiting_for_dispatch_lock_refuses_view_mutation(app, monkeypatch):
    entered = asyncio.Event(); original = app.dispatch
    async def observed(action,*args,**kwargs):
        if action == 'view.update': entered.set()
        return await original(action,*args,**kwargs)
    monkeypatch.setattr(app,'dispatch',observed)
    async with app.lock:
        task = asyncio.create_task(navigate(app,'live'))
        await entered.wait()
        for queue in list(app.queues): app.unsubscribe(queue)
        before = deepcopy(app.clients.records)
    with pytest.raises(AppError, match='connected'): await task
    assert app.clients.records == before


async def test_reconnect_uses_new_live_id_without_erasing_saved_views(app):
    for queue in list(app.queues): app.unsubscribe(queue)
    old = deepcopy(app.clients.records)
    app.clients.attach('reconnected',resume='live')
    with app.clients.bind('reconnected'): app.subscribe()
    await navigate(app)
    assert app.clients.records['reconnected']['view']['panel'] == 'runtime'
    assert all(app.clients.records[key] == value for key,value in old.items())
    assert not app.computer_visual.clients


async def test_legacy_no_client_api_view_update_remains_supported(tmp_path):
    app = AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        await navigate(app)
        assert app.state['view']['panel'] == 'runtime'
        assert not app.clients.records
    finally: await app.close()
