import json

import pytest

from amplifier_web import automatic_history, conversation_navigation


@pytest.mark.parametrize('reader', [automatic_history.read_transcript, conversation_navigation.query])
def test_projection_reacquires_history_when_writer_replaces_indexed_file(tmp_path, monkeypatch, reader):
    from amplifier_foundation.session.jsonl import TranscriptIndex
    path = tmp_path / 'transcript.jsonl'
    path.write_text(json.dumps({'role': 'user', 'content': 'Before'}) + '\n')
    monkeypatch.setattr(automatic_history, 'directory', lambda _: tmp_path)
    original = TranscriptIndex.project
    acquisitions = []

    def interrupted(index, *args):
        acquisitions.append(index)
        if len(acquisitions) == 1:
            replacement = tmp_path / 'replacement'
            replacement.write_text(json.dumps({'role': 'user', 'content': 'After'}) + '\n')
            replacement.replace(path)
        return original(index, *args)

    monkeypatch.setattr(TranscriptIndex, 'project', interrupted)
    session = {'id': 'chat', 'nativeProject': 'project', 'historyManaged': True, 'messages': []}
    result = reader(session)
    assert len({id(index) for index in acquisitions}) == 2
    assert acquisitions[0] is not acquisitions[1]
    if reader is automatic_history.read_transcript:
        assert result['messages'][0]['text'] == 'After'
    else:
        assert result['turns'] == conversation_navigation.query(session)['turns']
    assert session['messages'] == []


def test_retry_is_bounded_and_does_not_hide_other_read_failures():
    from amplifier_web.history_read_retry import retry_indexed_read
    for text, attempts in [('Session history changed during indexed read', 2),
                           ('The saved chat changed. Refresh it before loading earlier messages.', 1)]:
        calls = []

        @retry_indexed_read
        def read():
            calls.append(True)
            raise ValueError(text)

        with pytest.raises(ValueError, match='being saved' if attempts == 2 else 'Refresh'):
            read()
        assert len(calls) == attempts


def test_event_association_reacquires_transcript_without_reparsing_events(tmp_path, monkeypatch):
    from amplifier_web.event_log_view import EventIndex
    from amplifier_foundation.session.jsonl import TranscriptIndex
    path = tmp_path / 'transcript.jsonl'
    path.write_text(json.dumps({'role': 'user', 'content': 'Before'}) + '\n')
    original = TranscriptIndex.project
    acquired = []
    def replace(index, *args):
        acquired.append(index)
        if len(acquired) == 1:
            path.write_text(json.dumps({'role': 'user', 'content': 'After'}) + '\n')
        return original(index, *args)
    monkeypatch.setattr(TranscriptIndex, 'project', replace)
    index = EventIndex(tmp_path / 'events.jsonl', 'chat')
    assert index.associations(tmp_path) == {}
    assert len(acquired) == 2 and acquired[0] is not acquired[1]
