"""The short chat-folder name through the real app service: chats stay where they are and still open.

``tests/test_chat_project_names.py`` checks the naming rule itself. This file
checks what a person sees when the rule meets history that already exists.
The rule is Windows only; it is switched on here to run on every platform.
"""
import json
from pathlib import Path

import pytest

from amplifier_web import session_files
from amplifier_web.service import AppService
from amplifier_web.session_files import amplifier_home
from test_live_clients import command
from test_service import Runtime


def cli_name(path):
    name = str(Path(path).resolve()).replace('/', '-').replace('\\', '-').replace(':', '')
    return name if name.startswith('-') else '-' + name


def short_name(identity):
    return 'chat-' + identity.replace('-', '')[:16]


def saved_history(project, identity, workspace):
    directory = amplifier_home() / 'projects' / project / 'sessions' / identity
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'metadata.json').write_text(json.dumps(
        {'session_id': identity, 'working_dir': str(workspace), 'bundle': 'work', 'turn_count': 1}))
    (directory / 'transcript.jsonl').write_text('{"role":"user","content":"saved"}\n')
    return directory


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    service.clients.attach('web', kind='web')
    yield service
    if not service.closed:
        await service.close()


async def new_chat(app):
    result = await command(app, 'web', 'session.create', {'location': {'kind': 'managed'}})
    row = app._session(result['sessionId'])
    return row['id'], Path(row['workspace'])


async def refresh(app):
    for _ in range(6):  # Discovery is bounded per call.
        await app.history.refresh()


async def opens(app, identity):
    for action in ('session.select', 'session.inspect', 'session.history'):
        await command(app, 'web', action, {'id': identity})
    return app._session(identity)


def directory_rows(app, identity):
    return {row['nativeProject']: row for row in app.directory.select('native_id=?', (identity,), limit=-1)}


async def test_new_app_chat_saves_under_the_short_name(app):
    identity, workspace = await new_chat(app)
    projects = amplifier_home() / 'projects'
    # Creating the chat already saves its view. Nothing is written under the CLI name.
    assert (projects / short_name(identity) / 'sessions' / identity / 'unified' / 'view.json').is_file()
    assert not (projects / cli_name(workspace)).exists()
    saved_history(short_name(identity), identity, workspace)
    await refresh(app)
    assert (await opens(app, identity))['nativeProject'] == short_name(identity)


async def test_chat_with_history_under_the_cli_name_opens_where_it_is(app, monkeypatch):
    # The previous version created this chat and saved it under the CLI name.
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)
    identity, workspace = await new_chat(app)
    kept = saved_history(cli_name(workspace), identity, workspace)
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    await refresh(app)
    assert (await opens(app, identity))['nativeProject'] == cli_name(workspace)
    rows = directory_rows(app, identity)
    assert set(rows) == {cli_name(workspace)} and rows[cli_name(workspace)]['workspace'] == str(workspace)
    assert (kept / 'transcript.jsonl').is_file()
    assert not (amplifier_home() / 'projects' / short_name(identity)).exists()  # nothing was moved or created


async def test_both_names_with_history_still_open_the_chat_and_keep_the_other_readable(app):
    # Only after an update rollback: an older version saved the same chat ID under the CLI name.
    identity, workspace = await new_chat(app)
    saved_history(short_name(identity), identity, workspace)
    kept = saved_history(cli_name(workspace), identity, workspace)
    await refresh(app)
    # One chat ID in two project folders is not an ambiguity for the person.
    assert (await opens(app, identity))['nativeProject'] == short_name(identity)
    rows = directory_rows(app, identity)
    assert set(rows) == {short_name(identity), cli_name(workspace)}
    assert rows[short_name(identity)]['workspace'] == str(workspace)
    # The kept folder is not hidden and not used: it is listed read-only, without a folder.
    assert rows[cli_name(workspace)].get('workspace') is None
    assert rows[cli_name(workspace)]['historyReadOnlyReason']
    assert (kept / 'transcript.jsonl').read_text() == '{"role":"user","content":"saved"}\n'
