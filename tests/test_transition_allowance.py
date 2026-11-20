import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppError, AppService
from test_service import Runtime


REQUEST = {'offerId': 'a' * 64, 'additionalCalls': 32}


@pytest.mark.parametrize('origin,changes', [
    ('ui', {'expectedGenerationId': 'generation'}), ('ui', {'mode': 'queue'}),
    ('ui', {'via': 'call'}), ('agent', {}), ('system', {}),
    ('ui', {'expectedInterruptionRevision': 3}),
    ('ui', {'recoveryAllowance': {**REQUEST, 'additionalCalls': 33}}),
    ('ui', {'recoveryAllowance': {**REQUEST, 'additionalCalls': True}}),
])
async def test_only_fresh_human_ordinary_sends_can_carry_an_allowance(tmp_path, origin, changes):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        args = {'sessionId': session['id'], 'text': 'acknowledged', 'expectedInterruptionRevision': 0,
                'recoveryAllowance': REQUEST, **changes}
        with pytest.raises(AppError):
            await app.dispatch('conversation.send', args, origin=origin, command_id='fresh-allowance')
        assert not runtime.sent and not session['messages']
    finally:
        await app.close()


async def test_duplicate_send_reads_the_original_approval_receipt(tmp_path):
    runtime = Runtime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        session = app._session()
        args = {'sessionId': session['id'], 'text': 'acknowledged', 'expectedInterruptionRevision': 0,
                'recoveryAllowance': REQUEST}
        for _ in range(2):
            await app.dispatch('conversation.send', args, command_id='approved-input')
        messages = [m for m in session['messages'] if m.get('inputId') == 'approved-input' and m['role'] == 'user']
        assert len(messages) == 1 and messages[0]['recoveryAllowance'] == REQUEST and len(runtime.sent) == 1
        with pytest.raises(AppError):
            await app.dispatch('conversation.send', {**args, 'recoveryAllowance': {**REQUEST, 'additionalCalls': 2}},
                               command_id='approved-input')
        assert len(runtime.sent) == 1
    finally:
        await app.close()


@pytest.mark.parametrize('operation,active,queued', [('send', True, False), ('send', False, True),
                                                     ('retry', False, False), ('control', False, False)])
def test_worker_cannot_turn_other_input_into_paid_authority(operation, active, queued):
    worker = Worker()
    worker.runtime = SimpleNamespace(accepted={}, generation={'id': 'active'} if active else None, queued_inputs=queued)
    worker.session = SimpleNamespace(coordinator=Mock())
    worker.controls = SimpleNamespace(selection_revision='selection')
    with pytest.raises(ValueError):
        worker.bind_input_context('fresh', {'op': operation, 'interruptionRevision': 2, 'recovery_allowance': REQUEST})
    assert not worker.context_bindings
    worker.bind_input_context('plain', {'op': 'send'}, {'clientId': 'browser', 'recoveryAllowance': copy.copy(REQUEST)})
    assert 'recoveryAllowance' not in worker.context_bindings['plain']
