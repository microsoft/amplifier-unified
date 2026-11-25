"""Execution-index placement preserves immutable identity and writer ownership."""
import json
from pathlib import Path
import sqlite3

import pytest

from amplifier_web import resource_files as files


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'state.sqlite3', timeout=0)
    connection.execute('CREATE TABLE state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.commit()
    yield connection
    connection.close()


def indexed(db, identity):
    row = db.execute('SELECT value FROM state_resources WHERE id=?', (identity,)).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize('size', [8192, 8193])
def test_execution_index_ceiling_uses_canonical_utf8_bytes_and_prepares_once(db, monkeypatch, size):
    value = {'records': {'unicode': 'é' * 4000, 'padding': ''}}
    value['records']['padding'] = 'x' * (size - files.prepare(value).size)
    prepared = files.prepare(value)
    assert prepared.size == size
    real_prepare, calls = files.prepare, []

    def prepare(value):
        calls.append(value)
        return real_prepare(value)

    monkeypatch.setattr(files, 'prepare', prepare)
    reference = files.put_execution_index(db, value)
    assert len(calls) == 1
    assert reference == {'$resource': prepared.identity, 'bytes': size}
    path = files.root(db) / (prepared.identity + '.json')
    assert path.exists() == (size > 8192)
    assert json.loads(indexed(db, prepared.identity)) == (
        {'$blob': prepared.identity} if size > 8192 else value)
    db.commit()
    with sqlite3.connect(db.execute('PRAGMA database_list').fetchone()[2]) as reopened:
        assert files.read_detached(files.detach(reopened, prepared.identity)) == value


def test_execution_index_reuses_inline_but_generic_and_tagged_root_stay_file_backed(db):
    value = {'records': {'key': 'small index'}}
    first = files.put_execution_index(db, value)
    db.commit()
    assert files.put_execution_index(db, value) == first
    assert not (files.root(db) / (first['$resource'] + '.json')).exists()
    generic = files.put(db, {'records': {'key': 'ordinary resource'}})
    root_value = {'format': files.EXECUTION_ROOT_FORMAT, 'index': first}
    root = files.put_execution_index(db, root_value)
    assert json.loads(indexed(db, generic['$resource'])) == {'$blob': generic['$resource']}
    assert json.loads(indexed(db, root['$resource'])) == {'$blob': 'execution-root/1:' + root['$resource']}
    assert (files.root(db) / (generic['$resource'] + '.json')).is_file()
    assert (files.root(db) / (root['$resource'] + '.json')).is_file()
    assert files.read_detached(files.detach(db, root['$resource'])) == root_value


def test_execution_index_preserves_existing_large_inline_without_a_redundant_file(db):
    value = {'records': {'retained': 'x' * 8192}}
    prepared = files.prepare(value)
    assert prepared.size > 8192
    db.execute('INSERT INTO state_resources VALUES (?, ?)', (prepared.identity, prepared.text))
    db.commit()
    files.put_execution_index(db, value)
    assert indexed(db, prepared.identity) == prepared.text
    assert not files.root(db).exists()
    assert files.read_detached(files.detach(db, prepared.identity)) == value


@pytest.mark.parametrize('missing_file', [False, True])
def test_execution_index_preserves_file_pointer_and_exact_missing_file_recovery(db, missing_file):
    value = {'children': {'a': {'$resource': '1' * 64, 'bytes': 12}}}
    reference = files.put(db, value)
    db.commit()
    saved = indexed(db, reference['$resource'])
    path = files.root(db) / (reference['$resource'] + '.json')
    expected = path.read_bytes()
    if missing_file:
        path.unlink()
    assert files.put_execution_index(db, value) == reference
    assert indexed(db, reference['$resource']) == saved
    assert path.read_bytes() == expected
    assert files.read_detached(files.detach(db, reference['$resource'])) == value


@pytest.mark.parametrize('kind', ['exact', 'same-size-corrupt', 'wrong-size', 'directory', 'symlink', 'broken-symlink'])
def test_execution_index_never_hides_existing_backing_path(db, tmp_path, kind):
    value = {'records': {'key': 'owned value'}}
    prepared = files.prepare(value)
    directory = files.root(db)
    directory.mkdir()
    path = directory / (prepared.identity + '.json')
    if kind in {'exact', 'same-size-corrupt', 'wrong-size'}:
        encoded = prepared.encoded if kind == 'exact' else b'x' * (prepared.size if kind == 'same-size-corrupt' else 1)
        path.write_bytes(encoded)
    elif kind == 'directory':
        path.mkdir()
    else:
        target = tmp_path / 'other.json'
        if kind == 'symlink':
            target.write_bytes(prepared.encoded)
        path.symlink_to(target)
    if kind in {'wrong-size', 'directory', 'symlink', 'broken-symlink'}:
        with pytest.raises(ValueError, match='conflicts'):
            files.put_execution_index(db, value)
        assert indexed(db, prepared.identity) is None and not db.in_transaction
        assert path.is_symlink() if 'symlink' in kind else path.exists()
    else:
        reference = files.put_execution_index(db, value)
        assert json.loads(indexed(db, prepared.identity)) == {'$blob': prepared.identity}
        assert path.read_bytes() == encoded
        if kind == 'same-size-corrupt':
            with pytest.raises(ValueError, match='identity'):
                files.read_detached(files.detach(db, reference['$resource']))
        else:
            assert files.read_detached(files.detach(db, reference['$resource'])) == value


def test_execution_index_preserves_conflicting_index_row(db):
    value = {'records': {'key': 'new index'}}
    prepared = files.prepare(value)
    original = '{"retained":"conflicting row"}'
    db.execute('INSERT INTO state_resources VALUES (?, ?)', (prepared.identity, original))
    db.commit()
    files.put_execution_index(db, value)
    assert indexed(db, prepared.identity) == original
    assert (files.root(db) / (prepared.identity + '.json')).read_bytes() == prepared.encoded
    with pytest.raises(ValueError, match='identity'):
        files.read_detached(files.detach(db, prepared.identity))


@pytest.mark.parametrize('outer_transaction', [False, True])
def test_execution_index_baseexception_rolls_back_only_owned_savepoint(db, monkeypatch, outer_transaction):
    class Interrupted(BaseException):
        pass

    if outer_transaction:
        db.execute('BEGIN')
        db.execute('INSERT INTO state_resources VALUES (?, ?)', ('kept', '{}'))
    original = files._index_prepared

    def fail(db, prepared, value):
        original(db, prepared, value)
        assert indexed(db, prepared.identity) == prepared.text
        raise Interrupted()

    monkeypatch.setattr(files, '_index_prepared', fail)
    with pytest.raises(Interrupted):
        files.put_execution_index(db, {'records': {'uncommitted': True}})
    assert db.execute('SELECT id FROM state_resources').fetchall() == ([('kept',)] if outer_transaction else [])
    assert db.in_transaction == outer_transaction
    assert not files.root(db).exists()


def test_execution_index_owns_writer_before_path_selection_and_leaves_commit_to_caller(db, monkeypatch):
    value = {'children': {}}
    prepared = files.prepare(value)
    path = files.root(db) / (prepared.identity + '.json')
    other = sqlite3.connect(db.execute('PRAGMA database_list').fetchone()[2], timeout=0)
    original_exists, checked = Path.exists, []

    def exists(candidate):
        if candidate == path:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute('BEGIN IMMEDIATE')
            checked.append(True)
        return original_exists(candidate)

    monkeypatch.setattr(Path, 'exists', exists)
    try:
        db.execute('BEGIN')  # A caller-owned deferred transaction has no writer yet.
        files.put_execution_index(db, value)
        assert checked and db.in_transaction
        assert indexed(other, prepared.identity) is None
        db.rollback()
        assert indexed(other, prepared.identity) is None
        assert not files.root(db).exists()
    finally:
        other.close()
