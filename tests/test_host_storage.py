import json
from pathlib import Path
import pytest

from amplifier_web.host.storage import SessionStore
from amplifier_web.host.approvals import Approvals


def test_checkpoint_roundtrip_keeps_transcript_and_removes_credentials(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    messages = [{"role": "system", "content": "regenerate"}, {"role": "user", "content": "continue"}]
    store.save("child-123", messages, {"parent_id": "root", "config": {"api_key": "do-not-save", "model": "chosen-model"}})
    rows, metadata = store.load("child-123")
    assert rows == messages[1:]
    assert metadata["parent_id"] == "root"
    assert metadata["config"] == {"model": "chosen-model"}
    assert (tmp_path / "sessions/child-123/transcript.jsonl").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "sessions/child-123/checkpoint.json").exists()
    assert not any("do-not-save" in p.read_text() for p in (tmp_path / "sessions/child-123").iterdir())


def test_import_is_read_only_once_and_keeps_job_evidence_without_lock(tmp_path):
    legacy = tmp_path / "legacy"
    source = legacy / "projects/example/sessions/previous"
    (source / "live-jobs").mkdir(parents=True)
    (source / "transcript.jsonl").write_text('{"role":"user","content":"saved"}\n')
    (source / "metadata.json").write_text('{"parent_id":"root"}')
    (source / "live-jobs/job-123.json").write_text('{"version":1,"call_id":"call-1","status":"pending"}')
    (source / "live-jobs/owner.lock").write_text('old owner')
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    store = SessionStore(tmp_path / "own", legacy_home=legacy)
    messages, metadata = store.import_cli("previous")
    assert messages[0]["content"] == "saved"
    assert metadata["legacy_import"]["jobs_replayed"] is False
    assert (store.base_dir / "previous/live-jobs/job-123.json").exists()
    assert not (store.base_dir / "previous/live-jobs/owner.lock").exists()
    store.save("previous", [{"role":"user","content":"new work"}], metadata)
    assert store.import_cli("previous")[0][0]["content"] == "new work"
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}


def test_session_paths_cannot_escape_store(tmp_path):
    store = SessionStore(tmp_path)
    for identity in ("../escape", "/absolute", "a/b", "a\\b", "..", ""):
        with pytest.raises(ValueError):
            store.save(identity, [], {})


@pytest.mark.asyncio
async def test_approval_callback_is_required_and_timeout_never_allows():
    import asyncio
    with pytest.raises(RuntimeError):
        await Approvals(None, None).request_approval("deploy?", ["allow", "deny"], 1, "allow")
    async def absent(prompt, options):
        await asyncio.sleep(1)
    assert await Approvals(None, absent).request_approval("deploy?", ["allow", "deny"], .001, "allow") == "deny"


@pytest.mark.asyncio
async def test_approval_preserves_user_decision_and_rejects_invented_choice():
    async def allow(prompt, options):
        assert prompt == "apply change?"
        assert options == ["allow", "deny"]
        return "allow"
    assert await Approvals(None, allow).request_approval("apply change?", ["allow", "deny"]) == "allow"
    async def bad(prompt, options):
        return "approve-all-forever"
    with pytest.raises(ValueError):
        await Approvals(None, bad).request_approval("apply?", ["allow", "deny"])


def test_find_prefers_native_backup_over_private_legacy_and_deduplicates_paths(tmp_path):
    from amplifier_web.session_files import sessions_dir
    root = sessions_dir(tmp_path) / 'fixture'
    root.mkdir(parents=True)
    backup = root / 'transcript.jsonl.backup'
    backup.write_text('{"role":"user","content":"native backup"}\n')
    legacy = SessionStore(tmp_path / 'app/sessions')
    legacy.directory('fixture').mkdir()
    (legacy.directory('fixture') / 'checkpoint.json').write_text(json.dumps({'version': 1,
        'messages': [{'role': 'user', 'content': 'stale private'}], 'metadata': {}}))
    rows, _ = SessionStore.find(tmp_path / 'app', 'fixture', tmp_path)
    assert rows[0]['content'] == 'native backup'
    assert not (root / 'transcript.jsonl').exists()
    (root / 'transcript.jsonl').write_bytes(backup.read_bytes())
    assert SessionStore.find(tmp_path / 'app', 'fixture', tmp_path)[0] == rows


def test_explicit_legacy_import_accepts_backup_only_source(tmp_path):
    source = tmp_path / 'legacy/sessions/fixture'
    source.mkdir(parents=True)
    backup = source / 'transcript.jsonl.backup'
    backup.write_text('{"role":"user","content":"saved backup"}\n')
    store = SessionStore(tmp_path / 'own', legacy_home=tmp_path / 'legacy')
    rows, metadata = store.import_cli('fixture')
    assert rows[0]['content'] == 'saved backup'
    assert metadata['legacy_import']['jobs_replayed'] is False
    assert not (source / 'transcript.jsonl').exists()
