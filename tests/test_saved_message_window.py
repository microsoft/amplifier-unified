import hashlib
import json
from pathlib import Path

import pytest

from amplifier_web.resource_files import Detached
from amplifier_web.transcript_window import TranscriptWindowReader
from amplifier_web import saved_message_window as module


def source(tmp_path, rows):
    raw = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()
    identity = hashlib.sha256(raw).hexdigest()
    (tmp_path / (identity + '.json')).write_bytes(raw)
    return Detached(tmp_path, identity, json.dumps({'$blob': identity}))


def test_saved_array_cursor_preserves_exact_ids_nested_objects_and_escapes(tmp_path):
    rows = [{'id': str(i), 'role': 'user', 'text': 'hi {"nested"} \\\\" unicode☃',
             'attachments': [{'id': 'a', 'value': ['x', {'quoted': '\\\\"'}]}]} for i in range(235)]
    saved = source(tmp_path, rows)
    signer = TranscriptWindowReader()
    before = None
    collected = []
    for _ in range(4):
        page = module.read_saved_window(saved, signer, session_id='app', fence=1, before=before)
        collected = page['items'] + collected
        before = page['before']
        if before is None:
            break
    assert collected == rows
    assert before is None
    with pytest.raises(ValueError):
        module.read_saved_window(saved, signer, session_id='app', fence=2,
                                before=module.read_saved_window(saved, signer, session_id='app', fence=1)['before'])


def test_huge_saved_record_progress_retains_readable_neighbors(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'MAX_BYTES', 1024)
    monkeypatch.setattr(module, 'MAX_RECORD', 256)
    rows = [{'id': 'older', 'text': 'old'}, {'id': 'large', 'text': 'x' * 4000}, {'id': 'recent', 'text': 'new'}]
    saved = source(tmp_path, rows)
    signer = TranscriptWindowReader()
    before = None
    ids, gaps, tokens = [], [], set()
    for _ in range(12):
        page = module.read_saved_window(saved, signer, session_id='app', fence=1, before=before)
        assert page['coverage']['scannedBytes'] <= 1024
        ids = [row['id'] for row in page['items']] + ids
        gaps += page['unavailableRanges']
        before = page['before']
        if before is None:
            break
        assert before not in tokens
        tokens.add(before)
    assert ids == ['older', 'recent']
    assert gaps and before is None


def test_window_bounds_ignore_large_array_prefix(tmp_path):
    saved = source(tmp_path, [{'id': 'big', 'text': 'x' * 12_000_000}] +
                   [{'id': str(i), 'text': str(i)} for i in range(150)])
    page = module.read_saved_window(saved, TranscriptWindowReader(), session_id='app', fence=1)
    assert [row['id'] for row in page['items']] == [str(i) for i in range(50, 150)]
    assert page['coverage']['scannedBytes'] <= 8 * 1024 * 1024
    assert page['hasEarlier']
