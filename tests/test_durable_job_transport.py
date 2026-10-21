import copy
import hashlib
import json

import pytest

from amplifier_web import test_job_transport as transport


@pytest.fixture
def receipt():
    payload = {'rootOwner': 'root', 'operationId': 'launch', 'universeId': 'guest', 'jobId': 'job',
               'command': 'true', 'timeoutSeconds': 1200}
    binding = {key: value for key, value in payload.items() if key != 'command'}
    binding['commandSha256'] = hashlib.sha256(payload['command'].encode()).hexdigest()
    result = {'job': {'version': 1, 'id': 'job', 'binding': binding, 'state': 'passed', 'terminal': True},
              'result': {'stdout': 'ok', 'stderr': '', 'exit_code': 0}}
    return payload, result


def test_exact_receipt_accepts_only_matching_owner_command_and_result(receipt):
    payload, result = receipt
    observed = transport.result(payload, {'exit_code': 0, 'stdout': json.dumps(result), 'truncated': False})
    assert observed['ok'] and observed['job']['terminal']


@pytest.mark.parametrize('change', ['owner', 'command', 'state', 'terminal', 'exit', 'output', 'truncated'])
def test_observer_refuses_mismatched_or_incomplete_result(receipt, change):
    payload, result = receipt
    result = copy.deepcopy(result)
    if change == 'owner':
        result['job']['binding']['rootOwner'] = 'other'
    elif change == 'command':
        result['job']['binding']['commandSha256'] = 'other'
    elif change == 'state':
        result['job'].update(state='unsupported', terminal=False)
    elif change == 'terminal':
        result['job']['terminal'] = False
    elif change == 'exit':
        result['result']['exit_code'] = 3
    elif change == 'output':
        result['result']['stdout'] = 'x'*8193
    with pytest.raises(ValueError):
        transport.result(payload, {'exit_code': 0, 'stdout': json.dumps(result), 'truncated': change == 'truncated'})
