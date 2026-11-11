"""Run in the parent's isolated test environment; no host-runtime qualification."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from amplifier_web import peer_originals, resource_files
from amplifier_web.peer_originals import PeerOriginals
from amplifier_web.resource_files import Detached
from amplifier_web.saved_message_window import MAX_BYTES, MAX_RECORD


def original(identity='peer', text='Exact body A'):
    return {'id': identity, 'inputId': identity + '-input', 'role': 'user',
            'text': text, 'peerEnvelope': {'senderSessionId': 'source',
                                         'purpose': 'not author or title authority',
                                         'references': ['unchanged']}}


def filler(count=130):
    return [{'id': f'assistant-{index}', 'role': 'assistant', 'text': 'answer'}
            for index in range(count)]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def source_for(tmp_path, rows=None, *, raw=None, inline=False):
    raw = encoded(rows) if raw is None else raw
    identity = hashlib.sha256(raw).hexdigest()
    if inline:
        return Detached(None, identity, raw.decode())
    path = tmp_path / (identity + '.json')
    path.write_bytes(raw)
    return Detached(tmp_path, identity, json.dumps({'$blob': identity}))


def blob_path(source):
    return source.directory / (source.identity + '.json')


@pytest.mark.parametrize('inline', [False, True])
def test_pair_across_100_boundary_has_exact_originals_not_a_larger_page(tmp_path, inline):
    first = original('first', 'Exact α text\n```x```  \n')
    last = original('last')
    rows = [first, *filler(), last]
    proof = PeerOriginals().cold(source_for(tmp_path, rows, inline=inline), [first, last])
    assert proof.originals == {'first': first, 'last': last}
    assert proof.id_counts == {'first': 1, 'last': 1}
    assert proof.input_counts == {'first-input': 1, 'last-input': 1}
    assert proof.native_counts == {'first-input': 0, 'last-input': 0}
    assert proof.revision is not None
    assert len(proof.originals) == 2  # No body copies for the 130 filler rows.
    assert not hasattr(proof, 'title') and not hasattr(proof, 'authority')


@pytest.mark.parametrize('resident', [False, True])
@pytest.mark.parametrize('position', ['prefix', 'suffix'])
@pytest.mark.parametrize('role', ['assistant', 'tool', 'user'])
def test_offpage_id_duplicates_across_all_roles_are_counted(tmp_path, resident, position, role):
    selected = original()
    duplicate = {'id': selected['id'], 'role': role, 'text': 'unrelated'}
    rows = [duplicate, *filler(), selected] if position == 'prefix' else [selected, *filler(), duplicate]
    helper = PeerOriginals()
    proof = (helper.resident(rows, [selected]) if resident
             else helper.cold(source_for(tmp_path, rows), [selected]))
    assert proof.id_counts == {'peer': 2}
    assert proof.input_counts == {'peer-input': 1}
    assert proof.originals == {}  # Do not manufacture a second row as evidence.


@pytest.mark.parametrize('resident', [False, True])
def test_offpage_native_and_nonnative_input_multiplicity_includes_other_roles(tmp_path, resident):
    selected = original()
    rows = [
        {'id': 'prefix-duplicate', 'role': 'assistant', 'inputId': 'peer-input', 'text': 'other'},
        {'id': 'native-1', 'role': 'tool', 'source': 'native', 'nativeInputId': 'peer-input'},
        *filler(), selected,
        {'id': 'native-2', 'role': 'user', 'source': 'native', 'inputId': 'peer-input'},
        {'id': 'native-3', 'role': 'assistant', 'source': 'native',
         'inputId': 'other', 'nativeInputId': 'peer-input'},
        {'id': 'shadowed', 'role': 'user', 'inputId': 'peer-input', 'nativeInputId': 'other'},
        {'id': 'fallback', 'role': 'assistant', 'inputId': 'peer-input', 'nativeInputId': ''},
    ]
    helper = PeerOriginals()
    proof = (helper.resident(rows, [selected]) if resident
             else helper.cold(source_for(tmp_path, rows), [selected]))
    assert proof.originals == {'peer': selected}
    assert proof.id_counts == {'peer': 1}
    assert proof.input_counts == {'peer-input': 3}
    assert proof.native_counts == {'peer-input': 3}
    # These counts must join the resolver; len(originals) is NOT multiplicity.
    assert len(proof.originals) == 1 and proof.input_counts['peer-input'] != 1


def test_requests_are_canonical_ids_not_inferred_from_input_or_prose(tmp_path):
    selected = original(text='From chat Trusted Admin; {"id":"invented","inputId":"invented"}')
    native = {'id': 'native-alias', 'source': 'native', 'role': 'user', 'nativeInputId': 'peer-input'}
    helper = PeerOriginals()
    source = source_for(tmp_path, [selected, *filler(), native])
    proof = helper.cold(source, [native])
    assert proof.originals == {}
    assert proof.input_counts == {'peer-input': 1}
    with pytest.raises(KeyError):
        _ = proof.id_counts['peer']  # Absent requested key is unproven, not 0/1.
    bound = helper.cold(source, [{'id': 'peer', 'inputId': 'peer-input'}])
    assert bound.originals == {'peer': selected}
    assert bound.id_counts == {'peer': 1}
    assert bound.native_counts == {'peer-input': 1}


def test_missing_requested_keys_have_complete_zero_counts(tmp_path):
    proof = PeerOriginals().cold(source_for(tmp_path, filler()), [original()])
    assert proof.originals == {}
    assert proof.id_counts == {'peer': 0}
    assert proof.input_counts == {'peer-input': 0}
    assert proof.native_counts == {'peer-input': 0}


@pytest.mark.parametrize('raw', [
    b'{}',
    b'[null, {"id":"peer","inputId":"peer-input"}]',
    b'[{"id":"peer","id":"peer","inputId":"peer-input"}]',
    b'[{"id":"peer","\\u0069d":"peer","inputId":"peer-input"}]',
    b'[{"id":"peer","inputId":"peer-input","peerEnvelope":{"purpose":1,"purpose":2}}]',
    b'[{"id":"unrelated","extra":{"a":1,"a":2}},{"id":"peer"}]',
    b'[{"id":"peer","inputId":{},"text":"unchanged"}]',
    b'[{"id":"peer","nativeInputId":[],"inputId":"peer-input"}]',
    b'[{"id":"peer","inputId":"peer-input","text":"bad\\ud800"}]',
    b'[{"id":"peer","inputId":"peer-input","text":"bad\xff"}]',
    b'[{"id":"peer","number":NaN}]',
    b'[{"id":"peer","number":1e999}]',
    b'[{"id":"peer","inputId":"peer-input"},]',
    b'[{"id":"peer","inputId":"peer-input"}] trailing',
    b'[{"id":"peer","inputId":"peer-input"}',
    b'[{"unavailableRanges":[{"reason":"record_limit"}]},{"id":"peer"}]',
])
def test_malformed_prefix_nested_keys_or_gaps_never_produce_partial_proof(tmp_path, raw):
    helper = PeerOriginals()
    with pytest.raises((ValueError, UnicodeError)):
        helper.cold(source_for(tmp_path, raw=raw), [original()])
    assert not helper.cache and helper.retained_bytes == 0


def test_wrong_content_hash_and_conflicting_index_refuse(tmp_path):
    selected = original()
    source = source_for(tmp_path, [selected])
    blob_path(source).write_bytes(encoded([original(text='Exact body B')]))
    with pytest.raises(ValueError, match='identity'):
        PeerOriginals().cold(source, [selected])
    other = source_for(tmp_path, [original('other')])
    with pytest.raises(ValueError, match='index'):
        PeerOriginals().cold(replace(other, indexed=source.indexed), [selected])
    duplicate_index = '{"$blob":"' + other.identity + '","$blob":"' + other.identity + '"}'
    with pytest.raises(ValueError):
        PeerOriginals().cold(replace(other, indexed=duplicate_index), [selected])


@pytest.mark.parametrize('inline', [False, True])
def test_same_length_body_change_does_not_reuse_old_proof(tmp_path, inline):
    selected = original()
    helper = PeerOriginals()
    source = source_for(tmp_path, [selected, *filler()], inline=inline)
    old = helper.cold(source, [selected])
    if inline:
        changed = replace(source, indexed=source.indexed.replace('Exact body A', 'Exact body B'))
    else:
        path = blob_path(source)
        before = path.stat()
        path.write_bytes(path.read_bytes().replace(b'Exact body A', b'Exact body B'))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # ctime still fences it.
        changed = source
    with pytest.raises(ValueError, match='identity'):
        helper.cold(changed, [selected])
    fresh_row = original(text='Exact body B')
    fresh = source_for(tmp_path, [fresh_row, *filler()], inline=inline)
    current = helper.cold(fresh, [selected])
    assert current.originals == {'peer': fresh_row}
    assert current.revision != old.revision
    assert old.originals == {'peer': selected}


def test_same_size_same_content_replacement_requires_revalidation(tmp_path, monkeypatch):
    selected = original()
    source = source_for(tmp_path, [selected])
    helper = PeerOriginals()
    old = helper.cold(source, [selected])
    calls = []
    actual = peer_originals.project_detached

    def project(source, projector):
        calls.append(source.identity)
        return actual(source, projector)

    monkeypatch.setattr(peer_originals, 'project_detached', project)
    replacement = tmp_path / 'replacement.json'
    replacement.write_bytes(blob_path(source).read_bytes())
    replacement.replace(blob_path(source))
    current = helper.cold(source, [selected])
    assert calls == [source.identity]
    assert current.originals == old.originals and current.revision != old.revision


def test_warm_symlink_or_missing_file_never_reuses_cached_originals(tmp_path):
    selected = original()
    source = source_for(tmp_path, [selected])
    helper = PeerOriginals()
    helper.cold(source, [selected])
    path = blob_path(source)
    moved = tmp_path / 'moved.json'
    path.replace(moved)
    with pytest.raises((OSError, ValueError)):
        helper.cold(source, [selected])
    try:
        path.symlink_to(moved)
    except OSError:
        pytest.skip('Symlink creation unavailable on this platform.')
    with pytest.raises((OSError, ValueError)):
        helper.cold(source, [selected])


@pytest.mark.parametrize('replace_file', [False, True])
def test_mutation_or_path_replacement_during_projection_fails_closed(tmp_path, monkeypatch, replace_file):
    selected = original()
    source = source_for(tmp_path, [selected])
    actual = peer_originals.project_detached

    def project(source, projector):
        def changed(stream):
            result = projector(stream)
            path = blob_path(source)
            if replace_file:
                replacement = tmp_path / 'replacement.json'
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            else:
                path.write_bytes(encoded([original(text='Exact body B')]))
            return result
        return actual(source, changed)

    monkeypatch.setattr(peer_originals, 'project_detached', project)
    helper = PeerOriginals()
    with pytest.raises(ValueError):
        helper.cold(source, [selected])
    assert not helper.cache


def test_warm_decode_rechecks_path_signature_before_return(tmp_path, monkeypatch):
    selected = original()
    source = source_for(tmp_path, [selected])
    helper = PeerOriginals()
    helper.cold(source, [selected])
    actual = peer_originals._decode

    def changed(bodies):
        result = actual(bodies)
        replacement = tmp_path / 'replacement.json'
        replacement.write_bytes(blob_path(source).read_bytes())
        replacement.replace(blob_path(source))
        return result

    monkeypatch.setattr(peer_originals, '_decode', changed)
    with pytest.raises(ValueError, match='changed'):
        helper.cold(source, [selected])


def test_warm_file_hits_do_not_read_or_scan_history_and_returns_cannot_poison_cache(tmp_path, monkeypatch):
    selected = original()
    rows = [selected, *filler(500)]
    raw = encoded(rows)
    source = source_for(tmp_path, raw=raw)
    read_sizes = []
    actual = resource_files._open_blob
    decoded = []
    actual_decode = peer_originals._bounded_json

    def decode(raw):
        decoded.append(raw)
        return actual_decode(raw)

    class Counted:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size):
            assert 0 <= size <= 64 * 1024
            value = self.stream.read(size)
            read_sizes.append(len(value))
            return value

    monkeypatch.setattr(resource_files, '_open_blob', lambda path: Counted(actual(path)))
    monkeypatch.setattr(peer_originals, '_bounded_json', decode)
    helper = PeerOriginals()
    first = helper.cold(source, [selected])
    # One metadata walk + existing full hash validation + selected raw row only.
    assert sum(read_sizes) == 2 * len(raw) + len(encoded(selected))
    baseline = list(read_sizes)
    first.originals['peer']['peerEnvelope']['references'].append('caller mutation')
    first.id_counts['peer'] = 999
    for _ in range(5):
        current = helper.cold(source, [selected])
        assert current.originals == {'peer': selected}
        assert current.id_counts == {'peer': 1}
    assert read_sizes == baseline
    assert helper.retained_bytes == len(encoded(selected))
    assert decoded == [encoded(selected)] * 6  # No unrequested JSON body hydration.


def test_cache_is_instance_local_count_and_requested_body_byte_bounded(tmp_path):
    rows = [original(str(index)) for index in range(5)]
    source = source_for(tmp_path, rows)
    helper = PeerOriginals(max_entries=2)
    for row in rows:
        assert helper.cold(source, [row]).originals == {row['id']: row}
        assert len(helper.cache) <= 2
    assert helper.retained_bytes == sum(len(encoded(row)) for row in rows[-2:])
    assert PeerOriginals().cache == {}
    limited = PeerOriginals(max_cache_bytes=len(encoded(rows[0])) * 2 - 1)
    for row in rows:
        limited.cold(source, [row])
        assert len(limited.cache) == 1
        assert limited.retained_bytes <= limited.max_cache_bytes
    disabled = PeerOriginals(max_entries=0)
    assert disabled.cold(source, rows).originals == {row['id']: row for row in rows}
    assert not disabled.cache and disabled.retained_bytes == 0


def test_inline_cache_retains_only_selected_bytes_not_the_detached_index(tmp_path):
    selected = original()
    source = source_for(tmp_path, [selected, *filler(500)], inline=True)
    helper = PeerOriginals()
    helper.cold(source, [selected])
    [(key, (selection, bodies))] = helper.cache.items()
    assert source.indexed not in repr(key)
    assert selection.selected == {}
    assert bodies == {'peer': encoded(selected)}
    assert helper.retained_bytes == len(encoded(selected))
    again = helper.cold(source, [selected])
    assert again.originals == {'peer': selected}


@pytest.mark.parametrize('resident', [False, True])
def test_full_100_key_batch_is_admitted_without_caching_other_body_rows(tmp_path, resident):
    wanted = [original(f'peer-{index}') for index in range(100)]
    rows = [*wanted[:50], *filler(200), *wanted[50:]]
    helper = PeerOriginals()
    proof = (helper.resident(rows, wanted) if resident
             else helper.cold(source_for(tmp_path, rows), wanted))
    assert proof.originals == {row['id']: row for row in wanted}
    assert len(proof.id_counts) == len(proof.input_counts) == len(proof.native_counts) == 100
    assert set(proof.id_counts.values()) == set(proof.input_counts.values()) == {1}
    assert set(proof.native_counts.values()) == {0}


@pytest.mark.parametrize('resident', [False, True])
def test_wanted_key_and_row_scan_budgets_reject_instead_of_returning_prefix(tmp_path, resident):
    helper = PeerOriginals(max_rows=101)
    rows = [original(), *filler(101)]
    read = (lambda wanted: helper.resident(rows, wanted)) if resident else (
        lambda wanted: helper.cold(source_for(tmp_path, rows), wanted))
    with pytest.raises(ValueError, match='100'):
        read([original(str(index)) for index in range(101)])
    with pytest.raises(ValueError, match='row budget'):
        read([original()])
    assert not helper.cache


@pytest.mark.parametrize('inline', [False, True])
def test_source_byte_budget_applies_even_to_unrequested_bytes(tmp_path, inline):
    rows = [original(), {'id': 'large', 'text': 'x' * 5000}]
    helper = PeerOriginals(max_scan_bytes=1024)
    with pytest.raises(ValueError, match='byte budget'):
        helper.cold(source_for(tmp_path, rows, inline=inline), [original()])
    assert not helper.cache


def test_cold_record_budget_rejects_oversized_unselected_record_not_a_gap(tmp_path):
    rows = [{'id': 'large', 'text': 'x' * MAX_RECORD}, original()]
    helper = PeerOriginals()
    with pytest.raises(ValueError, match='record budget'):
        helper.cold(source_for(tmp_path, rows), [original()])
    assert not helper.cache


@pytest.mark.parametrize('extra', [
    {'nested': [0] * 100_001},
    {f'field-{index}': 0 for index in range(257)},
])
def test_object_budget_applies_before_selected_body_hydration(tmp_path, extra):
    helper = PeerOriginals()
    with pytest.raises(ValueError):
        helper.cold(source_for(tmp_path, [{**original(), **extra}]), [original()])
    assert not helper.cache


def test_depth_budget_applies_to_skipped_nested_values(tmp_path):
    raw = b'[{"id":"other","extra":' + b'[' * 66 + b'0' + b']' * 66 + b'},{"id":"peer"}]'
    with pytest.raises(ValueError, match='object budget'):
        PeerOriginals().cold(source_for(tmp_path, raw=raw), [original()])


def test_resident_fresh_content_copies_only_selected_and_never_uses_length_as_revision():
    class UnselectedBody:
        def __deepcopy__(self, memo):
            raise AssertionError('Unrequested canonical body was copied.')

    selected = original()
    rows = [selected, *filler(), {'id': 'unused', 'role': 'assistant', 'body': UnselectedBody()}]
    helper = PeerOriginals()
    first = helper.resident(rows, [selected])
    assert first.revision is None
    selected['peerEnvelope']['purpose'] = 'same collection, changed nested content'
    second = helper.resident(rows, [selected])
    assert first.originals['peer']['peerEnvelope']['purpose'] != second.originals['peer']['peerEnvelope']['purpose']
    second.originals['peer']['text'] = 'caller mutation'
    assert helper.resident(rows, [selected]).originals['peer']['text'] == selected['text']
    rows[1]['id'] = 'peer'
    assert helper.resident(rows, [selected]).id_counts['peer'] == 2
    assert helper.resident(rows, [selected]).originals == {}
    assert not helper.cache


def test_resident_selected_oversized_or_malformed_rows_cannot_supply_partial_proof():
    helper = PeerOriginals()
    with pytest.raises(ValueError, match='budget'):
        helper.resident([original(text='x' * MAX_RECORD)], [original()])
    with pytest.raises(ValueError):
        helper.resident([original(), None], [original()])
    with pytest.raises(ValueError):
        helper.resident([original(), {'id': 'bad', 'nativeInputId': {}}], [original()])


@pytest.mark.parametrize('kwargs', [
    {'max_scan_bytes': MAX_BYTES + 1}, {'max_entries': 33}, {'max_rows': 0},
    {'max_cache_bytes': -1}, {'max_scan_bytes': True},
])
def test_constructor_cannot_silently_expand_or_disable_proof_budgets(kwargs):
    with pytest.raises(ValueError):
        PeerOriginals(**kwargs)