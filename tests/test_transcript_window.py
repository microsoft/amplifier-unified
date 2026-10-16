import base64
import json
from pathlib import Path

import pytest

from amplifier_web import transcript_window as module
from amplifier_web.transcript_window import TranscriptWindowReader

OWNER = {'project': 'project', 'nativeId': 'session'}


def write(root, values, name='transcript.jsonl'):
    path = root / name
    path.write_bytes(b''.join(json.dumps(value).encode() + b'\n' for value in values))
    return path


def row(text, role='user'):
    return {'role': role, 'content': text}


def test_recent_cursor_exact_rows_identity_and_unknown_authority(tmp_path):
    values = [row(str(i), 'assistant' if i % 2 else 'user') for i in range(230)]
    write(tmp_path, values)
    reader = TranscriptWindowReader()
    pages = []
    before = None
    for expected in (values[-100:], values[30:130], values[:30]):
        result = reader.read_window(tmp_path, OWNER, before=before)
        assert result['status'] == 'ready'
        assert [item['value'] for item in result['rows']] == expected
        assert result['messageWindow']['total'] is None
        assert result['messageWindow']['offset'] is None
        assert result['messageWindow']['globalInputUniqueness'] == 'unknown'
        assert all('nativeIndex' not in item and 'nativeMessageId' not in item for item in result['rows'])
        pages.extend(result['rows'])
        before = result['messageWindow']['before']
    assert before is None
    assert len({item['displayId'] for item in pages}) == 230
    again = reader.read_window(tmp_path, OWNER)
    assert again['rows'] == pages[:100]


def test_cursor_rejects_other_host_owner_and_source_change(tmp_path):
    path = write(tmp_path, [row(str(i)) for i in range(5)])
    reader = TranscriptWindowReader()
    token = reader.read_window(tmp_path, OWNER, limit=2)['messageWindow']['before']
    assert TranscriptWindowReader().read_window(tmp_path, OWNER, before=token)['reason'] == 'invalid_cursor'
    assert reader.read_window(tmp_path, {**OWNER, 'nativeId': 'other'}, before=token)['reason'] == 'source_changed'
    assert reader.read_window(tmp_path, OWNER, before=token[:-2] + 'xx')['reason'] == 'invalid_cursor'
    with path.open('ab') as stream:
        stream.write(b'{"role":"user","content":"new"}\n')
    assert reader.read_window(tmp_path, OWNER, before=token)['reason'] == 'source_changed'


def test_restore_cursor_replays_current_page_not_preceding_page(tmp_path):
    values = [row(str(i)) for i in range(230)]
    write(tmp_path, values)
    reader = TranscriptWindowReader()
    before = None
    for expected in (values[-100:], values[30:130], values[:30]):
        page = reader.read_window(tmp_path, OWNER, before=before)
        window = page['messageWindow']
        restored = reader.read_window(tmp_path, OWNER, before=window['restoreCursor'])
        assert [item['value'] for item in restored['rows']] == expected
        assert restored == page
        before = window['before']
    assert before is None


def test_restore_cursor_rejects_tamper_owner_restart_and_source_changes(tmp_path):
    path = write(tmp_path, [row(str(i)) for i in range(101)])
    reader = TranscriptWindowReader()
    token = reader.read_window(tmp_path, OWNER)['messageWindow']['restoreCursor']
    assert len(token) <= 4096
    raw = bytearray(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
    raw[0] ^= 1
    forged = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    assert reader.read_window(tmp_path, OWNER, before=forged)['reason'] == 'invalid_cursor'
    assert reader.read_window(tmp_path, {**OWNER, 'project': 'other'}, before=token)['reason'] == 'source_changed'
    assert TranscriptWindowReader().read_window(tmp_path, OWNER, before=token)['reason'] == 'invalid_cursor'
    with path.open('ab') as stream:
        stream.write(json.dumps(row('new')).encode() + b'\n')
    assert reader.read_window(tmp_path, OWNER, before=token)['reason'] == 'source_changed'


def test_restore_cursor_is_one_bounded_read_even_behind_later_pages(tmp_path, monkeypatch):
    # The malformed unread prefix must not be indexed or parsed for restoration.
    values = [row(str(i) + 'x' * 1000) for i in range(1000)]
    path = write(tmp_path, values)
    path.write_bytes(b'not-json\n' + path.read_bytes())
    reader = TranscriptWindowReader()
    page = reader.read_window(tmp_path, OWNER)
    for _ in range(5):
        page = reader.read_window(tmp_path, OWNER, before=page['messageWindow']['before'])
    calls = []
    original = reader._read
    def bounded(*args):
        result = original(*args)
        calls.append(args[-1]['read'])
        return result
    monkeypatch.setattr(reader, '_read', bounded)
    restored = reader.read_window(tmp_path, OWNER, before=page['messageWindow']['restoreCursor'])
    assert restored == page
    assert len(calls) == 1 and calls[0] < 3 * module.BLOCK
    assert len(restored['rows']) == 100
    assert restored['messageWindow']['total'] is None


def test_append_intent_uses_backup_and_never_uncommitted_primary(tmp_path):
    write(tmp_path, [row('uncommitted')])
    write(tmp_path, [row('safe')], 'transcript.jsonl.backup')
    (tmp_path / 'transcript.jsonl.append-pending').touch()
    result = TranscriptWindowReader().read_window(tmp_path, OWNER)
    assert [item['value'] for item in result['rows']] == [row('safe')]
    assert result['messageWindow']['source'] == 'backup'
    (tmp_path / 'transcript.jsonl.backup').unlink()
    assert TranscriptWindowReader().read_window(tmp_path, OWNER)['status'] == 'unavailable'


def test_corrupt_window_recovers_backup_but_does_not_claim_prefix_validation(tmp_path):
    write(tmp_path, [row('safe')], 'transcript.jsonl.backup')
    (tmp_path / 'transcript.jsonl').write_bytes(b'{broken}\n')
    result = TranscriptWindowReader().read_window(tmp_path, OWNER)
    assert result['messageWindow']['recoveredBackup']
    (tmp_path / 'transcript.jsonl').write_bytes(b'{broken prefix}\n' + json.dumps(row('recent')).encode() + b'\n')
    result = TranscriptWindowReader().read_window(tmp_path, OWNER, limit=1)
    assert result['rows'][0]['value'] == row('recent')
    assert result['messageWindow']['validation'] == 'window'
    assert not result['messageWindow']['recoveredBackup']
    assert result['messageWindow']['hasEarlier']


def test_bounded_io_and_visible_selection_ignores_huge_prefix(tmp_path, monkeypatch):
    values = [row('x' * 1000) for _ in range(20000)] + [row('tool', 'tool'), row('visible')]
    path = write(tmp_path, values)
    calls = []
    original = module.os.fdopen
    class Guard:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def __getattr__(self, key): return getattr(self.stream, key)
        def read(self, count=-1):
            assert 0 <= count <= module.BLOCK
            calls.append(count)
            return self.stream.read(count)
    def opening(*args, **kwargs):
        return Guard(original(*args, **kwargs))
    monkeypatch.setattr(module.os, 'fdopen', opening)
    result = TranscriptWindowReader().read_window(tmp_path, OWNER, limit=1, visible=lambda value: value['role'] != 'tool')
    assert result['rows'][0]['value'] == row('visible')
    assert sum(calls) <= module.BLOCK + 1
    assert result['messageWindow']['hasEarlier']


def test_oversize_and_budget_gaps_progress_without_losing_readable_neighbors(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'READ_LIMIT', 1024)
    monkeypatch.setattr(module, 'RECORD_LIMIT', 256)
    monkeypatch.setattr(module, 'BLOCK', 128)
    write(tmp_path, [row('older'), row('x' * 4000), row('recent')])
    reader = TranscriptWindowReader()
    before = None
    values = []
    gaps = []
    tokens = set()
    for _ in range(8):
        result = reader.read_window(tmp_path, OWNER, before=before)
        assert result['status'] == 'ready'
        assert result['coverage']['scannedBytes'] <= 1024
        values += [item['value']['content'] for item in result['rows']]
        gaps += result['unavailableRanges']
        before = result['messageWindow']['before']
        if before is None:
            break
        assert before not in tokens
        tokens.add(before)
    assert values == ['recent', 'older']
    assert gaps and before is None


def test_source_change_during_predicate_discards_entire_result(tmp_path):
    path = write(tmp_path, [row('old')])
    def mutate(value):
        path.write_text(json.dumps(row('changed')) + '\n')
        return True
    assert TranscriptWindowReader().read_window(tmp_path, OWNER, visible=mutate) == {
        'status': 'retry', 'reason': 'source_changed'}


@pytest.mark.parametrize('tail', [b'{"role":"user","content":NaN}', b'{"role":""}', b'[]'])
def test_invalid_rows_fail_closed(tmp_path, tail):
    (tmp_path / 'transcript.jsonl').write_bytes(tail)
    assert TranscriptWindowReader().read_window(tmp_path, OWNER)['status'] == 'unavailable'


def test_blank_lines_crlf_and_unterminated_final_record(tmp_path):
    values = [row('one'), row('two')]
    (tmp_path / 'transcript.jsonl').write_bytes(b'\n' + json.dumps(values[0]).encode() + b'\r\n\n' + json.dumps(values[1]).encode())
    result = TranscriptWindowReader().read_window(tmp_path, OWNER)
    assert [item['value'] for item in result['rows']] == values


def test_structural_expansion_is_a_bounded_explicit_gap(tmp_path):
    deep = b'{"role":"tool","content":' + b'[' * 100 + b'0' + b']' * 100 + b'}\n'
    (tmp_path / 'transcript.jsonl').write_bytes(json.dumps(row('before')).encode() + b'\n' + deep + json.dumps(row('after')).encode() + b'\n')
    result = TranscriptWindowReader().read_window(tmp_path, OWNER)
    assert [item['value']['content'] for item in result['rows']] == ['before', 'after']
    assert result['unavailableRanges'][0]['reason'] == 'object_limit'


def test_actual_large_recent_message_is_returned_without_prefix_materialization(tmp_path):
    text = 'a' * 3_530_000
    path = write(tmp_path, [row('prefix' * 2_000_000), row(text)])
    result = TranscriptWindowReader().read_window(tmp_path, OWNER, limit=1)
    assert result['rows'][0]['value']['content'] == text
    assert result['coverage']['scannedBytes'] < 3_600_000
    assert result['messageWindow']['hasEarlier']
    assert not result['unavailableRanges']


@pytest.mark.skipif(not hasattr(module.os, 'mkfifo'), reason='POSIX named pipes')
def test_non_regular_source_is_rejected_without_blocking_and_backup_recovers(tmp_path):
    module.os.mkfifo(tmp_path / 'transcript.jsonl')
    write(tmp_path, [row('safe')], 'transcript.jsonl.backup')
    result = TranscriptWindowReader().read_window(tmp_path, OWNER)
    assert result['rows'][0]['value'] == row('safe')
    assert result['messageWindow']['recoveredBackup']
