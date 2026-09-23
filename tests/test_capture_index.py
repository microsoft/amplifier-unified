import json
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
