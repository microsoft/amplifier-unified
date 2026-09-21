"""Background send entry points keep exact uncertain delivery without replay."""
import asyncio
import copy
import json

import pytest

from amplifier_web.runtime import RuntimeOperationPending
from amplifier_web.updates import UpdateManager
from test_delivery_ack_timeout import running, release  # Shared real subprocess fixture.


def receipt(service, identity):
    row = service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0])


async def guarded_send(service, kind):
    source = service._session()
    identity = kind + '-followup'
    if kind == 'voice':
        async def submit():
            return await service.voice_delegate('follow-up', identity, source['id'])
    else:
        await service.on_runtime_event('runtime.status', {'sessionId': source['id'], 'status': 'idle'})
        args = {'sessionId': source['id'], 'messageId': source['messages'][0]['id'],
                'mode': 'fork', 'text': 'follow-up'}
        async def submit():
            return await service.dispatch('message.edit', args, command_id=identity)
    tasks = set(service.tasks)
    admitted = await submit()
    assert admitted['accepted'] is True  # App admission is separate from delivery.
    pending = set(service.tasks) - tasks
    assert pending
    await asyncio.wait_for(asyncio.gather(*pending), 2)
    return identity, submit


@pytest.mark.parametrize('kind', ['voice', 'fork'])
async def test_guarded_send_late_ack_reconciles_exact_receipt_without_replay(running, kind):
    service, inputs = running
    source = service._session()
    originals = copy.deepcopy(source['messages'])
    identity, submit = await guarded_send(service, kind)
    session = service._session()
    # A new fork has only reported readiness; the existing voice turn is still
    # working. An acknowledgement timeout must not replace either with failure.
    assert session['status'] == ('ready' if kind == 'fork' else 'working')
    assert not session.get('error') and UpdateManager(service).busy()
    assert all(turn['phase'] != 'error' for turn in session['execution']['turns'])
    assert receipt(service, identity)['delivery'] == 'unknown'
    if kind == 'fork':
        assert session['id'] != source['id'] and source['messages'] == originals
        assert session['messages'][-1]['delivery']['status'] == 'unknown'
    await service.dispatch('view.update', {'patch': {'draft': 'another unsent message'}})
    await service.on_runtime_event('runtime.delivery', {'sessionId': session['id'], 'inputId': 'unrelated'})
    assert receipt(service, identity)['delivery'] == 'unknown'
    duplicate = await submit()
    assert duplicate['duplicate'] and duplicate['delivery'] == 'unknown'
    # A late ack can arrive after the live turn has already settled.
    await service.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    await release(service)
    assert receipt(service, identity)['delivery'] == 'accepted'
    assert session['status'] == 'idle' and not session.get('error')
    duplicate = await submit()
    assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
    assert service._session()['id'] == session['id']
    assert service.state['view']['draft'] == 'another unsent message'
    assert inputs.read_text().splitlines() == ['original-input', identity]
    service._delivery(session, identity, 'unknown')
    assert receipt(service, identity)['delivery'] == 'accepted'


@pytest.mark.parametrize('kind', ['voice', 'fork'])
async def test_guarded_send_worker_exit_still_reports_real_failure(running, kind):
    service, _ = running
    identity, _ = await guarded_send(service, kind)
    session = service._session()
    row = service.runtime.workers[session['id']]
    await service.runtime._write(row, {'op': 'release', 'exit': True})
    async with asyncio.timeout(2):
        while 'exited (code 7)' not in session.get('error', ''):
            await asyncio.sleep(0)
    assert session['status'] == 'error'
    assert receipt(service, identity)['delivery'] == 'unknown'


@pytest.mark.parametrize('operation', ['send', 'control'])
async def test_other_guarded_operation_is_not_suppressed(running, operation):
    service, _ = running
    session = service._session()
    async def other_operation(sid):
        raise RuntimeOperationPending(operation)
    await service._guard(other_operation, (session['id'],))
    assert session['status'] == 'error' and 'may still be running' in session['error']


@pytest.mark.parametrize('binding', ['foreign-session', 'foreign-input', 'unbound'])
async def test_delivery_without_message_requires_exact_saved_input_and_session(running, binding):
    service, _ = running
    session = service._session()
    identity = 'unmatched-receipt'
    saved = {'accepted': True, 'delivery': 'unknown'}
    if binding != 'unbound':
        saved.update(inputId=identity if binding == 'foreign-session' else 'other-input',
                     sessionId='other-session' if binding == 'foreign-session' else session['id'])
    service.db.execute('INSERT INTO commands VALUES (?,?,?)', (identity, 'fixture', json.dumps(saved)))
    await service.on_runtime_event('runtime.delivery', {'sessionId': session['id'], 'inputId': identity})
    assert receipt(service, identity) == saved
