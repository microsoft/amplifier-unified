import copy
import hashlib
import json

import pytest

from amplifier_web.attention import snapshot
from amplifier_web.service import AppError
from amplifier_web.smart_tools import configuration_key
from test_interactive_publication import arguments, interactive  # noqa: F401


async def test_disconnected_read_polling_is_rejected_before_admission(interactive):
    service = interactive
    server = service.state['smartTools']['servers'][0]
    # Mixed read/write tool declarations cannot identify harmless polling.
    server['tools'][0]['annotations'] = {'readOnlyHint': False}
    # A retained canvas can still have its old catalog after transport loss.
    server.update(status='disconnected', connectionState='disconnected')
    before = copy.deepcopy(service.state['smartTools']['operations'])
    for index in range(20):
        with pytest.raises(AppError, match='Reconnect'):
            await service.dispatch('smartTools.appCall', arguments(service),
                                   command_id=f'poll-{index}', include_state=False)
    assert service.state['smartTools']['operations'] == before
    assert service.db.execute('SELECT COUNT(*) FROM smart_tool_operations').fetchone()[0] == len(before)
    assert not service.smart_tool_requests


async def test_disconnect_keeps_previously_admitted_request_receipt(interactive):
    service = interactive
    args = arguments(service)
    accepted = await service.dispatch('smartTools.appCall', args, command_id='accepted', include_state=False)
    await service.wait_smart_tool(accepted['operationId'])
    original = copy.deepcopy(service.smart_tools.operation('accepted'))
    service.state['smartTools']['servers'][0]['status'] = 'disconnected'
    duplicate = await service.dispatch('smartTools.appCall', args, command_id='accepted', include_state=False)
    assert duplicate['duplicate']
    assert service.smart_tools.operation('accepted') == original


async def test_connected_denial_retains_failed_receipt_without_transport(interactive, monkeypatch):
    service = interactive
    calls = []

    async def unexpected_transport(*args, **kwargs):
        calls.append(args)
        raise AssertionError('Ungranted tool must never reach transport')

    monkeypatch.setattr(service.smart_tools, 'execute', unexpected_transport)
    args = {**arguments(service), 'name': 'ungranted'}
    accepted = await service.dispatch('smartTools.appCall', args,
                                      command_id='denied', include_state=False)
    receipt = await service.wait_smart_tool(accepted['operationId'])
    assert receipt['status'] == 'failed'
    assert 'not granted' in receipt['error']
    assert service.db.execute('SELECT COUNT(*) FROM smart_tool_operations').fetchone()[0] == 1
    service.state['smartTools']['servers'][0]['status'] = 'disconnected'
    duplicate = await service.dispatch('smartTools.appCall', args,
                                       command_id='denied', include_state=False)
    assert duplicate['duplicate']
    assert await service.wait_smart_tool('denied') == receipt
    assert not calls


async def test_connection_loss_after_admission_retains_not_sent_receipt(interactive, monkeypatch):
    from amplifier_web.smart_tool_lifecycle import ConnectionUnavailable, CONNECTION_UNAVAILABLE
    service = interactive

    async def disconnected(*args, **kwargs):
        raise ConnectionUnavailable(CONNECTION_UNAVAILABLE)

    monkeypatch.setattr(service.smart_tools, 'execute', disconnected)
    accepted = await service.dispatch('smartTools.appCall', arguments(service),
                                      command_id='lost-before-dispatch', include_state=False)
    await service.wait_smart_tool(accepted['operationId'])
    operation = service.smart_tools.operation(accepted['operationId'])
    assert operation['status'] == 'failed'
    assert operation['failureReason'] == 'connection_unavailable'
    assert operation['requestState'] == 'not_sent'
    assert service.db.execute('SELECT COUNT(*) FROM smart_tool_operations').fetchone()[0] == 1


def test_equivalent_read_connection_alerts_group_without_hiding_other_failures():
    server = {'id': 'one', 'command': 'fixture', 'tools': [
        {'name': 'read', 'annotations': {'readOnlyHint': True}},
        {'name': 'write', 'annotations': {'readOnlyHint': False}}]}
    base = {'action': 'smartTools.call', 'origin': 'app', 'status': 'failed',
            'target': {'id': 'one', 'name': 'read'}, 'configuration': configuration_key(server),
            'error': 'Connect this tool before using it. Previous requests are never replayed.'}
    rows = [{**base, 'id': str(index), 'updatedAt': index} for index in range(50)]
    rows += [{**base, 'id': 'write', 'target': {'id': 'one', 'name': 'write'},
              'result': {'isError': True}, 'error': 'Write failed after dispatch'},
             {**base, 'id': 'unknown', 'error': 'The request timed out.'},
             {**base, 'id': 'interrupted', 'status': 'interrupted'}]
    state = {'smartTools': {'servers': [server], 'operations': rows}}
    original = copy.deepcopy(state)
    result = snapshot(state)
    assert result['unread'] == 4
    group = next(item for item in result['items'] if item.get('operationIds'))
    assert len(group['operationIds']) == 50
    assert '50' in group['detail']
    assert state == original
    legacy_read = {'smart-tool:'+op['id']: hashlib.sha256(json.dumps(
        ['smart-tool:'+op['id'], 'Smart Tool needs attention', op['error'], op.get('updatedAt')],
        sort_keys=True).encode()).hexdigest()[:24] for op in rows[:50]}
    state['attentionRead'] = legacy_read
    assert snapshot(state)['unread'] == 3
    state['attentionRead'] = {group['id']: group['fingerprint']}
    assert snapshot(state)['unread'] == 3
    state['smartTools']['operations'].append({**base, 'id': 'new', 'updatedAt': 100})
    assert snapshot(state)['unread'] == 4


def test_missing_connection_identity_does_not_group_historical_operations():
    base = {'id': 'one', 'action': 'smartTools.call', 'origin': 'app', 'status': 'failed',
            'target': {'name': 'read'}, 'configuration': 'old',
            'error': 'Connect this tool before using it. Previous requests are never replayed.'}
    state = {'smartTools': {'servers': [], 'operations': [base, {**base, 'id': 'two'}]}}
    assert snapshot(state)['unread'] == 2
