from amplifier_web.shared_state_probe import text_content


def test_shared_state_probe_only_projects_displayable_user_and_assistant_text():
    assert text_content({"content": "plain"}) == "plain"
    assert text_content({"content": [
        {"type": "text", "text": "visible"},
        {"type": "tool_use", "input": {"private": "value"}},
        {"type": "output_text", "text": "also visible"},
    ]}) == "visible\nalso visible"
    assert text_content({"content": {"private": "value"}}) == ""

def test_native_history_precedes_legacy_checkpoint_without_modifying_files(tmp_path):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.shared_state_probe import query
    root = sessions_dir(tmp_path) / 'fixture'
    root.mkdir(parents=True)
    (root / 'transcript.jsonl').write_text('{"role":"user","content":"native saved turn"}\n')
    (root / 'metadata.json').write_text('{"bundle":"anchors","name":"Native fixture"}')
    shared = SharedSessionStore(tmp_path, 'fixture', root=tmp_path / 'legacy-locks')
    # Native browsing never creates a common checkpoint or rewrites history.
    before = {path: path.read_bytes() for path in root.iterdir()}
    result = query({'version': 1, 'op': 'open', 'workspace': str(tmp_path), 'sessionId': 'fixture'})
    assert result['messages'] == [{'role': 'user', 'text': 'native saved turn'}]
    assert result['bundle'] == 'anchors' and result['name'] == 'Native fixture'
    assert before == {path: path.read_bytes() for path in root.iterdir()}
    assert shared.read() is None


def test_backup_only_history_can_be_browsed_without_restoration(tmp_path):
    from amplifier_web.session_files import sessions_dir
    from amplifier_web.shared_state_probe import query
    root = sessions_dir(tmp_path) / 'fixture'
    root.mkdir(parents=True)
    (root / 'transcript.jsonl.backup').write_text('{"role":"user","content":"backup turn"}\n')
    result = query({'version': 1, 'op': 'view', 'workspace': str(tmp_path), 'sessionId': 'fixture'})
    assert result['messages'][0]['text'] == 'backup turn'
    assert not (root / 'transcript.jsonl').exists()
