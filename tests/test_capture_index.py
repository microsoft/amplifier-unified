from functools import partial
import json
import sys
from pathlib import Path
import sqlite3

import pytest

from amplifier_web.capture_index import index_shared, CaptureDrift, hold_replacement, rebind_replaced_capture
from amplifier_web.session_files import capture_dir, read_event


@pytest.fixture(params=[False, True], autouse=True)
def metadata_validation_mode(request, monkeypatch):
    # Exercise indexing contracts through legacy and cached metadata validation.
    if request.param:
        from amplifier_web.capture_index import CaptureMetadataCache
        monkeypatch.setattr(sys.modules[__name__], 'index_shared',
                            partial(index_shared, metadata_cache=CaptureMetadataCache()))


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER);'
                            'CREATE TABLE records(id TEXT PRIMARY KEY,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT);'
                            'CREATE TABLE deliveries(record_id TEXT);')
    yield connection
    connection.close()


def write_capture(workspace, session, text, *, append=False):
    directory = capture_dir(workspace, session)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'metadata.json').write_text(json.dumps({'format': 'context-intelligence', 'version': '1.0.0'}))
    with (directory / 'events.jsonl').open('a' if append else 'w') as stream:
        stream.write(json.dumps({'event': 'tool:post', 'timestamp': '2026-09-23T00:00:00Z',
                                 'data': {'event_id': text, 'result': text}}) + '\n')
    return directory


def test_shared_workspace_children_stay_distinct_and_appends_are_incremental(db, tmp_path):
    workspace = str(tmp_path / 'Project.name_with spaces')
    scopes = [(workspace, 'root'), (workspace, 'child-1'), (workspace, 'child-2')]
    for _, session in scopes:
        write_capture(workspace, session, session)
    rows = index_shared(db, scopes + scopes, {'streams': ['tools']})
    assert {r[3] for r in rows} == {'root', 'child-1', 'child-2'}
    assert index_shared(db, scopes, {'streams': ['tools']}) == []
    write_capture(workspace, 'child-2', 'next', append=True)
    rows = index_shared(db, scopes, {'streams': ['tools']})
    assert len(rows) == 1 and rows[0][3] == 'child-2' and rows[0][-1]['result'] == 'next'
    references = [json.loads(r[0]) for r in db.execute('SELECT data FROM records')]
    assert {read_event(r)['data']['result'] for r in references} == {'root', 'child-1', 'child-2', 'next'}


@pytest.mark.parametrize('setting', ['AMPLIFIER_HOME', 'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'])
def test_capture_relocation_is_seen_between_scans(db, tmp_path, monkeypatch, setting):
    workspace = str(tmp_path / 'workspace')
    scopes = [(workspace, 'root'), (workspace, 'child')]
    for generation in ('first', 'second'):
        monkeypatch.setenv(setting, str(tmp_path / generation))
        for _, session in scopes:
            write_capture(workspace, session, generation + '-' + session)
        rows = index_shared(db, scopes, {'streams': ['tools']})
        assert {r[-1]['result'] for r in rows} == {generation + '-root', generation + '-child'}
    assert db.execute('SELECT count(*) FROM captures').fetchone()[0] == 4


def test_workspace_symlink_change_is_seen_between_scans(db, tmp_path):
    link = tmp_path / 'workspace-link'
    for generation in ('first', 'second'):
        target = tmp_path / generation
        target.mkdir()
        link.unlink(missing_ok=True)
        link.symlink_to(target, target_is_directory=True)
        write_capture(str(link), 'child', generation)
        rows = index_shared(db, [(str(link), 'child')], {'streams': ['tools']})
        assert [r[-1]['result'] for r in rows] == [generation]


def test_every_capture_metadata_is_validated_on_each_scan(db, tmp_path):
    workspace = str(tmp_path)
    scopes = [(workspace, 'one'), (workspace, 'two')]
    for _, session in scopes:
        write_capture(workspace, session, session)
    index_shared(db, scopes, {'streams': ['tools']})
    path = capture_dir(workspace, 'two') / 'metadata.json'
    path.write_text(json.dumps({'format': 'context-intelligence', 'version': '2.0.0'}))
    with pytest.raises(ValueError, match='Unsupported Context Intelligence'):
        index_shared(db, scopes, {'streams': ['tools']})
    assert json.loads(path.read_text())['version'] == '2.0.0'


def test_invalid_child_id_is_rejected_even_with_shared_workspace(db, tmp_path):
    write_capture(str(tmp_path), 'valid', 'valid')
    with pytest.raises(ValueError, match='Invalid session identifier'):
        index_shared(db, [(str(tmp_path), 'valid'), (str(tmp_path), '../escape')], {'streams': ['tools']})


@pytest.mark.parametrize('row_factory', [None, sqlite3.Row])
def test_unchanged_eof_is_still_read_without_rewriting_offsets(db, tmp_path, monkeypatch, row_factory):
    db.row_factory = row_factory
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'first')
    path = directory / 'events.jsonl'
    scopes, config = [(workspace, 'root')], {'streams': ['tools']}
    assert len(index_shared(db, scopes, config)) == 1
    before = tuple(db.execute('SELECT offset,inode FROM captures').fetchone())
    changes = db.total_changes
    opens = []
    original_open = Path.open

    def record_open(self, *args, **kwargs):
        if self == path:
            opens.append(args)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', record_open)
    assert index_shared(db, scopes, config) == []
    assert opens == [('rb',)]
    assert tuple(db.execute('SELECT offset,inode FROM captures').fetchone()) == before
    assert db.total_changes == changes
    write_capture(workspace, 'root', 'next', append=True)
    assert [r[-1]['result'] for r in index_shared(db, scopes, config)] == ['next']
    assert db.execute('SELECT offset FROM captures').fetchone()[0] == path.stat().st_size
    assert db.total_changes == changes + 2  # One new event and one advanced cursor.


def test_partial_tail_keeps_cursor_until_completed_then_advances(db, tmp_path):
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'first')
    path = directory / 'events.jsonl'
    scopes, config = [(workspace, 'root')], {'streams': ['tools']}
    index_shared(db, scopes, config)
    before = db.execute('SELECT offset,inode FROM captures').fetchone()
    partial = json.dumps({'event': 'tool:post', 'timestamp': '2026-09-23T00:00:00Z',
                          'data': {'result': 'second'}})
    with path.open('a') as stream:
        stream.write(partial)
    changes = db.total_changes
    assert index_shared(db, scopes, config) == []
    assert db.execute('SELECT offset,inode FROM captures').fetchone() == before
    assert db.total_changes == changes
    with path.open('a') as stream:
        stream.write('\n')
    assert [r[-1]['result'] for r in index_shared(db, scopes, config)] == ['second']
    assert db.execute('SELECT offset FROM captures').fetchone()[0] == path.stat().st_size


def test_unqualified_replacement_holds_original_index_and_deliveries(db, tmp_path):
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'first')
    path = directory / 'events.jsonl'
    scopes, config = [(workspace, 'root')], {'streams': ['tools']}
    old = index_shared(db, scopes, config)[0]
    db.execute('INSERT INTO deliveries VALUES(?)', (old[0],))
    before = db.execute('SELECT offset,inode FROM captures').fetchone()
    replacement = directory / 'replacement.jsonl'
    replacement.write_text(path.read_text().replace('first', 'other'))
    replacement.replace(path)
    assert path.stat().st_size == before[0] and path.stat().st_ino != before[1]
    assert index_shared(db, scopes, config) == []
    assert db.execute('SELECT offset,inode FROM captures').fetchone() == before
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 1
    reference = json.loads(db.execute('SELECT data FROM records').fetchone()[0])
    with pytest.raises(ValueError, match='stale'):
        read_event(reference)


def test_truncation_and_new_empty_capture_keep_required_cursor_writes(db, tmp_path):
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'first')
    path = directory / 'events.jsonl'
    scopes, config = [(workspace, 'root')], {'streams': ['tools']}
    old = index_shared(db, scopes, config)[0]
    db.execute('INSERT INTO deliveries VALUES(?)', (old[0],))
    inode = path.stat().st_ino
    path.write_bytes(b'')
    assert index_shared(db, scopes, config) == []
    assert db.execute('SELECT offset,inode FROM captures').fetchone()[1] == inode
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 1
    changes = db.total_changes
    assert index_shared(db, scopes, config) == []
    assert db.total_changes == changes
    empty = write_capture(workspace, 'empty', 'discard') / 'events.jsonl'
    empty.write_bytes(b'')
    assert index_shared(db, [(workspace, 'empty')], config) == []
    assert db.total_changes == changes + 1
    assert db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(empty),)).fetchone() == (0, empty.stat().st_ino)


def test_unchanged_eof_still_propagates_read_errors_without_advancing(db, tmp_path, monkeypatch):
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'first')
    path = directory / 'events.jsonl'
    scopes, config = [(workspace, 'root')], {'streams': ['tools']}
    index_shared(db, scopes, config)
    before = db.execute('SELECT offset,inode FROM captures').fetchone()
    changes = db.total_changes
    original_open = Path.open

    def deny_open(self, *args, **kwargs):
        if self == path:
            raise PermissionError('Capture read denied')
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', deny_open)
    with pytest.raises(PermissionError, match='Capture read denied'):
        index_shared(db, scopes, config)
    assert db.execute('SELECT offset,inode FROM captures').fetchone() == before
    assert db.total_changes == changes


def test_stream_configuration_changes_keep_current_offset_without_backfill(db, tmp_path):
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'not-enabled')
    scopes = [(workspace, 'root')]
    assert index_shared(db, scopes, {'streams': []}) == []
    write_capture(workspace, 'root', 'enabled-now', append=True)
    assert [row[-1]['result'] for row in index_shared(db, scopes, {'streams': ['tools']})] == ['enabled-now']
    write_capture(workspace, 'root', 'disabled-again', append=True)
    assert index_shared(db, scopes, {'streams': []}) == []
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert db.execute('SELECT offset FROM captures').fetchone()[0] == (directory / 'events.jsonl').stat().st_size


def replacement_fixture(db, tmp_path):
    import hashlib
    workspace = str(tmp_path)
    directory = write_capture(workspace, 'root', 'same-id')
    write_capture(workspace, 'root', 'same-id', append=True)
    path = directory / 'events.jsonl'
    index_shared(db, [(workspace, 'root')], {'streams': ['tools']})
    # IDs need not be event_id; two repeated event IDs remain distinct.
    db.executescript('DROP TABLE deliveries; CREATE TABLE deliveries('
                     'record_id TEXT,destination TEXT,status TEXT,attempts INTEGER,payload TEXT,error TEXT);')
    for i, (identity,) in enumerate(db.execute('SELECT id FROM records')):
        db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',
                   (identity, 'dest', 'accepted' if i else 'pending', i, '{"exact":"kept"}', None))
    original = path.read_bytes()
    converted = original.replace(b'"result": "same-id"', b'"result": "image reference, original metadata retained"')
    describe = lambda raw: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    witness = {'old': describe(original), 'new': describe(converted)}
    old_offset = new_offset = 0
    mapping = []
    for old, new in zip(original.splitlines(keepends=True), converted.splitlines(keepends=True)):
        mapping.append({'old': {**describe(old), 'offset': old_offset},
                        'new': {**describe(new), 'offset': new_offset}})
        old_offset += len(old)
        new_offset += len(new)
    return workspace, path, converted, witness, mapping


def test_qualified_rebind_preserves_delivery_bytes_ids_and_no_backfill(db, tmp_path):
    workspace, path, converted, witness, mapping = replacement_fixture(db, tmp_path)
    records = db.execute('SELECT id,at,stream,session,workspace,event FROM records').fetchall()
    deliveries = db.execute('SELECT * FROM deliveries').fetchall()
    hold_replacement(db, path, 'operation', witness)
    db.commit()
    replacement = path.with_name('candidate')
    replacement.write_bytes(converted)
    replacement.replace(path)
    # Crash window remains protected until exact recovery, not delete+reindex.
    assert index_shared(db, [(workspace, 'root')], {'streams': ['tools']}) == []
    result = rebind_replaced_capture(db, path, 'operation', witness, iter(mapping))
    assert result['backfilled'] == result['deliveriesChanged'] == 0
    assert db.execute('SELECT id,at,stream,session,workspace,event FROM records').fetchall() == records
    assert db.execute('SELECT * FROM deliveries').fetchall() == deliveries
    assert index_shared(db, [(workspace, 'root')], {'streams': ['tools']}) == []
    for (raw,) in db.execute('SELECT data FROM records'):
        assert read_event(json.loads(raw))['data']['event_id'] == 'same-id'
    assert rebind_replaced_capture(db, path, 'operation', witness, iter(mapping))['alreadyRebound']
    write_capture(workspace, 'root', 'genuinely-new', append=True)
    assert [row[-1]['result'] for row in index_shared(db, [(workspace, 'root')], {'streams': ['tools']})] == ['genuinely-new']


@pytest.mark.parametrize('damage', ['missing', 'bad-hash', 'duplicate', 'cursor'])
def test_bad_rebind_rolls_back_and_keeps_hold_and_outcomes(db, tmp_path, damage):
    _, path, converted, witness, mapping = replacement_fixture(db, tmp_path)
    hold_replacement(db, path, 'operation', witness)
    db.commit()
    if damage == 'missing':
        mapping.pop()
    elif damage == 'duplicate':
        mapping.append(mapping[0])
    elif damage == 'bad-hash':
        mapping[-1]['new']['sha256'] = '0' * 64
    else:
        db.execute('UPDATE captures SET offset=1')
    before = db.execute('SELECT * FROM records').fetchall()
    deliveries = db.execute('SELECT * FROM deliveries').fetchall()
    candidate = path.with_name('candidate')
    candidate.write_bytes(converted)
    candidate.replace(path)
    with pytest.raises(CaptureDrift):
        rebind_replaced_capture(db, path, 'operation', witness, iter(mapping))
    assert db.execute('SELECT * FROM records').fetchall() == before
    assert db.execute('SELECT * FROM deliveries').fetchall() == deliveries
    assert db.execute('SELECT count(*) FROM capture_maintenance').fetchone()[0] == 1


def test_identical_later_slice_never_masquerades_as_already_rebound_record(db, tmp_path):
    import hashlib
    directory = write_capture(str(tmp_path), 'root', 'discard')
    path = directory / 'events.jsonl'
    same = b'{"event":"tool:post","data":{"result":"x"}}\n'
    large = same[:-1] + b' ' * len(same) + b'\n'
    original, converted = large + same + same, same * 3
    path.write_bytes(original)
    describe = lambda raw: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    witness = {'old': describe(original), 'new': describe(converted)}
    old_offset = new_offset = 0
    mappings = []
    for before, after in zip((large, same, same), (same, same, same)):
        mappings.append({'old': {**describe(before), 'offset': old_offset},
                         'new': {**describe(after), 'offset': new_offset}})
        old_offset += len(before)
        new_offset += len(after)
    reference = {'$event': str(path), **mappings[1]['old'], 'eventId': None}
    db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?)',
               ('retained-second', 1, 'tools', 'root', str(tmp_path), 'tool:post', json.dumps(reference)))
    db.execute('INSERT INTO deliveries VALUES(?)', ('retained-second',))
    db.execute('INSERT INTO captures VALUES(?,?,?)', (str(path), len(original), path.stat().st_ino))
    hold_replacement(db, path, 'same', witness)
    db.commit()
    candidate = path.with_name('candidate')
    candidate.write_bytes(converted)
    candidate.replace(path)
    rebind_replaced_capture(db, path, 'same', witness, iter(mappings))
    rebound = json.loads(db.execute('SELECT data FROM records').fetchone()[0])
    assert rebound['offset'] == len(same)  # NOT the coincidentally identical third slice.
    db.commit()
    assert rebind_replaced_capture(db, path, 'same', witness, iter(mappings))['alreadyRebound']
    assert db.execute('SELECT * FROM deliveries').fetchall() == [('retained-second',)]


@pytest.mark.parametrize('damage', ['inode', 'hash', 'legacy'])
def test_replacement_admission_refuses_stale_reference_before_any_effect(db, tmp_path, damage):
    _, path, _, witness, _ = replacement_fixture(db, tmp_path)
    if damage == 'inode':
        db.execute('UPDATE captures SET inode=1')
    else:
        identity, raw = db.execute('SELECT id,data FROM records LIMIT 1').fetchone()
        reference = json.loads(raw)
        if damage == 'legacy':
            reference.pop('sha256')
        else:
            reference['sha256'] = '0' * 64
        db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(reference), identity))
    before = path.read_bytes()
    deliveries = db.execute('SELECT * FROM deliveries').fetchall()
    with pytest.raises(CaptureDrift):
        hold_replacement(db, path, 'rejected', witness)
    assert path.read_bytes() == before
    assert db.execute('SELECT * FROM deliveries').fetchall() == deliveries
    assert db.execute('SELECT count(*) FROM capture_maintenance').fetchone()[0] == 0


def test_unindexed_unterminated_source_is_held_before_replacement(db, tmp_path):
    import hashlib
    path = tmp_path / 'events.jsonl'
    original = b'{"event":"llm:request","data":{"raw":"retain"}}'
    path.write_bytes(original)
    facts = {'bytes': len(original), 'sha256': hashlib.sha256(original).hexdigest()}
    with pytest.raises(CaptureDrift, match='unterminated'):
        hold_replacement(db, path, 'no-final-newline', {'old': facts, 'new': facts})
    assert path.read_bytes() == original
    assert db.execute('SELECT count(*) FROM capture_maintenance').fetchone()[0] == 0


@pytest.mark.parametrize('held', ['maintenance', 'inode'])
def test_capture_quarantine_keeps_other_sources_incremental(db, tmp_path, held):
    workspace = str(tmp_path)
    first = write_capture(workspace, 'held', 'old') / 'events.jsonl'
    write_capture(workspace, 'healthy', 'before')
    scopes = [(workspace, 'held'), (workspace, 'healthy')]
    index_shared(db, scopes, {'streams': ['tools']})
    held_before = db.execute('SELECT * FROM records WHERE session=?', ('held',)).fetchall()
    cursor = db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(first),)).fetchone()
    if held == 'maintenance':
        db.execute('INSERT INTO capture_maintenance VALUES(?,?,?)', (str(first), 'unknown-operation', '{}'))
    else:
        replacement = first.with_name('other')
        replacement.write_bytes(first.read_bytes())
        replacement.replace(first)
    write_capture(workspace, 'healthy', 'after', append=True)
    assert [row[-1]['result'] for row in index_shared(db, scopes, {'streams': ['tools']})] == ['after']
    assert db.execute('SELECT * FROM records WHERE session=?', ('held',)).fetchall() == held_before
    assert db.execute('SELECT offset,inode FROM captures WHERE path=?', (str(first),)).fetchone() == cursor
