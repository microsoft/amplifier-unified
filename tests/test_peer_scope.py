"""Frozen request bindings fail closed without upgrading historical receipts."""
import copy

import pytest

from amplifier_web.peer_scope import participant_matches, valid_binding


def request():
    return {
        'endpointBindingVersion': 1, 'senderSessionId': 'sender', 'workspace': '/projects/one',
        'target': {'sessionId': 'recipient', 'workspace': '/projects/two'},
        'senderLocation': {'kind': 'workspace', 'path': '/projects/one'},
        'recipientLocation': {'kind': 'workspace', 'path': '/projects/two'},
    }


def test_each_endpoint_matches_only_its_frozen_location():
    receipt = request()
    assert valid_binding(receipt)
    for identity, path in [('sender', '/projects/one'), ('recipient', '/projects/two')]:
        assert participant_matches(receipt, {'id': identity, 'workspace': path})
        assert not participant_matches(receipt, {'id': identity, 'workspace': path + '/moved'})
    assert not participant_matches(receipt, {'id': 'unrelated', 'workspace': '/projects/one'})
    assert not participant_matches(receipt, {'id': 'sender', 'workspace': '/projects/one',
                                            'location': {'kind': 'managed'}})


@pytest.mark.parametrize('field', [
    'endpointBindingVersion', 'senderLocation', 'recipientLocation', 'workspace', 'senderSessionId',
])
def test_incomplete_new_receipts_never_fall_back_to_legacy(field):
    receipt = request()
    del receipt[field]
    assert not valid_binding(receipt)
    assert not participant_matches(receipt, {'id': 'recipient', 'workspace': '/projects/two'})


@pytest.mark.parametrize('field,value', [
    ('endpointBindingVersion', True), ('endpointBindingVersion', 2),
    ('target', None), ('target', []), ('senderLocation', 'managed'),
    ('recipientLocation', {'kind': 'workspace', 'path': '/projects/one'}),
    ('workspace', None), ('workspace', ''), ('senderSessionId', ''),
])
def test_malformed_binding_is_not_authority(field, value):
    receipt = request()
    receipt[field] = value
    assert not valid_binding(receipt)


def test_legacy_scope_and_self_notification_remain_narrow():
    old = {'senderSessionId': 'sender', 'target': {'sessionId': 'recipient'},
           'workspace': '/projects/one'}
    assert valid_binding(old)
    assert participant_matches(old, {'id': 'recipient', 'workspace': '/projects/one'})
    assert not participant_matches(old, {'id': 'recipient', 'workspace': '/projects/two'})
    partial = copy.deepcopy(old)
    partial['target']['workspace'] = '/projects/two'
    assert not valid_binding(partial)
    new = request()
    new['target']['sessionId'] = new['senderSessionId']
    assert not valid_binding(new)
    new['target']['workspace'] = new['workspace']
    new['recipientLocation'] = copy.deepcopy(new['senderLocation'])
    assert valid_binding(new)