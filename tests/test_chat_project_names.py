"""Windows gives the app's own chat folders a short project name; nothing else changes.

The rule runs on every platform here through its private switch. Native Windows
runs the same file; ``tests/test_windows_long_paths_off.py`` proves the lengths.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from amplifier_web import session_files
from amplifier_web.managed_chats import allocate
from amplifier_web.session_files import (
    amplifier_home, append_event, capture_dir, capture_sessions_dir, chat_project_name, project_slug, sessions_dir)


def cli_name(path):
    """The shared CLI convention, written out independently of the code under test."""
    name = str(Path(path).resolve()).replace('/', '-').replace('\\', '-').replace(':', '')
    return name if name.startswith('-') else '-' + name


def short_name(identity):
    return 'chat-' + identity.replace('-', '')[:16]


def history(project, identity, workspace, *, name='metadata.json'):
    directory = amplifier_home() / 'projects' / project / 'sessions' / identity
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({'session_id': identity, 'working_dir': str(workspace)}))
    return directory


@pytest.fixture
def windows_rule(monkeypatch):
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)


@pytest.fixture
def chat(tmp_path):
    home = tmp_path / 'app'
    home.mkdir()
    identity = str(uuid.uuid4())
    return identity, Path(allocate(home, identity, 'receipt-' + identity))


def test_the_rule_is_on_for_windows_only():
    assert session_files._SHORT_CHAT_PROJECTS is (os.name == 'nt')


def test_other_platforms_keep_the_cli_name_and_read_nothing(chat, monkeypatch):
    identity, workspace = chat
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)
    # Even an existing short folder with history changes nothing there.
    history(short_name(identity), identity, workspace)

    def refuse(*args, **kwargs):
        raise AssertionError('The CLI name needs no file access beyond resolve().')

    monkeypatch.setattr(session_files.os, 'scandir', refuse)
    monkeypatch.setattr('amplifier_web.managed_chats.metadata', refuse)
    assert project_slug(workspace) == cli_name(workspace)
    assert sessions_dir(workspace) == amplifier_home() / 'projects' / cli_name(workspace) / 'sessions'
    assert capture_sessions_dir(workspace) == sessions_dir(workspace)


def test_name_comes_from_the_chat_id_and_never_equals_a_cli_name():
    identity = '8c7973a8-38de-5423-b16c-ff0d26c3f2f1'
    for path in ('C:/Users/A/AppData/Local/Amplifier/app/chats/' + identity + '/files',
                 'D:/OTHER/CHATS/' + identity.upper() + '/FILES'):
        name = chat_project_name(path)
        assert name == 'chat-8c7973a838de5423' and len(name) == 21 and not name.startswith('-')
    for path in ('C:/work/chats/' + identity, 'C:/work/chats/' + identity + '/files/src',
                 'C:/work/chat/' + identity + '/files', 'C:/work/chats/not-a-uuid/files',
                 'C:/work/chats/' + identity.replace('-', '') + '/files'):
        assert chat_project_name(path) is None


def test_new_app_chat_gets_the_short_name_in_every_store(chat, windows_rule, tmp_path, monkeypatch):
    identity, workspace = chat
    assert project_slug(workspace) == short_name(identity)
    assert sessions_dir(workspace) == amplifier_home() / 'projects' / short_name(identity) / 'sessions'
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    assert capture_dir(workspace, identity) == (
        tmp_path / 'capture' / short_name(identity) / 'sessions' / identity / 'context-intelligence').resolve()
    from amplifier_web.shared_settings import settings_paths
    assert settings_paths(workspace, session_id=identity)['session'] == (
        amplifier_home() / 'projects' / short_name(identity) / 'sessions' / identity / 'settings.yaml')
    # The capture records name the folder that holds them.
    reference = append_event(workspace, identity, 'test:event', {'timestamp': '2026-11-14T00:00:00Z', 'event_id': 'e1'})
    directory = Path(reference['$event']).parent
    assert directory == capture_dir(workspace, identity)
    assert json.loads((directory / 'metadata.json').read_text())['workspace'] == short_name(identity)
    assert json.loads((directory / 'events.jsonl').read_text())['workspace'] == short_name(identity)


def test_folder_shape_alone_is_not_the_apps_folder(chat, windows_rule, tmp_path):
    identity, workspace = chat
    # A person's own folder of the same shape keeps the shared CLI name.
    own = tmp_path / 'mine' / 'chats' / str(uuid.uuid4()) / 'files'
    own.mkdir(parents=True)
    assert project_slug(own) == cli_name(own)
    # A copied allocation record names another folder, so it proves nothing.
    copied = tmp_path / 'copy' / 'chats' / str(uuid.uuid4()) / 'files'
    copied.mkdir(parents=True)
    (copied.parent / 'managed-chat.json').write_text((workspace.parent / 'managed-chat.json').read_text())
    assert project_slug(copied) == cli_name(copied)
    # Every other folder is the CLI name, as before.
    assert project_slug(tmp_path) == cli_name(tmp_path)
    assert project_slug(workspace.parent) == cli_name(workspace.parent)


def test_a_copy_of_a_chat_folder_never_takes_over_the_original_history(chat, windows_rule, tmp_path):
    import shutil
    identity, workspace = chat
    original = history(short_name(identity), identity, workspace)
    # The whole chat folder is copied or restored somewhere else: same chat ID,
    # same allocation record, another path. A person then opens the copy.
    copy = tmp_path / 'restored' / 'chats' / identity / 'files'
    shutil.copytree(workspace.parent, copy.parent)
    assert chat_project_name(copy) == short_name(identity)  # the shape and the ID are equal
    assert project_slug(copy) == cli_name(copy)             # the saved history names another folder
    assert sessions_dir(copy) != sessions_dir(workspace)
    # The copy's own history under its CLI name keeps its place as well.
    own = history(cli_name(copy), identity, copy)
    assert project_slug(copy) == cli_name(copy) and sessions_dir(copy) / identity == own
    # The original is unchanged, and it still resolves after its folder is deleted.
    assert project_slug(workspace) == short_name(identity)
    shutil.rmtree(workspace.parent)
    assert project_slug(workspace) == short_name(identity) and (original / 'metadata.json').is_file()
    assert project_slug(copy) == cli_name(copy)
    # An empty folder under the short name proves nothing either.
    other = str(uuid.uuid4())
    (amplifier_home() / 'projects' / short_name(other) / 'sessions').mkdir(parents=True)
    unowned = tmp_path / 'elsewhere' / 'chats' / other / 'files'
    unowned.mkdir(parents=True)
    assert project_slug(unowned) == cli_name(unowned)


def test_chat_with_history_under_the_cli_name_stays_there(chat, windows_rule):
    identity, workspace = chat
    for name in ('metadata.json', 'metadata.json.backup', 'transcript.jsonl', 'transcript.jsonl.backup',
                 'settings.yaml'):  # the chat's own saved settings also keep it in place
        directory = history(cli_name(workspace), identity, workspace, name=name)
        assert project_slug(workspace) == cli_name(workspace)
        assert sessions_dir(workspace) / identity == directory
        (directory / name).unlink()
    history(cli_name(workspace), identity, workspace)
    # An empty short folder (for example from an older reader) does not hide it.
    (amplifier_home() / 'projects' / short_name(identity) / 'sessions' / identity).mkdir(parents=True)
    assert project_slug(workspace) == cli_name(workspace)
    # Nothing was moved or renamed.
    assert (amplifier_home() / 'projects' / cli_name(workspace) / 'sessions' / identity / 'metadata.json').is_file()


def test_empty_leftover_of_a_failed_first_chat_is_not_history(chat, windows_rule):
    identity, workspace = chat
    # A first message that failed on the long path left only folders behind,
    # and the capture hook's first events (the state of both clean-room PCs).
    (amplifier_home() / 'projects' / cli_name(workspace) / 'sessions' / identity / 'unified').mkdir(parents=True)
    capture = amplifier_home() / 'projects' / cli_name(workspace) / 'sessions' / identity / 'context-intelligence'
    capture.mkdir()
    (capture / 'metadata.json').write_text('{"format": "context-intelligence"}')
    (capture / 'events.jsonl').write_text('{}\n')
    # No conversation was saved there, so this chat must work under the short name.
    assert project_slug(workspace) == short_name(identity)
    assert (capture / 'events.jsonl').is_file()  # kept where it is; it is diagnostic data, not history
    history(short_name(identity), identity, workspace)
    assert project_slug(workspace) == short_name(identity)


def test_both_names_with_history_use_the_short_name_and_keep_the_other(chat, windows_rule):
    identity, workspace = chat
    # Only after an update rollback: an older version wrote under the CLI name.
    kept = history(cli_name(workspace), identity, workspace)
    history(short_name(identity), identity, workspace)
    assert project_slug(workspace) == short_name(identity)
    assert (kept / 'metadata.json').is_file()


def test_history_stays_addressable_after_the_chat_folder_is_deleted(chat, windows_rule):
    import shutil
    identity, workspace = chat
    directory = history(short_name(identity), identity, workspace)
    shutil.rmtree(workspace.parent)  # managed deletion removes chats/<id>, record included
    assert project_slug(workspace) == short_name(identity)
    # The name itself does not depend on letter case.
    assert chat_project_name(str(workspace).upper()) == short_name(identity)
    if os.name == 'nt':
        # Windows compares paths without case: another spelling of the deleted
        # folder is the same folder, through the whole rule.
        assert project_slug(str(workspace).upper()) == short_name(identity)
    # The backup record alone is enough to identify the folder.
    (directory / 'metadata.json').rename(directory / 'metadata.json.backup')
    (directory / 'transcript.jsonl').write_text('{"role":"user","content":"saved"}\n')
    assert project_slug(workspace) == short_name(identity)
    shutil.rmtree(directory)
    assert project_slug(workspace) == cli_name(workspace)  # no saved history names this folder now


def test_an_unreadable_record_is_an_error_and_never_another_name(chat, windows_rule, monkeypatch):
    import shutil
    identity, workspace = chat
    history(short_name(identity), identity, workspace)
    shutil.rmtree(workspace.parent)
    assert project_slug(workspace) == short_name(identity)
    real_open = open

    def in_use(path, *args, **kwargs):
        if str(path).endswith('metadata.json'):
            raise PermissionError(13, 'The record is in use by another program')
        return real_open(path, *args, **kwargs)

    # A temporary refusal (a sharing violation, access denied) is not proof
    # that the record is missing. The same folder must not get the CLI name now
    # and the short name a moment later.
    with monkeypatch.context() as patch:
        patch.setattr(session_files, 'open', in_use, raising=False)
        with pytest.raises(PermissionError):
            project_slug(workspace)
    assert project_slug(workspace) == short_name(identity)
    # The same holds for the folder listing itself.
    def listing_refused(path):
        raise PermissionError(13, 'Access is denied')

    with monkeypatch.context() as patch:
        patch.setattr(session_files.os, 'scandir', listing_refused)
        with pytest.raises(PermissionError):
            project_slug(workspace)


def test_a_worker_record_identifies_a_deleted_chat_folder_only_when_it_names_it(chat, windows_rule):
    import shutil
    identity, workspace = chat
    worker = '0000000000000000-0123456789abcdef_helper'
    root = history(short_name(identity), identity, workspace)
    record = history(short_name(identity), worker, workspace) / 'metadata.json'
    shutil.rmtree(workspace.parent)
    shutil.rmtree(root)  # The root session's record is gone; a worker's record names the folder.
    assert project_slug(workspace) == short_name(identity)
    # A record that is not readable JSON is passed over, not taken as proof.
    (record.parent.parent / identity).mkdir()
    (record.parent.parent / identity / 'metadata.json').write_text('{not json')
    assert project_slug(workspace) == short_name(identity)
    # A worker that recorded another folder proves nothing about this one.
    record.write_text(json.dumps({'session_id': worker, 'working_dir': str(workspace.parent / 'elsewhere')}))
    assert project_slug(workspace) == cli_name(workspace)


def test_only_chat_shaped_folders_cost_file_access(chat, windows_rule, tmp_path, monkeypatch):
    identity, workspace = chat
    for index in range(40):
        history(short_name(identity), f'0000000000000000-{index:016x}_worker', workspace)
    scans, real_scandir = [], os.scandir

    def scandir(path):
        scans.append(str(path))
        return real_scandir(path)

    from amplifier_web import managed_chats
    records, real_metadata = [], managed_chats.metadata

    def metadata(path):
        records.append(str(path))
        return real_metadata(path)

    def no_access(*args):
        raise AssertionError('A folder a person chose is decided from the path string alone.')

    monkeypatch.setattr(session_files.os, 'scandir', scandir)
    monkeypatch.setattr(managed_chats, 'metadata', metadata)
    # The settled case, however many sessions the chat has: the allocation
    # record once and one folder listing. No session record is opened.
    monkeypatch.setattr('builtins.open', no_access)
    assert project_slug(workspace) == short_name(identity)
    assert len(scans) == 1 and len(records) == 1
    scans.clear(); records.clear()
    monkeypatch.setattr(session_files, 'amplifier_home', no_access)
    assert project_slug(tmp_path / 'project') == cli_name(tmp_path / 'project')
    assert project_slug(workspace.parent) == cli_name(workspace.parent)
    assert not scans and not records


def test_existence_checks_use_the_form_that_windows_accepts_at_any_length(monkeypatch):
    # History written while long paths were on must still be found after they
    # are turned off. Native proof: tests/test_windows_long_paths_off.py.
    assert session_files._unlimited(Path('/home/a/projects')) == str(Path('/home/a/projects')) or os.name == 'nt'
    monkeypatch.setattr(session_files, '_WINDOWS', True)
    assert session_files._unlimited('C:\\Users\\a\\shared\\projects') == '\\\\?\\C:\\Users\\a\\shared\\projects'
    assert session_files._unlimited('\\\\server\\share\\projects') == '\\\\?\\UNC\\server\\share\\projects'
    assert session_files._unlimited('\\\\?\\C:\\already') == '\\\\?\\C:\\already'
    monkeypatch.setattr(session_files, '_WINDOWS', False)
    assert session_files._unlimited('/home/a/projects') == '/home/a/projects'
    # A name longer than any folder name is confirmed absent, not an error.
    assert session_files._has_history(Path('/home/a/projects') / ('n' * 300)) is False


# Callers that compare a saved or scanned project name with project_slug(workspace).


def permission_target(session):
    from amplifier_web.chat_permissions import target
    management = SimpleNamespace(service=SimpleNamespace(_session=lambda identity: session))
    return target(management, {'sessionId': session['id']})


def test_file_access_settings_accept_old_and_new_chats(chat, windows_rule):
    identity, workspace = chat
    new = {'id': identity, 'workspace': str(workspace)}
    assert permission_target(new) is new
    assert permission_target({**new, 'nativeProject': short_name(identity)})
    # A chat that keeps its folder under the CLI name still matches its saved name.
    history(cli_name(workspace), identity, workspace)
    assert permission_target({**new, 'nativeProject': cli_name(workspace)})
    with pytest.raises(ValueError, match='does not match'):
        permission_target({**new, 'nativeProject': short_name(identity)})


def test_bundle_reference_scan_reports_a_kept_folder_and_continues(chat, windows_rule, monkeypatch):
    from amplifier_web import bundle_reference_repair as repair
    identity, workspace = chat
    monkeypatch.setattr('amplifier_web.host.config.read_config',
                        lambda *args, **kwargs: SimpleNamespace(registrations={}))
    monkeypatch.setattr('amplifier_web.host.bundle_paths.local_bundle_path', lambda *args: None)

    def status(project):
        return repair.reference_status(amplifier_home(), {
            'workspace': str(workspace), 'nativeProject': project, 'nativeIdentity': identity})

    history(cli_name(workspace), identity, workspace)
    assert status(cli_name(workspace)) == 'target-unregistered'  # passed the identity comparison
    history(short_name(identity), identity, workspace)
    assert status(short_name(identity)) == 'target-unregistered'
    # The folder kept after a rollback is reported, and the scan goes on.
    assert status(cli_name(workspace)) == 'identity-conflict'


def cleanup_scope(tmp_path, project):
    from amplifier_web.history_cleanup import scope
    app = SimpleNamespace(directory=None, state={'sessions': []}, data_dir=tmp_path / 'app')
    return scope(app, {'project': project})


def test_history_cleanup_verifies_old_and_new_folders(chat, windows_rule, tmp_path):
    import shutil
    identity, workspace = chat
    legacy = history(cli_name(workspace), identity, workspace)
    *_, paths, project, native = cleanup_scope(tmp_path, cli_name(workspace))
    assert project == cli_name(workspace) and native == {identity} and legacy in paths
    shutil.rmtree(amplifier_home() / 'projects' / cli_name(workspace))
    current = history(short_name(identity), identity, workspace)
    shutil.rmtree(workspace.parent)  # the chat was deleted; only its history remains
    *_, paths, project, native = cleanup_scope(tmp_path, short_name(identity))
    assert project == short_name(identity) and native == {identity} and current in paths


def test_managed_chat_deletion_plans_the_folder_in_use(chat, windows_rule, tmp_path):
    from amplifier_web.managed_deletion import _paths
    identity, workspace = chat
    app = SimpleNamespace(data_dir=tmp_path / 'app')
    marker = {'id': identity, 'workspace': str(workspace)}
    current = history(short_name(identity), identity, workspace)
    paths, project, native, _ = _paths(app, marker, [], {identity})
    assert project == short_name(identity) and native == {identity} and current in paths
    assert workspace.parent in paths


def test_native_history_scan_resolves_both_folders_without_stopping(chat, windows_rule):
    from amplifier_web.native_history import NativeHistory
    identity, workspace = chat

    def scan():
        snapshot = NativeHistory(amplifier_home()).scan()
        return ({row['nativeProject']: row['path'] for row in snapshot['workspaces']},
                {(row['nativeProject'], row['nativeIdentity']) for row in snapshot['sessions']})

    legacy = history(cli_name(workspace), identity, workspace)
    (legacy / 'transcript.jsonl').write_text('{"role":"user","content":"saved"}\n')
    workspaces, sessions = scan()
    assert workspaces[cli_name(workspace)] == str(workspace)
    assert (cli_name(workspace), identity) in sessions
    # After a rollback both folders exist. The scan completes; the folder in
    # use resolves to the chat, and the kept folder no longer claims its path.
    current = history(short_name(identity), identity, workspace)
    (current / 'transcript.jsonl').write_text('{"role":"user","content":"newer"}\n')
    workspaces, sessions = scan()
    assert workspaces[short_name(identity)] == str(workspace)
    assert (short_name(identity), identity) in sessions
    assert workspaces[cli_name(workspace)] is None


# A check that runs in an update stage: the process has the stage as its app
# home. The update checks new components in ``<stage>/qualification-workspace``.
# A chat with its own setup is checked there with the chat's folder.

STAGE_ID = '7260914d5698489dbbdd80d8a4c4ee40'
STAGE_NAME = 'update-7260914d5698'


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """A stage with its check folder, and the settings that the updater gives its probe."""
    folder = tmp_path / 'app' / 'updates' / 'releases' / STAGE_ID
    (folder / 'qualification-workspace').mkdir(parents=True)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(folder))
    monkeypatch.setenv('AMPLIFIER_HOME', str(folder / 'shared-config'))
    return folder


def test_update_check_folder_gets_a_short_name_in_every_store(stage, windows_rule, tmp_path, monkeypatch):
    from amplifier_web.session_files import stage_project_name
    workspace = stage / 'qualification-workspace'
    name = STAGE_NAME
    assert stage_project_name() == name and len(name) == 19
    assert project_slug(workspace) == name
    # Not a CLI name and not a chat name: it can never equal a project of a person or of a chat.
    assert not name.startswith('-') and not name.startswith('chat-')
    assert sessions_dir(workspace) == (stage / 'shared-config' / 'projects' / name / 'sessions').resolve()
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    identity = str(uuid.uuid4())
    assert capture_dir(workspace, identity) == (
        tmp_path / 'capture' / name / 'sessions' / identity / 'context-intelligence').resolve()
    # The capture records name the folder that holds them.
    reference = append_event(workspace, identity, 'test:event', {'timestamp': '2026-11-15T00:00:00Z', 'event_id': 'e1'})
    assert json.loads((Path(reference['$event']).parent / 'metadata.json').read_text())['workspace'] == name
    # The real session store of the probe creates its folder under that name.
    from amplifier_web.host.storage import SessionStore
    assert SessionStore.for_app(stage, workspace).base_dir == sessions_dir(workspace)
    assert os.listdir(stage / 'shared-config' / 'projects') == [name]


def test_two_stages_never_share_a_session_folder(stage, windows_rule, tmp_path, monkeypatch):
    def stores(folder):
        """The session folder and the capture folder of one check session, as that stage's probe computes them."""
        (folder / 'qualification-workspace').mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(folder))
        monkeypatch.setenv('AMPLIFIER_HOME', str(folder / 'shared-config'))
        workspace = folder / 'qualification-workspace'
        return project_slug(workspace), sessions_dir(workspace), capture_sessions_dir(workspace)

    # The capture folder is the installation's own, shared by every stage.
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    first = stores(stage)
    other = stores(stage.parent / ('8dcd8cf0' + 'a' * 24))
    assert (first[0], other[0]) == (STAGE_NAME, 'update-8dcd8cf0aaaa')
    assert first[1] != other[1] and first[2] != other[2]
    # The name holds the first 12 digits of the stage ID. Two stages that agree
    # in those digits (one pair in 2 to the power 48) get the same name. Each
    # stage has its own shared home, so their session folders still differ.
    # They then use one project folder below the capture folder; a session
    # there has its own folder, named by its session ID.
    twin = stores(stage.parent / (STAGE_ID[:12] + 'f' * 20))
    assert twin[0] == first[0] and twin[1] != first[1]
    assert twin[1].is_relative_to(stage.parent / (STAGE_ID[:12] + 'f' * 20) / 'shared-config')
    assert twin[2] == first[2] == (tmp_path / 'capture' / STAGE_NAME / 'sessions').resolve()


def test_a_chat_with_its_own_setup_is_checked_under_the_stage_name_whatever_its_folder_is(
        chat, stage, windows_rule, tmp_path, monkeypatch):
    """The check of a chat's own setup runs in a stage with the chat's folder as its workspace."""
    from amplifier_web.host.storage import SessionStore
    identity, app_chat = chat
    default_workspace = tmp_path / 'workspace'                 # the app's default folder for a person's chats
    chosen = tmp_path / 'Documents' / ('a folder that a person chose ' + 'x' * 120)
    for folder in (default_workspace, chosen):
        folder.mkdir(parents=True)
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    for folder in (default_workspace, chosen, app_chat, stage, stage / 'shared-config'):
        assert project_slug(folder) == STAGE_NAME
        # The length of the folder's path is not in the session path or in the capture path.
        assert sessions_dir(folder) == (stage / 'shared-config' / 'projects' / STAGE_NAME / 'sessions').resolve()
        assert capture_sessions_dir(folder) == (tmp_path / 'capture' / STAGE_NAME / 'sessions').resolve()
        assert SessionStore.for_app(stage, folder).base_dir == sessions_dir(folder)
    assert os.listdir(stage / 'shared-config' / 'projects') == [STAGE_NAME]


def test_outside_a_stage_every_folder_keeps_its_name(chat, stage, windows_rule, tmp_path, monkeypatch):
    """The app, a conversation worker and a person's own tools never run with a stage as their app home."""
    identity, app_chat = chat
    workspace = stage / 'qualification-workspace'
    chosen = tmp_path / 'a folder that a person chose'
    chosen.mkdir()
    # A folder with the layout of a stage is not the app home of this process.
    lookalike = tmp_path / 'mine' / 'updates' / 'releases' / STAGE_ID / 'qualification-workspace'
    lookalike.mkdir(parents=True)
    # The app itself, a folder beside the stages, a folder inside the stage, another folder.
    for home in (stage.parents[2], stage.parent, stage / 'shared-config', tmp_path, None):
        if home is None:
            monkeypatch.delenv('AMPLIFIER_WEB_HOME')
        else:
            monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
        monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
        for folder in (workspace, chosen, lookalike, stage):
            assert project_slug(folder) == cli_name(folder)
        assert project_slug(app_chat) == short_name(identity)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', '')
    assert project_slug(chosen) == cli_name(chosen)


def test_a_stage_as_the_app_home_with_another_shared_home_keeps_every_name(
        chat, stage, windows_rule, tmp_path, monkeypatch):
    """The stage name is only for the stage's own shared home, where no person has history."""
    from amplifier_web.session_files import stage_project_name
    identity, app_chat = chat
    chosen = tmp_path / 'a folder that a person chose'
    chosen.mkdir()
    assert stage_project_name() == STAGE_NAME                      # the settings that a check gets
    other = stage.parent / ('8dcd8cf0' + 'a' * 24)
    # A person's shared home, the stage itself, a folder inside its shared home, the shared home of another stage.
    for shared in (tmp_path / 'shared', stage, stage / 'shared-config' / 'projects', other / 'shared-config'):
        monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
        assert stage_project_name() is None
        for folder in (stage / 'qualification-workspace', chosen):
            assert project_slug(folder) == cli_name(folder)
            assert sessions_dir(folder) == (shared / 'projects' / cli_name(folder) / 'sessions').resolve()
        assert project_slug(app_chat) == short_name(identity)
    monkeypatch.setenv('AMPLIFIER_HOME', '')
    assert stage_project_name() is None
    # History that a person saved under that shared home stays where it is, under its own name.
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    saved = history(cli_name(chosen), str(uuid.uuid4()), chosen)
    assert saved.parent == sessions_dir(chosen)


def test_a_stage_needs_the_layout_and_the_id_that_the_updater_makes(tmp_path, chat, windows_rule, monkeypatch):
    identity, app_chat = chat
    for parts in (('updates', 'releases', STAGE_ID[:31]), ('updates', 'releases', STAGE_ID + '0'),
                  ('updates', 'releases', 'g' * 32), ('updates', 'release', STAGE_ID),
                  ('update', 'releases', STAGE_ID), ('releases', STAGE_ID)):
        folder = tmp_path.joinpath('app', *parts)
        (folder / 'qualification-workspace').mkdir(parents=True)
        monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(folder))
        monkeypatch.setenv('AMPLIFIER_HOME', str(folder / 'shared-config'))   # only the layout is wrong
        assert project_slug(folder / 'qualification-workspace') == cli_name(folder / 'qualification-workspace')
        assert project_slug(app_chat) == short_name(identity)
    # Letter case does not matter on Windows, and the name is always lower case.
    folder = tmp_path / 'App' / 'Updates' / 'Releases' / STAGE_ID.upper()
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(folder))
    monkeypatch.setenv('AMPLIFIER_HOME', str(folder / 'shared-config'))
    assert project_slug(tmp_path) == STAGE_NAME


def test_a_stage_that_an_earlier_version_checked_keeps_its_folder_and_the_next_check_gets_the_short_name(
        stage, windows_rule):
    # Where long paths are on, an earlier app version saved its check sessions
    # under the CLI name. The stage is still on disk when the app checks it again
    # after an app update. A check never continues a session, so nothing is read
    # from the earlier folder, and it stays as it is.
    workspace = stage / 'qualification-workspace'
    earlier = history(cli_name(workspace), str(uuid.uuid4()), workspace)
    (earlier / 'transcript.jsonl').write_text('{"role":"user","content":"earlier check"}\n')
    before = sorted(str(path) for path in (stage / 'shared-config').rglob('*'))
    assert project_slug(workspace) == STAGE_NAME
    from amplifier_web.host.storage import SessionStore
    store = SessionStore.for_app(stage, workspace)
    store.save('new-check', [{'role': 'user', 'content': 'next check'}], {'working_dir': str(workspace)})
    assert store.directory('new-check').parent == sessions_dir(workspace)
    assert sorted(os.listdir(stage / 'shared-config' / 'projects')) == sorted([STAGE_NAME, cli_name(workspace)])
    assert [path for path in before if not Path(path).exists()] == []
    assert (earlier / 'transcript.jsonl').read_text() == '{"role":"user","content":"earlier check"}\n'


def test_the_stage_name_reads_no_file(chat, stage, windows_rule, tmp_path, monkeypatch):
    identity, app_chat = chat
    chosen = tmp_path / 'a folder that a person chose'
    chosen.mkdir()

    def refuse(*args, **kwargs):
        raise AssertionError('The stage name comes from the app home setting alone.')

    monkeypatch.setattr(session_files.os, 'scandir', refuse)
    monkeypatch.setattr('amplifier_web.managed_chats.metadata', refuse)
    monkeypatch.setattr('builtins.open', refuse)
    for folder in (stage / 'qualification-workspace', chosen, app_chat):
        assert project_slug(folder) == STAGE_NAME


def test_other_platforms_keep_the_cli_name_in_a_stage(chat, stage, tmp_path, monkeypatch):
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)
    identity, app_chat = chat
    workspace = stage / 'qualification-workspace'
    for folder in (workspace, app_chat, tmp_path):
        assert project_slug(folder) == cli_name(folder)
    assert sessions_dir(workspace) == (stage / 'shared-config' / 'projects' / cli_name(workspace) / 'sessions').resolve()
