import asyncio
import json
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.service import AppError, AppService
from amplifier_web.surface_context import compact, revision
from amplifier_web.surface_delivery import SurfaceDelivery, SurfaceProvider

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='


@pytest.fixture
async def surface(tmp_path):
    app = AppService(tmp_path/'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    app.clients.attach('one')
    with app.clients.bind('one'):
        row = (await app.dispatch('canvas.apps.create', {'title': 'Sketch', 'content': '<canvas></canvas>',
            'manifest': {'version': 1, 'stateSchema': {'type': 'object'}},
            'initialState': {'strokes': [{'points': [[n, n] for n in range(100)]} for _ in range(7)], 'theme': 'copper'}}))['result']
        binding = app.surface_context.bind_input(row['sessionId'])
        view = app.canvas_views.summary('primary')
        target = {k: view[k] for k in ('viewId', 'resourceId', 'resourceRevision', 'generation')}
        await app.dispatch('canvas.views.observe', {**target, 'revision': revision(row), 'editVersion': 1,
                           'pending': False, 'status': 'ready', 'image': PNG})
    yield app, row, binding, target
    await app.close()


def bridge_for(app, row, binding):
    async def bridge(op, args):
        return await app.app_bridge(op, {**args, '_contextInputs': ['input-one'], '_contextBindings': [binding]}, row['sessionId'])
    return bridge


def request(*messages):
    return ChatRequest(messages=[Message(role='user', content='What is in the sketch?'), *messages],
                       tools=[ToolSpec(name='app_control', parameters={'type': 'object'})])


def notice(request):
    return json.loads(request.messages[-1].content[0].text.split('\n', 1)[1])


def provider(vision=True):
    return SimpleNamespace(get_info=lambda: SimpleNamespace(capabilities=['vision'] if vision else []))


async def test_patch_changes_are_bounded_and_input_binding_does_not_follow_other_device(surface):
    app, row, binding, target = surface
    bridge = bridge_for(app, row, binding)
    initial = await bridge('context.manifest', {})
    assert initial['surfaces'][0]['facts']['strokes']['count'] == 7
    assert 'points' not in compact(initial)
    assert len(compact(initial)) < 1600
    app.clients.attach('two')
    with app.clients.bind('two'):
        await app.dispatch('session.create', {})
    with app.clients.bind('one'):
        await app.dispatch('canvas.apps.state', {'id': row['id'], 'expectedRevision': 1, 'expectedStateRevision': 0,
                           'patch': {'strokes': []}})
    current = (await bridge('context.manifest', {}))['surfaces'][0]
    assert current['clientId'] == 'one' and current['revision'] == '1:1'
    assert current['facts']['strokes']['count'] == 0 and not current['image']['available']
    with pytest.raises(AppError, match='revision is unavailable'):
        await bridge('context.read', {'surfaceId': row['id'], 'revision': '1:0'})
    other = app.clients.records['two']['selectedSessionId']
    with pytest.raises(AppError, match='unavailable in the calling conversation'):
        await app.app_bridge('context.read', {'surfaceId': row['id']}, other)


async def test_notice_never_acknowledges_content_and_compaction_resyncs(surface):
    app, row, binding, _ = surface
    delivery = SurfaceDelivery(bridge_for(app, row, binding))
    first = await delivery.prepare(request(), provider(), commit=True)
    assert notice(first)['surfaces'][0]['unseenContent']
    second = await delivery.prepare(request(), provider(), commit=True)
    assert notice(second)['surfaces'][0]['unseenContent']
    read = await delivery.read({'surfaceId': row['id'], 'representation': 'state', 'fields': ['theme']})
    retained = request(Message(role='tool', name='app_control', tool_call_id='read', content=json.dumps(read)))
    current = await delivery.prepare(retained, provider(), commit=True)
    assert notice(current)['surfaces'][0]['observedFields'] == ['theme']
    assert 'facts' not in notice(current)['surfaces'][0]
    compacted = await delivery.prepare(request(), provider(), commit=True)
    assert notice(compacted)['surfaces'][0]['unseenContent']
    # A worker's independent ledger cannot acknowledge its parent's read.
    worker = SurfaceDelivery(bridge_for(app, row, binding))
    assert notice(await worker.prepare(retained, provider()))['surfaces'][0]['unseenContent']


async def test_selective_images_are_typed_and_never_leak_into_tool_outputs(surface):
    app, row, binding, _ = surface
    delivery = SurfaceDelivery(bridge_for(app, row, binding))
    await delivery.prepare(request(), provider(), commit=True)
    read = await delivery.read({'surfaceId': row['id'], 'representation': 'image'})
    assert PNG not in json.dumps(read)
    retained = request(Message(role='tool', name='app_control', tool_call_id='read', content=json.dumps(read)))
    current = await delivery.prepare(retained, provider(), commit=True)
    assert current.messages[-1].content[-1].type == 'image'
    assert current.messages[-1].content[-1].source['data'] == PNG
    assert notice(current)['surfaces'][0]['imageInThisRequest']
    no_vision = await delivery.prepare(retained, provider(False))
    assert all(block.type != 'image' for block in no_vision.messages[-1].content)
    compacted = await delivery.prepare(request(), provider())
    assert all(block.type != 'image' for block in compacted.messages[-1].content)


async def test_dirty_or_replaced_views_never_claim_saved_pixels(surface):
    app, row, binding, target = surface
    bridge = bridge_for(app, row, binding)
    with app.clients.bind('one'):
        await app.dispatch('canvas.views.dirty', {**target, 'dirty': True, 'editVersion': 2})
    with pytest.raises(AppError, match='unavailable'):
        await bridge('context.read', {'surfaceId': row['id'], 'representation': 'image'})
    with app.clients.bind('one'):
        await app.dispatch('canvas.views.observe', {**target, 'revision': revision(row), 'editVersion': 2,
                           'pending': True, 'status': 'pending', 'image': PNG})
    value = await bridge('context.read', {'surfaceId': row['id'], 'representation': 'image'})
    assert value['pendingLocalEdits'] and value['evidence'] == 'local-pending'
    # A stale capture may not overwrite newer local input.
    with app.clients.bind('one'):
        with pytest.raises(AppError, match='newer local edit'):
            await app.dispatch('canvas.views.observe', {**target, 'revision': revision(row), 'editVersion': 0,
                              'pending': False, 'status': 'ready', 'image': PNG})
        await app.dispatch('canvas.views.recover', target)
    assert (await bridge('context.manifest', {}))['surfaces'][0]['view'] == 'unavailable'
    with pytest.raises(AppError, match='unavailable'):
        await bridge('context.read', {'surfaceId': row['id'], 'representation': 'image'})


async def test_context_failure_is_short_and_explicit():
    async def broken(op, args):
        raise RuntimeError('synthetic unavailable')
    value = await SurfaceDelivery(broken).prepare(request(), provider())
    assert notice(value)['unavailable']


async def test_focus_is_bounded_and_expires_on_new_input(surface):
    app, row, binding, _ = surface
    epoch = ['one']
    original = bridge_for(app, row, binding)
    async def bridge(op, args):
        value = await original(op, args)
        if op == 'context.manifest':
            value['inputIds'] = list(epoch)
        return value
    delivery = SurfaceDelivery(bridge)
    await delivery.prepare(request(), provider(), commit=True)
    await delivery.interest({'surfaceId': row['id'], 'requests': 1})
    focused = await delivery.prepare(request(), provider(), commit=True)
    assert focused.messages[-1].content[-1].type == 'image'
    assert (await delivery.prepare(request(), provider(), commit=True)).messages[-1].content[-1].type == 'text'
    await delivery.interest({'surfaceId': row['id'], 'requests': 3})
    epoch[:] = ['two']
    assert (await delivery.prepare(request(), provider(), commit=True)).messages[-1].content[-1].type == 'text'


async def test_provider_budget_and_complete_receive_same_typed_contract(surface):
    app, row, binding, _ = surface
    class Provider:
        get_info = staticmethod(lambda: SimpleNamespace(capabilities=['vision']))
        async def request_budget(self, value, **kwargs):
            self.budget = value
            return {'fits': True}
        async def complete(self, value, **kwargs):
            self.actual = value
            return 'done'
    original = Provider()
    proxy = SurfaceProvider(original, SurfaceDelivery(bridge_for(app, row, binding)))
    assert not hasattr(proxy, 'stream')
    req = request()
    await proxy.request_budget(req)
    await proxy.complete(req)
    assert original.actual.model_dump() == original.budget.model_dump()
    assert len(req.messages) == 1
