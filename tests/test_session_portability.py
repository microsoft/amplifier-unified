"""Native naming migration and the current authoritative record."""
import json
from types import SimpleNamespace

from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.service import AppService


async def test_fallback_is_visible_in_native_metadata_and_reverse_rename_survives(tmp_path):
    app = AppService(tmp_path / "web", workspace=tmp_path)
    try:
        await app.dispatch("session.create", {})
        session = app._session()
        sid = session["id"]
        session.update(title="A useful first prompt", titleSource="automatic")
        app._save()
        store = SessionStore.for_app(app.data_dir, tmp_path)
        messages = [{"role": "user", "content": "original", "metadata": {"opaque": "kept"}}]
        store.save(sid, messages, {"bundle": "anchors", "working_dir": str(tmp_path)})
        directory = store.directory(sid)
        names = SessionMetadataStore(directory)
        assert names.read()["name"] == "A useful first prompt"
        assert names.read()["name_source"] == "fallback"
        await app.dispatch("session.rename", {"id": sid, "title": "Web choice"})
        await app.dispatch("session.rename", {"id": sid, "title": "New conversation"})
        assert names.read()["name"] == "New conversation"
        assert names.read()["name_source"] == "manual"
        stale = store.load(sid)[1]
        names.set_name("CLI choice")
        store.save(sid, messages, stale)
        assert store.load(sid)[1]["name"] == "CLI choice"
        await app.history.refresh()
        assert app._session(sid)["title"] == "CLI choice"
        app._save()
        restored = AppService(app.data_dir, workspace=tmp_path)
        try:
            assert restored._session(sid)["title"] == "CLI choice"
            assert store.load(sid)[0] == messages
        finally:
            await restored.close()
    finally:
        await app.close()


def test_legacy_sidecar_cannot_override_canonical_name(tmp_path):
    from amplifier_web.naming import adopt, read
    names = SessionMetadataStore(tmp_path, create=True)
    names.set_name("New CLI name")
    (tmp_path / "naming.json").write_text(json.dumps({"name": "Stale web name", "name_source": "manual"}))
    before = (tmp_path / "metadata.json").read_bytes()
    assert read(tmp_path)["name"] == "New CLI name"
    adopt(tmp_path, {"title": "Stale view title", "titleSource": "manual"})
    assert (tmp_path / "metadata.json").read_bytes() == before


def test_existing_web_title_migration_preserves_messages_events_and_identity(tmp_path):
    from amplifier_web.naming import adopt
    from amplifier_foundation.session.history import SessionHistoryStore
    history = SessionHistoryStore(tmp_path)
    history.save([{"role": "user", "content": "keep"}], {"session_id": "original", "unknown": 42})
    event = tmp_path / "events.jsonl"
    event.write_text('{"event":"tool:post","result":"already done"}\n')
    transcript = history.transcript_path.read_bytes()
    events = event.read_bytes()
    adopt(tmp_path, {"title": "Existing web fallback", "titleSource": "automatic"})
    assert history.load_metadata()["name"] == "Existing web fallback"
    assert history.load_metadata()["session_id"] == "original"
    assert history.load_metadata()["unknown"] == 42
    assert history.transcript_path.read_bytes() == transcript and event.read_bytes() == events


async def test_installed_cli_round_trip(tmp_path, monkeypatch):
    """Run with the CLI explicitly installed in the integration environment."""
    import pytest
    pytest.importorskip("amplifier_app_cli")
    import os
    import subprocess
    import sys
    from amplifier_app_cli.session_store import SessionStore as CLIStore
    from amplifier_app_cli.shared_root_state import SharedRootSession
    from amplifier_app_cli.main import CommandProcessor
    monkeypatch.chdir(tmp_path)
    app = AppService(tmp_path / "web", workspace=tmp_path)
    try:
        await app.dispatch("session.create", {"title": "Created in web"})
        session = app._session()
        sid = session["id"]
        web = SessionStore.for_app(app.data_dir, tmp_path)
        messages = [{"role": "user", "content": "Existing request"},
                    {"role": "assistant", "content": "Already answered", "metadata": {"continuation": "opaque"}}]
        web.save(sid, messages, {"bundle": "anchors", "working_dir": str(tmp_path)})
        directory = web.directory(sid)
        events = directory / "events.jsonl"
        events.write_text('{"event":"tool:post","result":"do not repeat"}\n')
        original_events = events.read_bytes()
        listed = subprocess.run([sys.executable, "-m", "amplifier_app_cli", "session", "list", "--format", "json"],
                                env={**os.environ, 'AMPLIFIER_ALLOW_SHARED_VENV': '1'}, capture_output=True, text=True, timeout=30)
        assert listed.returncode == 0, listed.stderr
        assert "Created in web" in listed.stdout
        cli = CLIStore()
        root = SharedRootSession.acquire(sid)
        try:
            resumed, metadata = root.read(cli)
            assert resumed == messages and metadata["bundle"] == "anchors"
            processor = SimpleNamespace(session=SimpleNamespace(coordinator=SimpleNamespace(session_id=sid)))
            result = await CommandProcessor._rename_session(processor, "Renamed in the actual CLI")
            assert "Renamed in the actual CLI" in result
            root.checkpoint(cli, resumed, bundle="anchors", metadata=metadata)
        finally:
            root.release()
        assert web.load(sid)[1]["name"] == "Renamed in the actual CLI"
        await app.history.refresh()
        assert app._session(sid)["title"] == "Renamed in the actual CLI"
        assert web.load(sid)[0] == messages
        assert events.read_bytes() == original_events
    finally:
        await app.close()
