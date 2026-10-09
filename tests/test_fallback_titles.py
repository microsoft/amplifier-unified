"""Unnamed conversations get a readable fallback title from their first request."""
import json

from amplifier_web.naming import fallback_title
from amplifier_web.native_history import NativeHistory
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

