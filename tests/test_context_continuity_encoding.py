"""Exact evidence identities with row-bounded temporary JSON allocations."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from amplifier_web.context_continuity import ContextContinuity, _source_digest


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def owner(tmp_path, checkpoint):
    capabilities = {}
    coordinator = SimpleNamespace(hooks=None, get=lambda _: {},
        get_capability=capabilities.get, register_capability=capabilities.__setitem__)
    result = ContextContinuity(coordinator, 'session', tmp_path, checkpoint)
    result.loaded = True
    return result


@pytest.mark.parametrize('value', [
    [], [{}], [None, True, False, 0, -0.0, 1.25, 1e-120, 2**80],
    [{'nested': [{'a': 'λ😀\n\\"', 'b': None}, [1, 2.5]], 'key': '中文'}],
    [(1, 'é', None)], ({'content': 'non-list compatibility'},),
])
def test_source_revision_matches_canonical_array(value):
    assert _source_digest(value) == canonical_digest(value)


def test_source_revision_does_not_encode_the_whole_history(monkeypatch):
    rows = [{'role': 'tool', 'content': 'x' * 4096, 'metadata': {'index': i}}
            for i in range(128)]
    rows[-1]['content'] += '😀'
    expected = canonical_digest(rows)
    original = json.dumps
    encoded_array_lengths = []
    def bounded(value, **kwargs):
        if isinstance(value, list):
            encoded_array_lengths.append(len(value))
            assert len(value) <= 1, 'Do not materialize a history-sized JSON string'
        return original(value, **kwargs)
    monkeypatch.setattr(json, 'dumps', bounded)
    assert _source_digest(rows) == expected
    assert encoded_array_lengths
    rows[0]['metadata']['index'] = -1
    monkeypatch.setattr(json, 'dumps', original)
    assert _source_digest(rows) == canonical_digest(rows) != expected


def tool_at_size(size):
    row = {'role': 'tool', 'tool_call_id': str(size), 'content': ''}
    row['content'] = 'x' * (size - len(json.dumps(row, ensure_ascii=False)))
    assert len(json.dumps(row, ensure_ascii=False)) == size
    return row


async def test_preserve_keeps_threshold_and_fresh_post_checkpoint_references(tmp_path):
    rows = [tool_at_size(16000), tool_at_size(16001),
            {'role': 'tool', 'tool_call_id': 'large', 'content': '😀' * 17000,
             'metadata': {'nested': [1]}},
            {'role': 'tool', 'tool_call_id': 'escaped', 'content': '\n' * 9000},
            {'role': 'assistant', 'content': 'x' * 17000}]
    before = canonical_digest(rows)
    calls = []
    async def checkpoint():
        calls.append(1)
        if len(calls) == 1:
            rows[2]['metadata']['nested'].append(2)
    continuity = owner(tmp_path, checkpoint)
    refs = await continuity.preserve(rows)
    expected = [{'kind': 'transcript', 'sessionId': 'session', 'messageIndex': i,
                 'sha256': canonical_digest(row), 'toolCallId': row.get('tool_call_id'),
                 'sourceRevision': before}
                for i, row in enumerate(rows)
                if row.get('role') == 'tool' and len(json.dumps(row, ensure_ascii=False)) > 16000]
    assert refs == expected and [r['messageIndex'] for r in refs] == [1, 2, 3]
    assert continuity.saved_digest == before and len(calls) == 1
    # The nested mutation across the await must still require another save.
    await continuity.preserve(rows)
    assert len(calls) == 2 and continuity.saved_digest == canonical_digest(rows) != before
    await continuity.preserve(copy.deepcopy(rows))
    assert len(calls) == 2


@pytest.mark.parametrize('failure', [OSError('save failed'), asyncio.CancelledError()])
async def test_failed_checkpoint_never_acknowledges_the_digest(tmp_path, failure):
    rows = [{'role': 'tool', 'content': 'x' * 17000}]
    calls = []
    async def checkpoint():
        calls.append(1)
        if len(calls) == 1:
            raise failure
    continuity = owner(tmp_path, checkpoint)
    with pytest.raises(type(failure)):
        await continuity.preserve(rows)
    assert continuity.saved_digest is None
    assert (await continuity.preserve(rows))[0]['sha256'] == canonical_digest(rows[0])
    assert len(calls) == 2


@pytest.mark.parametrize('invalid', ['nan', 'surrogate', 'cycle', 'unsupported'])
async def test_invalid_source_fails_before_checkpoint(tmp_path, invalid):
    value = {'nan': float('nan'), 'surrogate': 'x\ud800',
             'cycle': [], 'unsupported': object()}[invalid]
    if invalid == 'cycle':
        value.append(value)
    rows = [{'role': 'user', 'content': 'prefix'}, {'role': 'tool', 'content': value}]
    with pytest.raises(Exception) as expected:
        canonical_digest(rows)
    async def checkpoint():
        pytest.fail('Invalid history must not reach persistence')
    continuity = owner(tmp_path, checkpoint)
    with pytest.raises(type(expected.value)) as actual:
        await continuity.preserve(rows)
    assert str(actual.value) == str(expected.value)
    assert continuity.saved_digest is None
