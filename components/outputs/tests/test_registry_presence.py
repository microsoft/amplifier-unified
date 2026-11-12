"""Real SQLite presence reads stay bounded without decoding payloads."""
import json
import sqlite3

import pytest

from amplifier_outputs import OutputRegistry


@pytest.mark.parametrize('count', [100, 10000])
def test_existing_registry_gets_bounded_partial_index_without_decoding(count, monkeypatch):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE output_records (id TEXT PRIMARY KEY, session_id TEXT, created REAL, value TEXT)')
    db.executemany('INSERT INTO output_records VALUES (?,?,?,?)',
        ((str(i), 'selected', i, json.dumps({'kind': 'media' if i % 2 else 'file',
                                           'linked': False if i % 2 else True})) for i in range(count)))
    registry = OutputRegistry(db)
    foreign = registry.create('foreign', {'kind': 'media'}) if count < 10000 else None
    if foreign is None:
        db.execute('INSERT INTO output_records VALUES (?,?,?,?)',
                   ('foreign', 'foreign', 0, '{"kind":"media","linked":true}'))
    monkeypatch.setattr('amplifier_outputs.registry.json.loads',
                        lambda *_args, **_kwargs: pytest.fail('Presence must not decode registry payloads'))
    steps = 0
    def budget():
        nonlocal steps
        steps += 1
        return int(steps > 200)
    db.set_progress_handler(budget, 1)
    try:
        assert registry.has_linked_media('selected') is False
        assert registry.has_linked_media('foreign') is True
        assert registry.has_linked_media('missing') is False
    finally:
        db.set_progress_handler(None, 0)
        db.close()
    assert steps < 200
