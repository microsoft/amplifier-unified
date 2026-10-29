import copy

import pytest

from amplifier_web.message_preparation import MessagePreparation
from amplifier_web.resource_files import prepare


def save(cache, owner):
    pending = {}
    result = cache.prepare(owner, owner['messages'], pending)
    cache.committed(pending, {owner['id']: owner})
    return result


@pytest.mark.parametrize('first,second', [(True, 1), (1, 1.0), (0.0, -0.0), (False, 0), (None, '')])
def test_type_changes_and_signed_zero_are_not_hits(first, second):
    cache = MessagePreparation()
    owner = {'id': 'owner', 'messages': [{'text': 'x' * 16000, 'nested': [first]}]}
    before = save(cache, owner)
    owner['messages'][0]['nested'][0] = second
    after = save(cache, owner)
    assert after == prepare(owner['messages']) and after != before
    assert save(cache, owner) is after


def test_detached_snapshots_and_reordered_fields():
    cache = MessagePreparation()
    owner = {'id': 'owner', 'messages': [{'text': 'x' * 16000, 'nested': {'a': 1, 'b': 2}}]}
    before = save(cache, owner)
    snapshot = cache.entries['owner'].snapshot
    owner['messages'][0]['nested']['a'] = 3
    assert snapshot[0]['nested'] == {'a': 1, 'b': 2}
    assert save(cache, owner) != before
    old = cache.entries['owner']
    owner['messages'][0]['nested'] = {'b': 2, 'a': 3}
    save(cache, owner)
    assert cache.entries['owner'] is not old
    assert list(cache.entries['owner'].snapshot[0]['nested']) == ['b', 'a']


def test_custom_types_use_original_encoder_without_comparison_or_deepcopy_hooks():
    class HostileType(type):
        def __eq__(self, other):
            raise AssertionError('metaclass equality used')
    class Custom(str, metaclass=HostileType):
        def __eq__(self, other):
            raise AssertionError('custom equality used')
        def __deepcopy__(self, memo):
            raise AssertionError('custom deepcopy used')
    cache = MessagePreparation()
    owner = {'id': 'owner', 'messages': [{'text': 'plain'}]}
    save(cache, owner)
    owner['messages'][0]['text'] = Custom('custom')
    assert save(cache, owner) == prepare(owner['messages'])
    assert cache.entries == {}


def test_custom_keys_cycles_and_nonfinite_fall_back():
    class Key(str):
        def __deepcopy__(self, memo):
            raise AssertionError('custom key copied')
    cache = MessagePreparation()
    owner = {'id': 'owner', 'messages': [{Key('text'): 'custom'}]}
    assert save(cache, owner) == prepare(owner['messages']) and not cache.entries
    owner['messages'] = [{'value': float('nan')}]
    assert save(cache, owner).text == prepare(owner['messages']).text and not cache.entries
    owner['messages'] = []
    owner['messages'].append(owner['messages'])
    with pytest.raises(ValueError, match='Circular'):
        save(cache, owner)


def test_commit_replacement_deletion_cold_and_bounds():
    cache = MessagePreparation()
    cache.MAX_ENTRIES = 2
    owners = {}
    for number in range(3):
        owner = {'id': str(number), 'messages': [{'text': 'x' * 16000}]}
        owners[owner['id']] = owner
        pending = {}
        cache.prepare(owner, owner['messages'], pending)
        assert owner['id'] not in cache.entries
        cache.committed(pending, owners)
    # Full provisional+committed budget bypasses the third owner safely.
    assert list(cache.entries) == ['0', '1']
    assert sum(entry.charge for entry in cache.entries.values()) <= cache.MAX_BYTES
    cache.MAX_ENTRIES = 4
    prior = cache.entries['1']
    owners['1'] = copy.deepcopy(owners['1'])
    pending = {}
    cache.prepare(owners['1'], owners['1']['messages'], pending)
    assert pending['1'] is not prior
    cache.committed(pending, owners, {'0'})
    assert list(cache.entries) == ['1']
    owners['1'].pop('messages')
    cache.committed({}, owners)
    assert not cache.entries
    cache.MAX_BYTES = 100
    owner = {'id': 'oversize', 'messages': [{'text': 'x' * 16000}]}
    assert save(cache, owner) == prepare(owner['messages']) and not cache.entries


def test_snapshots_do_not_retain_mutable_aliases_needed_for_retirement():
    from amplifier_web.cold_display import exclusively_owned
    owner = {'id': 'owner', 'messages': [{'text': 'x' * 16000, 'nested': [1, 2]}]}
    assert exclusively_owned(owner, 'messages')
    cache = MessagePreparation()
    save(cache, owner)
    assert cache.entries
    assert exclusively_owned(owner, 'messages')


def test_provisional_and_committed_retention_share_budget():
    cache = MessagePreparation()
    owners = {'old': {'id': 'old', 'messages': [{'text': 'x' * 16000}]}}
    save(cache, owners['old'])
    cache.MAX_BYTES = cache.entries['old'].charge * 3 + 1000
    pending = {}
    for i in range(100):
        owner = {'id': str(i), 'messages': [{'text': 'y' * 16000}]}
        owners[str(i)] = owner
        assert cache.prepare(owner, owner['messages'], pending) == prepare(owner['messages'])
        union = {id(entry): entry for entry in cache.entries.values()}
        union.update((id(entry), entry) for entry in pending.values() if entry is not None)
        assert sum(entry.charge for entry in union.values()) <= cache.MAX_BYTES
        assert len(union) <= cache.MAX_ENTRIES
    assert list(cache.entries) == ['old']  # No commit yet.
    cache.committed(pending, owners)
    assert sum(entry.charge for entry in cache.entries.values()) <= cache.MAX_BYTES
