"""Stable, bounded receipts for parallel agent reads and ordinary UI callers."""
import asyncio
import copy
import json

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.smart_canvas import SmartCanvas


@pytest.fixture
async def app(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    app.smart_tools = SmartToolsManager(app)
    app.smart_canvas = SmartCanvas(app)
    await app.dispatch('session.create', {'title': 'Receipt reader'})
    yield app
    await app.close()


async def read(app, operation_id, **args):
    return await app.app_bridge('dispatch', {
        'action': 'smartTools.readResult',
        'args': {'operationId': operation_id, **args},
    }, app._session()['id'])


def retain(app, identity, result, status='completed'):
    app.smart_tools.persist_operation({'id': identity, 'status': status, 'result': result})
    app.db.commit()


async def test_parallel_reads_do_not_move_cursor_create_work_or_change_state(app):
    retain(app, 'first', {'value': 'one'})
    retain(app, 'second', {'value': 'two'})
    app.state['smartTools']['inspectedOperation'] = {'id': 'legacy-view'}
    before = copy.deepcopy(app.state)
    count = app.db.execute('SELECT count(*) FROM commands').fetchone()[0]
    first, second = await asyncio.gather(read(app, 'first'), read(app, 'second'))
    assert first['result']['operation']['result'] == {'value': 'one'}
    assert second['result']['operation']['result'] == {'value': 'two'}
    assert app.state == before
    assert app.db.execute('SELECT count(*) FROM commands').fetchone()[0] == count
    assert app.db.execute('SELECT count(*) FROM smart_tool_operations').fetchone()[0] == 2
    assert not app.smart_tool_requests
    ui = await app.dispatch('smartTools.readResult', {'operationId': 'first'})
    assert ui['result'] == first['result']


async def test_receipt_survives_recent_list_rollover_and_restart(app):
    async def execute(action, args, origin='ui'):
        return {'value': args['value']}
    app.smart_tools.execute = execute
    for index in range(65):
        await app.smart_tools.command('smartTools.discover', {'value': index}, f'op-{index}')
    assert len(app.state['smartTools']['operations']) == 50
    assert app.state['smartTools']['operations'][0]['id'] != 'op-0'
    assert (await read(app, 'op-0'))['result']['operation']['result']['value'] == 0
    await app.smart_tools.close()
    app.smart_tools = SmartToolsManager(app)
    assert (await read(app, 'op-0'))['result']['operation']['result']['value'] == 0


async def test_large_schema_and_text_are_lossless_with_stable_paths(app):
    text = 'A long description, including Unicode café. ' * 5000
    payload = {'schema': {'properties': {'document/~name': {'description': text}}},
               'literal': {'$statePath': '/untouched', '$operationPath': '/also-untouched'}}
    retain(app, 'large', payload)
    initial = await read(app, 'large')
    assert len(json.dumps(initial)) < 16000
    assert '$operationPath' in json.dumps(initial['result']['operation'])
    literal = (await read(app, 'large', path='/result/literal'))['result']['operation']['items']
    assert {row['key']: row['value'] for row in literal} == payload['literal']
    revision = initial['result']['operationRevision']
    recovered = ''
    offset = 0
    while offset is not None:
        page = (await read(app, 'large', path='/result/schema/properties/document~1~0name/description',
                           offset=offset, limit=16000, operationRevision=revision))['result']['operation']
        recovered += page['value']
        offset = page['nextOffset']
    assert recovered == text
    retain(app, 'large', {'new': 'result'})
    with pytest.raises(AppError, match='operation changed'):
        await read(app, 'large', path='/result', operationRevision=revision)


async def test_compact_dispatch_does_not_repeat_conversation_or_app_state(app):
    app._session()['messages'].append({'role': 'user', 'text': 'UNRELATED_TRANSCRIPT' * 10000})
    app.state['chatNavigation'] = {'large': 'UNRELATED_NAVIGATION' * 10000}
    async def execute(action, args, origin='ui'):
        return {'tools': [{'name': 'one'}]}
    app.smart_tools.execute = execute
    receipt = await app.app_bridge('dispatch', {'action': 'smartTools.discover', 'args': {'id': 'fixture'}}, app._session()['id'])
    assert len(json.dumps(receipt)) < 1500
    assert 'UNRELATED_' not in json.dumps(receipt)
    assert receipt['read']['args']['operationId'] == receipt['operationId']
    await app.wait_smart_tool(receipt['operationId'])
    assert (await read(app, receipt['operationId']))['result']['operation']['result']['tools'] == [{'name': 'one'}]
    # Broad state access remains explicit and available.
    state = await app.app_bridge('get_state', {'path': '/settings/workspace'}, app._session()['id'])
    assert state['value'] == app.state['settings']['workspace']


async def test_pending_failed_and_missing_results_are_explicit(app):
    app.smart_tool_requests['admitted'] = None
    pending = await read(app, 'admitted')
    assert pending['result']['status'] == 'pending'
    app.smart_tool_requests.pop('admitted')
    retain(app, 'failed', {'isError': True, 'content': [{'type': 'text', 'text': 'Tool failed'}]}, status='failed')
    assert (await read(app, 'failed'))['result']['status'] == 'failed'
    with pytest.raises(AppError, match='no longer retained'):
        await read(app, 'missing')
    with pytest.raises(AppError):
        await read(app, 'failed', path='/result', offset=-1)
    with pytest.raises(AppError, match='does not exist'):
        await read(app, 'failed', path='/missing')


async def test_oversized_keys_fall_back_to_lossless_bounded_json_pages(app):
    payload = {'k' * 25000: 'v' * 20000}
    retain(app, 'long-key', payload)
    receipt = (await read(app, 'long-key'))['result']
    assert receipt['read']['args']['format'] == 'json'
    text = ''
    while True:
        assert len(json.dumps(receipt)) < 26000
        page = receipt['operation']
        text += page['value']
        if page['nextOffset'] is None:
            break
        receipt = (await read(app, 'long-key', format='json', offset=page['nextOffset'],
                              operationRevision=receipt['operationRevision']))['result']
    assert json.loads(text)['result'] == payload
