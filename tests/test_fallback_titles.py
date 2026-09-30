"""Unnamed conversations get a readable fallback title from their first request."""
import json

from amplifier_web.native_history import NativeHistory
from amplifier_web.naming import fallback_title, first_user_title
from amplifier_web.session_files import project_slug


def write_session(home, workspace, identity, rows, metadata=None):
    directory = home / 'projects' / project_slug(workspace) / 'sessions' / identity
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'metadata.json').write_text(json.dumps(metadata or {'working_dir': str(workspace)}))
    (directory / 'transcript.jsonl').write_text(''.join(
        (row if isinstance(row, str) else json.dumps(row)) + '\n' for row in rows))
    return directory


def test_fallback_title_collapses_noise_and_caps_on_a_word_boundary():
    assert fallback_title('  Fix   the\n\nlogin   bug ') == 'Fix the login bug'
    assert fallback_title('## Plan the release') == 'Plan the release'
    assert fallback_title('> - @docs/README.md @src/app.py summarize these files') == 'summarize these files'
    assert fallback_title('<system-reminder>ignore me</system-reminder>\nWhat changed?') == 'What changed?'
    assert fallback_title('@only/a/mention.md') == '@only/a/mention.md'
    long = 'Use the bash tool to run git status here, then tell me in one sentence what it says'
    title = fallback_title(long)
    assert len(title) <= 64 and title.endswith('…')
    assert title == 'Use the bash tool to run git status here, then tell me in one…'
    assert fallback_title('x' * 100) == 'x' * 63 + '…'
    assert fallback_title('   \n ') is None


def test_first_user_title_skips_system_tool_ephemeral_and_service_rows(tmp_path):
    workspace = tmp_path / 'workspace'
    directory = write_session(tmp_path / 'home', workspace, 'abc', [
        'not json',
        {'role': 'system', 'content': 'You are helpful'},
        {'role': 'user', 'content': '   '},
        {'role': 'user', 'content': 'hidden', 'metadata': {'ephemeral': True}},
        {'role': 'user', 'content': 'Recovered', 'metadata': {'amplifier_input': {
            'version': 1, 'kind': 'service', 'id': 'job', 'source': 'recovery'}}},
        {'role': 'tool', 'content': 'output'},
        {'role': 'user', 'content': [{'type': 'image'}, {'type': 'text', 'text': 'Draft the Q3 plan'}]},
        {'role': 'user', 'content': 'Second message'},
    ])
    assert first_user_title(directory) == 'Draft the Q3 plan'


def test_first_user_title_reads_a_bounded_prefix(tmp_path):
    directory = write_session(tmp_path / 'home', tmp_path / 'w', 'big', [
        {'role': 'assistant', 'content': 'x' * 4096},
        {'role': 'user', 'content': 'Too late'},
    ])
    assert first_user_title(directory, limit=1024) is None
    assert first_user_title(directory) == 'Too late'
    assert first_user_title(tmp_path / 'missing') is None


def test_native_catalog_uses_first_user_message_and_stays_automatic(tmp_path):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    write_session(home, workspace, 'deadbeef-1', [{'role': 'user', 'content': 'Summarize the incident report'}])
    write_session(home, workspace, 'cafebabe-2', [{'role': 'assistant', 'content': 'hello'}])
    write_session(home, workspace, 'named-3', [{'role': 'user', 'content': 'ignored'}],
                  {'working_dir': str(workspace), 'name': 'Chosen', 'name_source': 'manual'})
    rows = {row['nativeIdentity']: row for row in NativeHistory(home).scan()['sessions']}
    assert rows['deadbeef-1']['title'] == 'Summarize the incident report'
    assert rows['deadbeef-1']['autoName'] is True
    assert rows['deadbeef-1']['titleFallback'] is True
    assert rows['cafebabe-2']['title'] == 'Conversation cafebabe'
    assert rows['cafebabe-2']['titleFallback'] is True
    assert rows['named-3']['title'] == 'Chosen'
    assert not rows['named-3'].get('titleFallback')


def test_native_catalog_caches_a_found_title_per_transcript(tmp_path, monkeypatch):
    home, workspace = tmp_path / 'home', tmp_path / 'workspace'
    workspace.mkdir()
    directory = write_session(home, workspace, 'root', [{'role': 'user', 'content': 'First ask'}])
    history = NativeHistory(home)
    assert history.scan()['sessions'][0]['title'] == 'First ask'
    calls = []
    import amplifier_web.native_history as module
    original = module.first_user_title
    monkeypatch.setattr(module, 'first_user_title', lambda *a, **k: calls.append(a) or original(*a, **k))
    with (directory / 'transcript.jsonl').open('a') as handle:
        handle.write(json.dumps({'role': 'assistant', 'content': 'Answer'}) + '\n')
    assert history.scan(force=True)['sessions'][0]['title'] == 'First ask'
    assert calls == []
