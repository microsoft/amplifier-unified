import json
from pathlib import Path
import sqlite3

import pytest

from amplifier_web.capture_index import index_shared
from amplifier_web.session_files import capture_dir, read_event


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


def test_replacement_at_same_final_offset_updates_inode_and_clears_stale_records(db, tmp_path):
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
    assert [r[-1]['result'] for r in index_shared(db, scopes, config)] == ['other']
    assert db.execute('SELECT offset,inode FROM captures').fetchone() == (before[0], path.stat().st_ino)
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
    reference = json.loads(db.execute('SELECT data FROM records').fetchone()[0])
    assert read_event(reference)['data']['result'] == 'other'


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
    assert db.execute('SELECT offset,inode FROM captures').fetchone() == (0, inode)
    assert db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0
    changes = db.total_changes
    assert index_shared(db, scopes, config) == []
    assert db.total_changes == changes
    empty = write_capture(workspace, 'empty', 'discard') / 'events.jsonl'
    empty.write_bytes(b'')
    assert index_shared(db, scopes + [(workspace, 'empty')], config) == []
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
