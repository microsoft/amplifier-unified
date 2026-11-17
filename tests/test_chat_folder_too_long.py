"""A folder that a person chose, with a path too long for Windows: one plain answer before anything is made.

Such a folder keeps the CLI project name, which is as long as its own path.
``tests/test_windows_long_paths_off.py`` proves on native Windows that the
longest folder which is accepted really works and that one character more
really fails. Here: the arithmetic, the sentence, and the way to the chat page.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

import pytest

from amplifier_web import session_files
from amplifier_web.managed_chats import allocate
from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
from amplifier_web.service import AppError, AppService
from amplifier_web.session_health import failure_details

import test_windows_path_budget as budget

IDENTITY = budget.ROOT_ID
SENTENCE = "This chat's folder has a path that is too long for Windows: "
ADVICE = 'Move the folder to a shorter path, or start a chat in another folder.'


@pytest.fixture
def long_paths_off(monkeypatch):
    monkeypatch.setattr(session_files, 'windows_refuses_long_paths', lambda: True)


@pytest.fixture
def short_homes(monkeypatch):
    """Short store folders that need not exist: the answer is arithmetic on names."""
    monkeypatch.setenv('AMPLIFIER_HOME', str(Path('/h').resolve()))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(Path('/c').resolve()))
    monkeypatch.delenv('AMPLIFIER_WEB_HOME', raising=False)
    return Path('/person').resolve()


def named(parent, size):
    """A folder path of exactly ``size`` characters. Nothing is created."""
    folder = parent / ('d' * (size - len(str(parent)) - 1))
    assert len(str(folder)) == size
    return folder


def longest_accepted(parent):
    return max(size for size in range(len(str(parent)) + 2, 256)
               if session_files.chat_folder_too_long(named(parent, size), IDENTITY) is None)


def test_the_measure_is_the_longest_name_that_keeping_a_chat_needs():
    rows = {name: relative for name, kind, relative in
            budget.session_store('p', IDENTITY) + budget.capture_store('p', IDENTITY)}
    session = rows['session folder']
    capture = rows['capture folder'].removesuffix('\\context-intelligence')

    def tail(name):
        return len(rows[name]) - len(session if rows[name].startswith('shared') else capture)

    keeps = ('transcript backup', 'pending transcript append', 'history temporary', 'metadata lock beside the folder',
             'view temporary', 'event log', 'hook metadata temporary')
    assert session_files._FILE_LIMIT == budget.FILE_LIMIT
    assert session_files._SESSION_TAIL == tail('view temporary') == 59
    assert session_files._CAPTURE_TAIL == tail('hook metadata temporary') == 50
    assert max(tail(name) for name in keeps if rows[name].startswith('shared')) == 59
    assert max(tail(name) for name in keeps if rows[name].startswith('capture')) == 50
    # Longer names that a folder inside the measure can still lose. The design document states their limits.
    not_measured = {'background-job record': 84, 'request observation': 110,
                    'request observation temporary': 86, 'oversized-request copy': 109}
    assert {name: tail(name) for name in not_measured} == not_measured
    # A new row in the budget needs a decision here: measured, or stated as a limit.
    assert set(rows) == {'session folder', 'capture folder', *keeps, *not_measured}


def test_a_folder_is_refused_exactly_when_a_file_of_the_chat_passes_the_limit(short_homes, long_paths_off, monkeypatch):
    fits = longest_accepted(short_homes)
    folder = named(short_homes, fits)
    view = session_files.sessions_dir(folder) / IDENTITY / 'unified' / ('.presentation-' + budget.HEX32 + '.tmp')
    assert len(str(view)) == budget.FILE_LIMIT and session_files.chat_folder_too_long(folder, IDENTITY) is None
    longer = named(short_homes, fits + 1)
    assert session_files.chat_folder_too_long(longer, IDENTITY) == (
        f'{SENTENCE}{longer} ({fits + 1} characters; at most {fits} fit here). {ADVICE}')
    assert session_files.chat_folder_too_long(str(longer), IDENTITY) is not None
    # A longer session ID needs a shorter folder, one character for one.
    assert session_files.chat_folder_too_long(folder, IDENTITY + 'x') is not None
    assert session_files.chat_folder_too_long(named(short_homes, fits - 1), IDENTITY + 'x') is None
    # The capture folder decides when it is the longer place.
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(Path('/' + 'c' * 40).resolve()))
    capture = session_files.capture_sessions_dir(folder) / IDENTITY / 'context-intelligence' / ('metadata.json.' + '9' * 10 + '.tmp')
    assert len(str(capture)) > budget.FILE_LIMIT and session_files.chat_folder_too_long(folder, IDENTITY) is not None
    assert longest_accepted(short_homes) < fits


def test_a_folder_with_no_room_at_all_gets_no_number(short_homes, long_paths_off, monkeypatch):
    # The person's shared home itself is so deep that no folder fits: no number would help.
    monkeypatch.setenv('AMPLIFIER_HOME', str(Path('/' + 'h' * 160).resolve()))
    folder = named(short_homes, 60)
    assert session_files.chat_folder_too_long(folder, IDENTITY) == f'{SENTENCE}{folder} (60 characters). {ADVICE}'


def test_nothing_is_refused_where_windows_accepts_the_path(short_homes, long_paths_off, tmp_path, monkeypatch):
    folder = named(short_homes, 250)
    assert session_files.chat_folder_too_long(folder, IDENTITY) is not None
    assert session_files.chat_folder_too_long(None, IDENTITY) is None
    assert session_files.chat_folder_too_long('', IDENTITY) is None
    # Not an ID or a path that this can measure: the ordinary checks answer, as before.
    assert session_files.chat_folder_too_long(folder, 'not/an/id') is None
    assert session_files.chat_folder_too_long('bad\0path', IDENTITY) is None
    # The app's own chat folder is never the subject of this answer, whatever its path is: a person cannot move
    # it. (On Windows it has a short project name. Here it has the CLI name, so the arithmetic alone would refuse.)
    deep = tmp_path / ('a' * 150) / 'app'
    deep.mkdir(parents=True)
    own = Path(allocate(deep, IDENTITY, 'receipt'))
    assert len(str(own)) > 200 and session_files.chat_folder_too_long(own, IDENTITY) is None
    assert session_files.chat_folder_too_long(own.with_name('other'), IDENTITY) is not None   # not that folder
    # A check in an update stage names every folder by the stage: the stage is the app home and its own
    # shared home is the shared home. With a person's shared home the folder is measured as always.
    stage, persons = tmp_path / 'app' / 'updates' / 'releases' / budget.HEX32, os.environ['AMPLIFIER_HOME']
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(stage))
    assert session_files.chat_folder_too_long(folder, IDENTITY) is not None
    monkeypatch.setenv('AMPLIFIER_HOME', str(stage / 'shared-config'))
    assert session_files.chat_folder_too_long(folder, IDENTITY) is None
    monkeypatch.setenv('AMPLIFIER_HOME', persons)
    monkeypatch.delenv('AMPLIFIER_WEB_HOME')
    assert session_files.chat_folder_too_long(folder, IDENTITY) is not None
    # Long paths on, another platform, or a setting that cannot be read: Windows decides, as before.
    monkeypatch.setattr(session_files, 'windows_refuses_long_paths', lambda: False)
    assert session_files.chat_folder_too_long(folder, IDENTITY) is None


@pytest.mark.skipif(sys.platform == 'win32', reason='On Windows the answer is the PC\'s own setting.')
def test_other_platforms_never_refuse(short_homes):
    assert session_files.windows_refuses_long_paths() is False
    assert session_files.chat_folder_too_long(named(short_homes, 250), IDENTITY) is None


def test_the_setting_is_read_from_windows_and_an_unreadable_one_refuses_nothing(monkeypatch):
    class Registry:
        HKEY_LOCAL_MACHINE = object()

        def __init__(self, value):
            self.value = value

        def OpenKey(self, hive, name):
            assert hive is self.HKEY_LOCAL_MACHINE and name == r'SYSTEM\CurrentControlSet\Control\FileSystem'
            return self

        def __enter__(self):
            return self

        def __exit__(self, *error):
            return False

        def QueryValueEx(self, key, name):
            assert name == 'LongPathsEnabled'
            if isinstance(self.value, BaseException):
                raise self.value
            return self.value, 4

    monkeypatch.setattr(session_files, '_WINDOWS', True)
    for value, expected in ((0, True), (1, False), (FileNotFoundError(), True), (PermissionError(), False)):
        monkeypatch.setitem(sys.modules, 'winreg', Registry(value))
        assert session_files.windows_refuses_long_paths() is expected


def test_the_trouble_notice_names_the_cause_whatever_the_folder_is_called():
    for name in ('authentication', 'rate limit', 'context window base64 invalid image_url',
                 'unauthorized tools.1 input_schema', 'app bridge response timed out'):
        text = f'{SENTENCE}C:\\Users\\me\\{name}\\x (150 characters; at most 78 fit here). {ADVICE}'
        detail = failure_details(text, 'RuntimeStartupError')
        assert detail['category'] == 'worker_startup' and detail['errorType'] == 'RuntimeStartupError'
        assert detail['summary'] == 'The path of this chat’s folder is too long for Windows.'
        assert detail['guidance'].startswith(ADVICE)
    other = failure_details('Something else', 'RuntimeStartupError')
    assert other['category'] == 'worker_startup' and other['summary'] == 'The conversation worker could not start.'


def real_folder(tmp_path):
    """An existing folder whose path is too long on every PC: 200 characters, each name inside every limit."""
    folder = tmp_path / ('d' * (199 - len(str(tmp_path))))
    folder.mkdir()
    assert len(str(folder)) == 200
    return folder


async def test_a_new_chat_in_such_a_folder_gets_the_sentence_and_nothing_is_made(tmp_path, long_paths_off, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    folder = real_folder(tmp_path)
    app = AppService(tmp_path / 'app', RuntimeManager(command=[sys.executable, '-c', 'pass'],
                                                      retention={'prewarm_on_select': False}), workspace=folder)
    try:
        before = [row['id'] for row in app.state['sessions']]
        with pytest.raises(AppError) as rejected:
            await app.dispatch('session.create', {})
        assert rejected.value.status == 409
        text = str(rejected.value)
        assert text.startswith(f'{SENTENCE}{folder} (200 characters') and text.endswith(f'{ADVICE} No chat was started.')
        assert [row['id'] for row in app.state['sessions']] == before
        assert not (session_files.amplifier_home() / 'projects').exists()
        # With room for the path the same call makes the chat, as before.
        monkeypatch.setattr(session_files, 'windows_refuses_long_paths', lambda: False)
        await app.dispatch('session.create', {})
        assert len(app.state['sessions']) == len(before) + 1
    finally:
        await app.close()


async def test_send_in_an_existing_chat_gets_the_sentence_keeps_the_message_and_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    folder = real_folder(tmp_path)
    marker = tmp_path / 'started'
    runtime = RuntimeManager(command=[sys.executable, '-c', f'open({str(marker)!r}, "w").close()'],
                             retention={'prewarm_on_select': False})
    app = AppService(tmp_path / 'app', runtime, workspace=folder)
    try:
        # The chat was made while Windows accepted the path. Then long paths are off.
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        monkeypatch.setattr(session_files, 'windows_refuses_long_paths', lambda: True)
        from amplifier_web import generation_leases
        reserved, acquire = [], generation_leases.acquire
        monkeypatch.setattr(generation_leases, 'acquire',
                            lambda *arguments: reserved.append(arguments) or acquire(*arguments))
        with pytest.raises(AppError) as rejected:
            await app.dispatch('conversation.send', {'text': 'Keep this unsent request'}, command_id='too-long')
        assert reserved == []   # the answer comes before a component generation is reserved
        text = str(rejected.value)
        assert rejected.value.code == 'worker_startup_failed' and rejected.value.receipt['delivery'] == 'failed'
        assert text.startswith(f'{SENTENCE}{folder} (200 characters')
        assert f'{ADVICE} This attempt did not send your message.' in text
        assert text.endswith('Your message has been saved.')
        session = app._session(sid)
        assert session['failure']['category'] == 'worker_startup'
        assert session['failure']['summary'] == 'The path of this chat’s folder is too long for Windows.'
        assert str(folder) in session['error']
        assert [row['text'] for row in session['messages']] == ['Keep this unsent request']
        assert not runtime.workers and not marker.exists()
    finally:
        await app.close()


async def test_the_start_asks_with_the_folder_and_the_id_of_the_chat_and_then_starts_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    seen = []
    monkeypatch.setattr(session_files, 'chat_folder_too_long', lambda *arguments: seen.append(arguments))
    runtime = RuntimeManager(command=[sys.executable, '-c', 'raise SystemExit(1)'], retention={'prewarm_on_select': False})

    async def emit(*arguments):
        return None

    with pytest.raises(Exception) as failure:
        await runtime.start({'id': str(uuid.uuid4()), 'workspace': str(tmp_path), 'runtimeSessionId': 'native-id'}, emit)
    assert 'too long for Windows' not in str(failure.value) and not isinstance(failure.value, RuntimeStartupError)
    assert seen == [(str(tmp_path), 'native-id')]
    # One rule for the ID, where a chat is created and where its worker starts.
    assert session_files.chat_files_identity({'id': 'a', 'nativeIdentity': 'b', 'runtimeSessionId': 'c'}) == 'c'
    assert session_files.chat_files_identity({'id': 'a', 'nativeIdentity': 'b'}) == 'b'
    assert session_files.chat_files_identity({'id': 'a', 'runtimeSessionId': None, 'nativeIdentity': ''}) == 'a'
    for owner in ('service.py', 'runtime.py'):
        source = (Path(session_files.__file__).with_name(owner)).read_text(encoding='utf-8')
        assert source.count("session.get('workspace'), chat_files_identity(session))") == 1, owner
    # An external agent keeps no chat files of this kind: it is not asked.
    with pytest.raises(Exception):
        await runtime.start({'id': str(uuid.uuid4()), 'workspace': str(tmp_path), 'externalAgent': {'id': 'x'}}, emit)
    assert len(seen) == 1
