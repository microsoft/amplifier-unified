"""Persisted JSON is UTF-8, including under a legacy Windows process locale."""
import json
from pathlib import Path

import pytest

from amplifier_web import release_notes, session_projection as projection


@pytest.fixture
def legacy_locale(monkeypatch):
    def read_text(path, encoding=None, errors=None, **kwargs):
        return path.read_bytes().decode(encoding or 'cp1252', errors or 'strict')
    monkeypatch.setattr(Path, 'read_text', read_text)


@pytest.mark.parametrize('operation', ['hydrate', 'undo', 'migrate'])
def test_saved_view_json_uses_utf8(tmp_path, monkeypatch, legacy_locale, operation):
    text = '“Saved 项目” — café'
    view = {'id': 'synthetic', 'workspace': str(tmp_path),
            'messages': [{'role': 'user', 'text': text}]}
    path = tmp_path / 'view.json'
    original = json.dumps(view, ensure_ascii=False)
    path.write_bytes(original.encode('utf-8'))
    monkeypatch.setattr(projection, 'view_path', lambda *args: path)
    if operation == 'hydrate':
        state = {'sessions': [{'id': view['id'], 'workspace': str(tmp_path), '$view': 1}]}
        projection.hydrate(tmp_path, state, None)
        assert state['sessions'][0]['messages'][0]['text'] == text
    elif operation == 'undo':
        undo = []
        projection.persist(tmp_path, {'sessions': [view]}, {}, undo=undo)
        assert undo == [(path, original)]
    else:
        checkpoint = tmp_path / 'sessions' / 'synthetic' / 'checkpoint.json'
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(json.dumps({'metadata': {'working_dir': text}},
                                         ensure_ascii=False).encode('utf-8'))
        migrated = []
        class Store:
            def _migrate(self, identity):
                migrated.append(identity)
        def for_app(home, workspace):
            assert workspace == text
            return Store()
        monkeypatch.setattr(projection.SessionStore, 'for_app', for_app)
        projection.migrate(tmp_path, {})
        assert migrated == ['synthetic']
    if operation != 'undo':
        assert path.read_bytes() == original.encode('utf-8')


def test_bundled_notes_use_utf8(tmp_path, monkeypatch, legacy_locale):
    path = tmp_path / 'notes.json'
    path.write_bytes(json.dumps({'schemaVersion': 1, 'releases': [
        {'version': '1.0.0', 'title': '“Ready”', 'changes': ['项目 — café']}
    ]}, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(release_notes, 'PATH', path)
    release_notes.bundled.cache_clear()
    try:
        assert release_notes.bundled()[0]['title'] == '“Ready”'
    finally:
        release_notes.bundled.cache_clear()