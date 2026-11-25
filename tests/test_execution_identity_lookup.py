"""Plain execution lookup keeps canonical identity and borrowed-row semantics."""
import pytest

from amplifier_web import execution_records as records
from amplifier_web.execution_working_set import exact


@pytest.mark.parametrize('kind', ['nodes', 'turns'])
def test_plain_lookup_does_not_encode_each_retained_identity(monkeypatch, kind):
    identity = 'quoted-"\\-雪-🦊'
    first = {'id': identity, 'detail': {'values': []}}
    duplicate = {'id': identity, 'detail': {'values': []}}
    rows = [{'id': str(i)} for i in range(200)] + [first, duplicate, {'id': None}]
    session = {'execution': {kind: rows}}
    calls, canonical = [], records.key

    def counted(kind, value):
        calls.append(value)
        return canonical(kind, value)

    monkeypatch.setattr(records, 'key', counted)
    query = {'id': identity}
    result = exact(session, kind, query)
    assert result is first
    result['detail']['values'].append('borrowed edit')
    assert first['detail']['values'] == ['borrowed edit']
    assert duplicate['detail']['values'] == []
    assert calls == [query]


@pytest.mark.parametrize('bad', [None, '', 1, False, []])
def test_plain_lookup_preserves_validation_order(bad):
    target = {'id': 'target'}
    with pytest.raises(ValueError, match='exact identity'):
        exact({'execution': {'nodes': []}}, 'nodes', {'id': bad})
    with pytest.raises(ValueError, match='exact identity'):
        exact({'execution': {'nodes': [{'id': bad}, target]}}, 'nodes', target)
    assert exact({'execution': {'nodes': [target, {'id': bad}]}}, 'nodes', target) is target


def test_plain_lookup_keeps_custom_key_semantics_and_frozen_query():
    class EncodedString(str):
        def __eq__(self, other):
            raise AssertionError('Canonical keys do not call custom string equality')

    query = {'id': 'target'}
    reads = []

    class CustomRow(dict):
        def get(self, name, default=None):
            reads.append(name)
            query['id'] = 'later'
            return super().get(name, default)

    earlier = CustomRow(id='earlier')
    target = {'id': 'target'}
    assert exact({'execution': {'nodes': [earlier, target]}}, 'nodes', query) is target
    assert reads == ['id']

    target = {'id': EncodedString('target')}
    assert exact({'execution': {'nodes': [target]}}, 'nodes', {'id': 'target'}) is target

    class CustomQuery(dict):
        def get(self, name, default=None):
            reads.append(name)
            return super().get(name, default)

    reads.clear()
    assert exact({'execution': {'nodes': [target]}}, 'nodes', CustomQuery(id='target')) is target
    assert reads == ['id']


def test_plain_lookup_freezes_query_before_reading_execution():
    query = {'id': 'original'}
    original, later = {'id': 'original'}, {'id': 'later'}

    class Execution(dict):
        def get(self, name, default=None):
            query['id'] = 'later'
            return [original, later]

    assert exact({'execution': Execution()}, 'nodes', query) is original


@pytest.mark.parametrize('session_id, index', [(None, 0), (True, 1), (1, 2), ('1', 3)])
def test_retired_lookup_keeps_composite_encoded_identity(session_id, index):
    rows = [{'id': 'same', 'sessionId': owner} for owner in [None, True, 1, '1']]
    session = {'execution': {'retiredUsageNodes': rows}}
    query = {'id': 'same', 'sessionId': session_id}
    result = exact(session, 'retiredUsageNodes', query)
    assert result is rows[index]
    assert exact(session, 'retiredUsageNodes', {'id': 'same'}) is rows[0]
