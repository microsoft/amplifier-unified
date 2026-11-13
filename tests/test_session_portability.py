"""Native naming migration and the current authoritative record."""
import json
from types import SimpleNamespace

from amplifier_foundation.session.metadata import SessionMetadataStore
from amplifier_web.host.storage import SessionStore
from amplifier_web.service import AppService


async def prepare_discovered_session(app, sid, workspace):
    """Finish initial discovery before testing a subsequent external update."""
    from amplifier_web.session_files import project_slug

    # Even an explicit refresh advances only one bounded discovery slice.
    # Prepare the original record, never retry the post-CLI rename assertion.
    for attempt in range(64):
        await app.history.refresh(force=attempt == 0)
        shared = app.state['sharedHistory']
        assert not shared.get('error'), shared
        if shared.get('reconciliation', {}).get('phase') == 'complete':
            break
    else:
        raise AssertionError(f'Initial native discovery did not complete: {shared}')
    row = app.history.index._catalog.get(project_slug(workspace), sid)
    assert row is not None, shared
    assert row['name'] == app._session(sid)['title'] == 'Created in web'
    return row


async def test_round_trip_preparation_finishes_bounded_cold_discovery(tmp_path, monkeypatch):
    """A partial cold slice is not evidence that a later rename was ignored."""
    from amplifier_web.session_files import project_slug

    app = AppService(tmp_path / 'web', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Created in web'})
        sid = app._session()['id']
        web = SessionStore.for_app(app.data_dir, tmp_path)
        messages = [{'role': 'user', 'content': 'Existing request'}]
        web.save(sid, messages, {'bundle': 'anchors', 'working_dir': str(tmp_path)})
        directory = web.directory(sid)
        original = (directory / 'transcript.jsonl').read_bytes()
        # Deterministically suspend real discovery, without sleeping, replacing
        # its reader, or increasing the production reconciliation budget.
        with monkeypatch.context() as cold:
            cold.setattr(app.history.index, '_recovery_budget', 1)
            await app.history.refresh()
            assert app.state['sharedHistory']['reconciliation']['phase'] == 'running'
            assert app.history.index._catalog.get(project_slug(tmp_path), sid) is None
            await prepare_discovered_session(app, sid, tmp_path)
        SessionMetadataStore(directory).set_name('External canonical name')
        await app.history.refresh()
        assert app._session(sid)['title'] == 'External canonical name'
        assert app.history.index._catalog.get(project_slug(tmp_path), sid)['name'] == 'External canonical name'
        assert (directory / 'transcript.jsonl').read_bytes() == original
        assert web.load(sid)[0] == messages
    finally:
        await app.close()


async def test_fallback_is_visible_in_native_metadata_and_reverse_rename_survives(tmp_path):
    app = AppService(tmp_path / "web", workspace=tmp_path)
    try:
        await app.dispatch("session.create", {})
        session = app._session()
        sid = session["id"]
        session.update(title="A useful first prompt", titleSource="automatic", description="Existing description")
        app._save()
        store = SessionStore.for_app(app.data_dir, tmp_path)
        messages = [{"role": "user", "content": "original", "metadata": {"opaque": "kept"}}]
        store.save(sid, messages, {"bundle": "anchors", "working_dir": str(tmp_path)})
        directory = store.directory(sid)
        names = SessionMetadataStore(directory)
        assert names.read()["name"] == "A useful first prompt"
        assert names.read()["name_source"] == "fallback"
        assert names.read()["description"] == "Existing description"
        names.update({"description": "Changed in the CLI"})
        await app.dispatch("session.rename", {"id": sid, "title": "Web choice"})
        assert names.read()["description"] == "Changed in the CLI"
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
        await app.close()
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
        original_transcript = (directory / 'transcript.jsonl').read_bytes()
        # This bare service has no background discovery loop. Establish the
        # original native catalog row before exercising the CLI update.
        before = await prepare_discovered_session(app, sid, tmp_path)
        listed = subprocess.run([sys.executable, "-m", "amplifier_app_cli", "session", "list", "--format", "json"],
                                env={**os.environ, 'AMPLIFIER_ALLOW_SHARED_VENV': '1'}, capture_output=True, text=True, timeout=30)
        assert listed.returncode == 0, listed.stderr
        assert "Created in web" in listed.stdout
        cli = CLIStore()
        root = SharedRootSession.acquire(sid)
        try:
            resumed, metadata = root.read(cli)
            assert resumed == messages and metadata["bundle"] == "anchors"
            async def current_messages():
                return resumed
            context = SimpleNamespace(get_messages=current_messages)
            coordinator = SimpleNamespace(session_id=sid,
                get_capability=lambda name: root if name == 'cli.shared_root_state' else None,
                get=lambda name: context if name == 'context' else None)
            processor = SimpleNamespace(session=SimpleNamespace(coordinator=coordinator), bundle_name='anchors')
            result = await CommandProcessor._rename_session(processor, "Renamed in the actual CLI")
            assert "Renamed in the actual CLI" in result
            root.checkpoint(cli, resumed, bundle="anchors", metadata=metadata)
        finally:
            root.release()
        assert web.load(sid)[1]["name"] == "Renamed in the actual CLI"
        await app.history.refresh()
        after = app.history.index._catalog.get(before['nativeProject'], sid)
        witness = {'canonical': web.load(sid)[1], 'catalog': after,
                   'resident': {key: app._session(sid).get(key) for key in
                                ('title', 'nativeNameRevision', 'nativeNamePolicyRevision')},
                   'discovery': app.state['sharedHistory']}
        assert after is not None and after['name'] == "Renamed in the actual CLI", witness
        assert app._session(sid)["title"] == "Renamed in the actual CLI", witness
        assert app.directory.resolve(sid)['title'] == "Renamed in the actual CLI", witness
        assert web.load(sid)[0] == messages
        assert (directory / 'transcript.jsonl').read_bytes() == original_transcript
        assert events.read_bytes() == original_events
    finally:
        await app.close()
