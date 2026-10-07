import json
import os
from pathlib import Path

import pytest
from amplifier_web.chat_title_preview import MAX_BYTES, read, unnamed
from amplifier_web.host.storage import SessionStore
from amplifier_web.service import AppService


@pytest.fixture
def saved(tmp_path):
    session = {'id': 'preview-test', 'workspace': str(tmp_path), 'title': 'Conversation deadbeef', 'titleSource': 'native'}
    store = SessionStore.for_app(tmp_path, tmp_path)
    directory = store.directory(session['id'])
    directory.mkdir(parents=True)
    def write(rows):
        (directory / 'transcript.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return session, directory, write


def test_excerpt_skips_internal_inputs_and_preserves_history(saved, tmp_path):
    session, directory, write = saved
    write([{'role': 'system', 'content': 'private system instructions'},
           {'role': 'user', 'content': 'observation', 'metadata': {'amplifier_input': {'kind': 'service'}}},
           {'role': 'user', 'content': 'temporary', 'metadata': {'ephemeral': True}},
           {'role': 'user', 'content': [{'type': 'tool_result', 'text': 'not text'}, {'type': 'text', 'text': 'Build  an\n orbit explorer'}]}])
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert read(tmp_path, session) == {'title': 'Build an orbit explorer', 'source': 'first-message'}
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize('source', ['manual', 'generated'])
def test_named_chat_is_not_read(saved, tmp_path, source):
    session, directory, _ = saved
    (directory / 'transcript.jsonl').symlink_to('/unavailable')
    for key in ('titleSource', 'nativeNameSource'):
        chat = {**session, key: source}
        assert not unnamed(chat)
        assert read(tmp_path, chat) == {'title': session['title'], 'source': 'saved'}


def test_reads_are_bounded_and_primary_wins(saved, tmp_path):
    session, directory, write = saved
    write([{'role': 'system', 'content': 'x' * MAX_BYTES}, {'role': 'user', 'content': 'too far'}])
    (directory / 'transcript.jsonl.backup').write_text(json.dumps({'role': 'user', 'content': 'old'}))
    assert read(tmp_path, session)['source'] == 'unnamed'
    write([])
    assert read(tmp_path, session)['source'] == 'unnamed'
    (directory / 'transcript.jsonl').unlink()
    assert read(tmp_path, session)['title'] == 'old'


def test_line_limit_and_long_title(saved, tmp_path):
    session, directory, write = saved
    write([{'role': 'assistant', 'content': 'a'}] * 128 + [{'role': 'user', 'content': 'too far'}])
    assert not read(tmp_path, session)['title']
    write([{'role': 'user', 'content': 'word ' * 100}])
    assert len(read(tmp_path, session)['title']) <= 97
    assert read(tmp_path, session)['title'].endswith('…')


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_does_not_follow_symlinks_or_block_on_special_files(saved, tmp_path, kind):
    session, directory, _ = saved
    path = directory / 'transcript.jsonl'
    if kind == 'symlink':
        target = tmp_path / 'outside.jsonl'
        target.write_text(json.dumps({'role': 'user', 'content': 'outside'}))
        path.symlink_to(target)
    else:
        os.mkfifo(path)
    assert read(tmp_path, session)['source'] == 'unnamed'


async def test_action_does_not_hydrate_or_mutate_or_start_runtime(tmp_path, monkeypatch):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Conversation deadbeef'})
        session = app._session()
        session.update(titleSource='native', nativeNameSource='fallback')
        SessionStore.for_app(tmp_path, tmp_path).save(session['id'], [{'role': 'user', 'content': 'Plan the launch'}], {})
        def forbidden(*args, **kwargs):
            raise AssertionError('Preview cannot hydrate a chat')
        monkeypatch.setattr(app.cold_display, 'hydrate', forbidden)
        revision = app.state['revision']
        result = await app.dispatch('session.titlePreview', {'sessionId': session['id']})
        assert result['result'] == {'title': 'Plan the launch', 'source': 'first-message'}
        assert app.state['revision'] == revision
        assert session['title'] == 'Conversation deadbeef'
        assert 'state' not in result
    finally:
        await app.close()
