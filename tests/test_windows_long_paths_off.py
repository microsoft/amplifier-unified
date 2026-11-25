"""Native Windows with long paths off: the limit is real and the real stores fit under it.

Long paths are off on a default Windows PC. The CI job (``.github/workflows/
windows-path-length.yml``) turns ``LongPathsEnabled`` off on its own runner,
starts this file in a new process and sets ``AMPLIFIER_REQUIRE_LONG_PATHS_OFF``.
Then a missing limit fails; it never skips. On another Windows PC the file skips
when long paths are on. It changes no setting itself.

The installation folder here has exactly the longest path that setup supports,
with the same layout as a consumer installation. Arithmetic for every path
shape is in ``tests/test_windows_path_budget.py``; this file checks that the
paths the stores really create have those lengths, and that Windows accepts them.
It also runs the app's real update compatibility probe in a stage folder there
(``tests/update_stage_probe.py``).
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

import pytest

import test_windows_path_budget as budget
import update_stage_probe

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows path limits need native Windows')
REQUIRED = os.environ.get('AMPLIFIER_REQUIRE_LONG_PATHS_OFF') == '1'
# A worker (sub-agent) session ID: <16 hex>-<16 hex>_<agent name>.
WORKER_PREFIX = '0000000000000000-0123456789abcdef_'


def unavailable(reason):
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope='module')
def base():
    """An existing short folder owned by this account, like a profile's AppData."""
    path = Path(tempfile.gettempdir()).resolve(strict=True)
    if len(str(path)) > budget.MAX_ROOT - 2:
        unavailable(f'The temporary folder path has {len(str(path))} characters; no room for a test folder.')
    return path


def length_refusal(error):
    """How Windows refuses a path over its limit when every parent folder exists.

    The Win32 calls answer path-not-found (3) or name-too-long (206). Python's
    own ``open`` reports the same refusal as "no such file" without a Windows code.
    """
    return error.winerror in (3, 206) or (error.winerror is None and error.errno == errno.ENOENT)


EXTENDED = '\\\\?\\'  # The path form that Windows accepts at any length, valid with long paths off.


def remove_tree(path):
    """Remove a tree by extended-length paths; only for folders this file created."""
    with os.scandir(path) as entries:
        for entry in list(entries):
            if entry.is_dir(follow_symlinks=False):
                remove_tree(entry.path)
            else:
                os.chmod(entry.path, stat.S_IWRITE)  # The source store seals its files read-only.
                os.remove(entry.path)
    os.chmod(path, stat.S_IWRITE)
    os.rmdir(path)


def refused_for_length(action):
    with pytest.raises(OSError) as error:
        action()
    return length_refusal(error.value)


@pytest.fixture(scope='module')
def long_paths_off(base):
    """Prove the limit before anything depends on it: same folder, one character apart."""
    parent = base / ('c' * (200 - len(str(base)) - 1))
    parent.mkdir()
    try:
        assert len(str(parent)) == 200
        fits = parent / ('f' * (budget.FILE_LIMIT - 201))
        over = parent / ('f' * (budget.FILE_LIMIT - 200))
        assert len(str(fits)) == 259 and len(str(over)) == 260
        fits.write_text('fits')  # The folder exists and is writable.
        try:
            over.write_text('over')
        except OSError as error:
            assert length_refusal(error), error
        else:
            unavailable('Windows accepted a 260-character path: long paths are on for this process.')
        # Folders: 247 characters are created, 248 are refused.
        (parent / ('d' * (budget.FOLDER_LIMIT - 201))).mkdir()
        assert refused_for_length((parent / ('d' * (budget.FOLDER_LIMIT - 200))).mkdir)
    finally:
        shutil.rmtree(parent)
    return True


class Installation:
    """Private folders laid out like a consumer installation, at an exact path length."""

    def __init__(self, base, size, monkeypatch, store=budget.SOURCE_FOLDER, letter='r'):
        from amplifier_web.windows_files import directory
        if size - len(str(base)) < 2:
            unavailable(f'The temporary folder path is too long for an installation folder of {size} characters.')
        self.root = base / (letter * (size - len(str(base)) - 1))
        assert len(str(self.root)) == size and not self.root.exists()
        for folder in ('', 'app', 'shared', 'capture', 'ownership', store):
            with directory(self.root / folder, owned_root=self.root, create=True):
                pass
        for setting, folder in (('AMPLIFIER_WEB_HOME', 'app'), ('AMPLIFIER_HOME', 'shared'),
                                ('AMPLIFIER_SESSION_STATE_HOME', 'ownership'),
                                ('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', 'capture'),
                                ('AMPLIFIER_SOURCE_STORE', store)):
            monkeypatch.setenv(setting, str(self.root / folder))

    def chat(self):
        from amplifier_web.managed_chats import allocate
        identity = str(uuid.uuid4())
        return identity, Path(allocate(self.root / 'app', identity, 'receipt-' + identity))

    def below(self, path):
        return str(Path(path).relative_to(self.root))

    def longest(self):
        """The longest file and folder paths that now exist. Ordinary paths reach all of them."""
        files, folders = [len(str(self.root))], [len(str(self.root))]
        for directory, names, leaves in os.walk(self.root):
            folders += [len(os.path.join(directory, name)) for name in names]
            files += [len(os.path.join(directory, name)) for name in leaves]
        return max(files), max(folders)


@pytest.fixture
def installation(base, long_paths_off, monkeypatch):
    created = []

    def create(size=budget.MAX_ROOT, store=budget.SOURCE_FOLDER):
        # One test may make several installations of the same path length: each gets its own name.
        created.append(Installation(base, size, monkeypatch, store, 'rqponm'[len(created)]))
        return created[-1]

    yield create
    for item in created:
        remove_tree(EXTENDED + str(item.root))  # Also removes what a test wrote over the limit.
        assert not item.root.exists()


def save_session(home, workspace, identity):
    """The app's real session store: Foundation history through Unified's SessionStore."""
    from amplifier_web.host.storage import SessionStore
    store = SessionStore.for_app(home, workspace)
    for text in ('first', 'second'):  # The second save also writes the backup files.
        store.save(identity, [{'role': 'user', 'content': text}], {'working_dir': str(workspace)})
    messages, metadata = store.load(identity)
    assert [row['content'] for row in messages] == ['second'] and metadata['working_dir'] == str(workspace)
    return store.directory(identity)


def write_view(session):
    """The compatible view beside the transcript: the write that failed in release 0.94.1."""
    from amplifier_web.presentation_files import atomic_write
    view = session / 'unified' / 'view.json'
    view.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(view, '{"messages": []}')
    assert json.loads(view.read_text(encoding='utf-8')) == {'messages': []}
    return view


def write_job_record(session):
    """A background-job record name, through the app's own atomic writer."""
    from amplifier_web.host.storage import SessionStore
    record = session / 'live-jobs' / ('job-' + budget.HEX64 + '.json')
    record.parent.mkdir(parents=True, exist_ok=True)
    SessionStore._atomic(record, '{"version": 1}')
    assert record.is_file()
    return record


def capture_events(workspace, identity):
    from amplifier_web.session_files import append_event, capture_dir
    reference = append_event(workspace, identity, 'test:event',
                             {'timestamp': '2026-11-14T00:00:00Z', 'event_id': 'event-1'})
    capture = capture_dir(workspace, identity)
    assert Path(reference['$event']) == capture / 'events.jsonl'
    saved = json.loads((capture / 'events.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert saved['data']['event_id'] == 'event-1' and saved['workspace'] == capture.parents[2].name
    assert json.loads((capture / 'metadata.json').read_text(encoding='utf-8'))['session_id'] == identity
    return capture


def save_oversized_request(capture):
    from amplifier_web import provider_payloads
    saved = provider_payloads.save(capture, {'messages': [{'role': 'user', 'content': 'x' * 64}]})
    return capture / provider_payloads.DIRECTORY / (saved['sha256'] + '.json')


def test_windows_refuses_a_260_character_path_with_long_paths_off(long_paths_off):
    assert long_paths_off


def test_app_chat_stores_work_under_the_longest_supported_installation_folder(installation):
    from amplifier_web.session_files import project_slug
    install = installation()
    assert len(str(install.root)) == budget.MAX_ROOT
    identity, workspace = install.chat()
    project = 'chat-' + identity.replace('-', '')[:16]
    assert project_slug(workspace) == project and len(project) == len(budget.CHAT_PROJECT)

    def expected(rows, name, identity):
        return len(next(relative for row, _, relative in rows(identity) if row == name))

    sessions = lambda value: budget.session_store(project, value)  # noqa: E731
    captures = lambda value: budget.capture_store(project, value)  # noqa: E731
    worker = WORKER_PREFIX + 'w' * (budget.WORKER_ID - len(WORKER_PREFIX))
    assert len(identity) == 36 and len(worker) == budget.WORKER_ID == 50
    for session_id in (identity, worker):
        session = save_session(install.root / 'app', workspace, session_id)
        assert len(install.below(session)) == expected(sessions, 'session folder', session_id)
        assert (session / 'transcript.jsonl.backup').is_file()
        view = write_view(session)
        assert len(install.below(view.with_name('.presentation-' + budget.HEX32 + '.tmp'))) == \
            expected(sessions, 'view temporary', session_id)
        assert len(install.below(write_job_record(session))) == expected(sessions, 'background-job record', session_id)
        capture = capture_events(workspace, session_id)
        assert len(install.below(capture)) == expected(captures, 'capture folder', session_id)
        assert len(install.below(save_oversized_request(capture))) == \
            expected(captures, 'oversized-request copy', session_id)
    # The longest path the budget allows for a worker session really is created.
    observed = capture / 'context-inspection' / (budget.HEX64 + '.json')
    observed.parent.mkdir()
    observed.write_text('{}')
    assert len(str(observed)) == budget.FILE_LIMIT
    files, folders = install.longest()
    assert files == budget.FILE_LIMIT and folders <= budget.FOLDER_LIMIT


def test_root_session_ownership_reads_no_transfer_fence_under_the_short_name(installation):
    from amplifier_foundation.session.shared_state import SharedSessionStore
    install = installation()
    identity, workspace = install.chat()
    save_session(install.root / 'app', workspace, identity)
    store = SharedSessionStore(workspace, identity)
    # Foundation computes the CLI name for its transfer fence. On Windows it
    # only reads there, finds no folder and reports no fence.
    assert store.transfer_fence() is None
    held = store.acquire(app='path-length-test')
    try:
        held.check()
        lock = install.root / 'ownership' / 'v1'
        assert [len(path.name) for path in lock.iterdir()] == [64]
        assert (next(lock.iterdir()) / identity / 'session.lock').is_file()
    finally:
        held.release()
    assert os.listdir(install.root / 'shared' / 'projects') == ['chat-' + identity.replace('-', '')[:16]]
    files, folders = install.longest()
    assert files <= budget.FILE_LIMIT and folders <= budget.FOLDER_LIMIT


def test_documented_limit_a_longer_worker_id_loses_only_the_oversized_request_copy(installation):
    install = installation()
    identity, workspace = install.chat()
    limit = budget.LONGEST_SESSION_ID['oversized-request copy']
    for size, saved in ((limit, True), (limit + 1, False)):
        worker = WORKER_PREFIX + 'w' * (size - len(WORKER_PREFIX))
        capture = capture_events(workspace, worker)
        if saved:
            assert len(str(save_oversized_request(capture))) == budget.FILE_LIMIT
        else:
            # provider_recording records "unavailable" for this error and the turn continues.
            assert refused_for_length(lambda: save_oversized_request(capture))
            assert not list((capture / 'provider-payloads').iterdir())  # no temporary left behind
        # The session itself, its view and its job records still work.
        session = save_session(install.root / 'app', workspace, worker)
        write_view(session)
        write_job_record(session)


def test_the_cli_name_fails_in_the_same_folder_the_failure_this_fix_removes(installation, monkeypatch):
    from amplifier_web import session_files
    install = installation()
    identity, workspace = install.chat()
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)  # the behavior before this change
    project = session_files.project_slug(workspace)
    assert project.startswith('-') and len(project) == len(
        budget.cli_project(budget.MAX_ROOT, f'app\\chats\\{identity}\\files'))
    view = session_files.sessions_dir(workspace) / identity / 'unified' / 'view.json'
    view.parent.mkdir(parents=True)  # The folders fit, as in the clean room.
    assert len(str(view)) <= budget.FILE_LIMIT < len(str(view.with_name('.presentation-' + budget.HEX32 + '.tmp')))
    from amplifier_web.presentation_files import atomic_write
    assert refused_for_length(lambda: atomic_write(view, '{}'))  # the first-chat 500 of release 0.94.1
    # That leftover has no transcript or metadata, so the chat now uses the short name and works.
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    assert session_files.project_slug(workspace) == 'chat-' + identity.replace('-', '')[:16]
    write_view(save_session(install.root / 'app', workspace, identity))


def test_chat_with_history_under_the_cli_name_keeps_working_where_it_is(installation, monkeypatch):
    from amplifier_web import session_files
    # A user name of up to 8 characters: the one case where the old name fits with long paths off.
    install = installation(41)
    identity, workspace = install.chat()
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)  # written by the previous version
    project = session_files.project_slug(workspace)
    assert project.startswith('-') and len(project) == len(budget.cli_project(41, f'app\\chats\\{identity}\\files'))
    before = save_session(install.root / 'app', workspace, identity)
    write_view(before)
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    assert session_files.project_slug(workspace) == project  # it stays; nothing is moved
    after = save_session(install.root / 'app', workspace, identity)
    assert after == before and before.parent.parent.name == project
    write_view(after)
    assert capture_events(workspace, identity).parents[2].name == project
    assert not (install.root / 'shared' / 'projects' / ('chat-' + identity.replace('-', '')[:16])).exists()


def test_history_longer_than_the_limit_is_found_and_the_chat_stays_under_its_name(installation):
    from amplifier_web import session_files
    # An installation from an earlier setup file: folder path of 92 characters.
    # Its chat was saved while long paths were on. They are off now.
    install = installation(92)
    identity, workspace = install.chat()
    project = '-' + str(workspace).replace('\\', '-').replace(':', '')
    session = install.root / 'shared' / 'projects' / project / 'sessions' / identity
    assert len(str(session)) == 300 > budget.FOLDER_LIMIT
    os.makedirs(EXTENDED + str(session))
    for name in ('metadata.json', 'transcript.jsonl'):
        with open(EXTENDED + str(session / name), 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'session_id': identity, 'working_dir': str(workspace)}) + '\n')
    # Ordinary paths cannot see it: an ordinary check reads this history as absent.
    assert not (session / 'metadata.json').exists()
    assert refused_for_length(lambda: os.scandir(str(session.parent)))
    # The name rule sees it. The chat stays where it is: its history is not
    # split into a second folder, and nothing is created under the short name.
    assert session_files._has_history(session.parents[1])
    assert session_files.project_slug(workspace) == project
    assert session_files.sessions_dir(workspace) / identity == session
    assert os.listdir(install.root / 'shared' / 'projects') == [project]
    # A new chat in the same installation gets the short name and works.
    other, folder = install.chat()
    assert session_files.project_slug(folder) == 'chat-' + other.replace('-', '')[:16]
    write_view(save_session(install.root / 'app', folder, other))


# Update stage. The compatibility probe of an in-app update runs in
# ``<installation folder>\app\updates\releases\<32 hex>`` with that stage as its
# app home. 51 is the installation folder path of the clean-room PC on which the
# probe failed (account name of 18 characters); the other is the longest that
# setup supports.
CLEAN_ROOM_ROOT = 51
PROJECT = 'update-' + update_stage_probe.STAGE_ID[:12]


@pytest.fixture
def stage(base, installation):
    """A stage with the updater's layout, in an installation of the given folder path length."""
    material = base / ('u' + uuid.uuid4().hex[:7])   # Test material stays outside the installation.
    material.mkdir()

    def create(size):
        install = installation(size)
        made = update_stage_probe.build(install.root / 'app', material)
        assert install.below(made.stage) == budget.STAGE.replace(budget.HEX32, update_stage_probe.STAGE_ID)
        assert len(str(made.stage)) == budget.length(budget.STAGE, size)
        return install, made

    yield create
    remove_tree(EXTENDED + str(material))


def run_probe(install, made, **options):
    """The app's real probe, with the folders that the installation gives its update."""
    return update_stage_probe.run(made, capture_root=install.root / 'capture',
                                  source_store=install.root / budget.SOURCE_FOLDER,
                                  state_home=install.root / 'ownership', **options)


@pytest.mark.parametrize('size', [CLEAN_ROOM_ROOT, budget.MAX_ROOT])
def test_the_update_probe_fails_under_the_cli_name_the_failure_this_fix_removes(stage, monkeypatch, size):
    from amplifier_web import session_files
    from amplifier_web.host.storage import SessionStore
    install, made = stage(size)
    # The settings that the updater gives its probe.
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(made.stage))
    monkeypatch.setenv('AMPLIFIER_HOME', str(made.shared))
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', False)   # the behavior before this change
    sessions = session_files.sessions_dir(made.workspace)
    cli = sessions.parent.name
    assert cli == update_stage_probe.cli_name(made.workspace)
    assert len(cli) == len(budget.cli_project(size, budget.CHECK_FOLDER)) == size + 78
    assert (len(str(sessions.parent)), len(str(sessions))) == (2 * size + 156, 2 * size + 165)
    if size == CLEAN_ROOM_ROOT:
        assert (len(cli), len(str(sessions.parent)), len(str(sessions))) == (129, 258, 267)   # computed there
    assert len(str(sessions.parent)) > budget.FOLDER_LIMIT
    # The call that failed on the clean-room PC: the session store creates its folder.
    with pytest.raises(FileNotFoundError) as refusal:
        SessionStore.for_app(made.stage, made.workspace)
    assert refusal.value.errno == errno.ENOENT and length_refusal(refusal.value)
    print(f'MEASURED installation folder {size}: stage {len(str(made.stage))}, CLI project name {len(cli)}, '
          f'project folder {len(str(sessions.parent))} (limit {budget.FOLDER_LIMIT}), sessions folder '
          f'{len(str(sessions))}; refused with {type(refusal.value).__name__}, errno {refusal.value.errno}, '
          f'Windows error {refusal.value.winerror}')
    # The picture measured there: the projects folder exists and is empty.
    assert os.listdir(made.shared / 'projects') == []
    # The real probe, with those names: the record that the Updates page showed.
    result = run_probe(install, made, before_the_fix=True)
    assert result.returncode == 1 and result.facts, result.stdout + result.stderr
    assert {name: result.facts.get(name) for name in ('ok', 'stage', 'errorType', 'osError')} == {
        'ok': False, 'stage': 'prepare', 'errorType': 'FileNotFoundError', 'osError': 'ENOENT'}, result.facts
    assert result.facts['failureLocation'].startswith('amplifier_web.host.storage:__init__:'), result.facts
    assert os.listdir(made.shared / 'projects') == []
    print(f'MEASURED installation folder {size}: the real probe under the CLI name ended with exit code '
          f'{result.returncode} and {json.dumps(result.facts, sort_keys=True)}')
    # With the short name the same call creates the folder.
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    store = SessionStore.for_app(made.stage, made.workspace)
    assert store.base_dir == made.shared / 'projects' / PROJECT / 'sessions' and store.base_dir.is_dir()
    assert os.listdir(made.shared / 'projects') == [PROJECT]


@pytest.mark.parametrize('size', [CLEAN_ROOM_ROOT, budget.MAX_ROOT])
def test_the_real_update_probe_completes_in_a_stage_with_long_paths_off(stage, monkeypatch, size):
    install, made = stage(size)
    update_stage_probe.passed(run_probe(install, made))
    assert update_stage_probe.expected_project(made) == PROJECT
    assert os.listdir(made.shared / 'projects') == [PROJECT]
    session = update_stage_probe.session_of(made, PROJECT)
    # The complete list of what the probe left with the fixture's profile: in its stage and below the capture folder.
    assert update_stage_probe.left_in_stage(made, PROJECT, session) == update_stage_probe.STAGE_AFTER_A_PROBE
    assert update_stage_probe.names(install.root / 'capture', PROJECT, session) == \
        update_stage_probe.CAPTURE_AFTER_A_PROBE
    # Nothing else in the installation changed: the person's shared home, the ownership store, the source store.
    for folder in ('shared', 'ownership', budget.SOURCE_FOLDER):
        assert os.listdir(install.root / folder) == []
    assert os.listdir(install.root / 'app') == ['updates']
    # The lengths are the budget's rows, and every path that exists is inside the Windows limits.
    rows = {name: budget.length(relative, size) for name, kind, relative in budget.update_stage(PROJECT, session)}
    folder = made.shared / 'projects' / PROJECT / 'sessions' / session
    assert len(str(folder)) == rows['probe session folder']
    assert len(str(folder / 'live-jobs' / 'owner.lock')) == rows['probe job-store lock']
    assert len(str(made.stage / 'runtime-reports' / session / 'module-load-failures.json')) == rows['probe report']
    capture = install.root / 'capture' / PROJECT / 'sessions' / session / 'context-intelligence'
    assert (len(str(capture)), len(str(capture / 'events.jsonl'))) == (
        rows['probe capture folder'], rows['probe event log'])
    files, folders = install.longest()
    longest = budget.STAGE_LONGEST[1] - (budget.MAX_ROOT - size)
    assert files <= longest <= budget.FILE_LIMIT and folders <= budget.FOLDER_LIMIT
    if size == budget.MAX_ROOT:
        assert (len(str(folder)), len(str(capture))) == (202, 154)
    print(f'MEASURED installation folder {size}: the real probe completed under the project name {PROJECT}; '
          f'session folder {len(str(folder))}, capture folder {len(str(capture))}, longest file that exists '
          f'{files}, longest folder that exists {folders}; longest path that the budget allows there {longest}')
    # The same stores at the same depth, through their real writers: a second
    # save also writes the backup files and goes through every temporary name.
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(made.stage))
    monkeypatch.setenv('AMPLIFIER_HOME', str(made.shared))
    other = str(uuid.uuid4())
    saved = save_session(made.stage, made.workspace, other)
    assert saved == folder.with_name(other) and (saved / 'transcript.jsonl.backup').is_file()
    assert len(str(saved / 'transcript.jsonl.backup')) == rows['probe transcript backup']
    assert capture_events(made.workspace, other) == capture.parents[1] / other / 'context-intelligence'
    assert os.listdir(made.shared / 'projects') == [PROJECT]
    files, folders = install.longest()
    assert files <= longest and folders <= budget.FOLDER_LIMIT


def test_a_persons_folder_is_refused_exactly_where_windows_refuses_a_file_of_the_chat(installation, base):
    """The app's answer for a folder that a person chose, against what Windows really does, one character apart.

    Such a folder keeps the CLI project name, which is as long as its path. The
    app answers with a plain sentence when a file that keeping the chat needs
    would pass the limit (``session_files.chat_folder_too_long``).
    """
    from amplifier_web import session_files
    install = installation()
    assert session_files.windows_refuses_long_paths() is True   # this PC's own setting, read from Windows
    identity = str(uuid.uuid4())
    parent = base / ('p' + uuid.uuid4().hex[:7])
    parent.mkdir()

    def named(size):
        return parent / ('d' * (size - len(str(parent)) - 1))

    try:
        fits = max(size for size in range(len(str(parent)) + 2, 200)
                   if session_files.chat_folder_too_long(named(size), identity) is None)
        # Installation folder path + \shared\projects\<name>\sessions (26 + the folder's own path length)
        # + the session ID (37) + the temporary file of the saved view (59) = 259.
        assert fits == budget.FILE_LIMIT - budget.MAX_ROOT - 26 - 37 - 59 == 78
        # The longest folder that the app accepts: every file that keeping a chat needs is really written.
        folder = named(fits)
        folder.mkdir()
        session = save_session(install.root / 'app', folder, identity)
        view = write_view(session)
        assert len(str(view.with_name('.presentation-' + budget.HEX32 + '.tmp'))) == budget.FILE_LIMIT
        capture = capture_events(folder, identity)
        assert session.parent.parent.name == capture.parents[2].name == update_stage_probe.cli_name(folder)
        # One character more: the app answers, and Windows refuses the write that the answer is about.
        longer = named(fits + 1)
        longer.mkdir()
        assert session_files.chat_folder_too_long(longer, identity) == (
            f"This chat's folder has a path that is too long for Windows: {longer} ({fits + 1} characters; at most "
            f'{fits} fit here). Move the folder to a shorter path, or start a chat in another folder.')
        session = save_session(install.root / 'app', longer, identity)   # the history itself still fits
        assert len(str(session / 'unified' / ('.presentation-' + budget.HEX32 + '.tmp'))) == budget.FILE_LIMIT + 1
        assert refused_for_length(lambda: write_view(session))
        assert not (session / 'unified' / 'view.json').exists()
        # The default workspace folder of the installation is inside the measure at the longest folder path.
        assert session_files.chat_folder_too_long(install.root / 'workspace', identity) is None
        print(f'MEASURED installation folder {budget.MAX_ROOT}: a folder that a person chose is accepted up to a path '
              f'of {fits} characters (the saved view\'s temporary file is then {budget.FILE_LIMIT}); at {fits + 1} '
              f'the app answers with its sentence and Windows refuses that file')
    finally:
        remove_tree(EXTENDED + str(parent))


# A chat with its own setup (a saved plan, as a fork has; a bundle that is not
# offered; dependency overrides of its folder) is checked in a stage too. The
# probe then gets the chat's folder as its workspace, with the same stage
# settings (``runtime_profiles.ensure``). The folders of a person: the app's
# default workspace folder ``<folder>\workspace``, and a folder somewhere else.
CHOSEN_FOLDER = 150   # the path length of the other folder: longer than any installation path


@pytest.fixture
def chosen_folder(base, long_paths_off):
    """A folder that a person chose, outside the installation, with a long path of its own."""
    parent = base / ('p' + uuid.uuid4().hex[:7])
    folder = parent / ('d' * (CHOSEN_FOLDER - len(str(parent)) - 1))
    folder.mkdir(parents=True)
    assert len(str(folder)) == CHOSEN_FOLDER <= budget.FOLDER_LIMIT
    (folder / 'notes.txt').write_text('mine', encoding='utf-8')
    yield folder
    remove_tree(EXTENDED + str(parent))


def chat_folders(install, chosen):
    """The folders in which a chat can be: the default workspace folder, a person's own, the app's chat folder."""
    from amplifier_web.windows_files import directory
    workspace = install.root / 'workspace'
    with directory(workspace, owned_root=install.root, create=True):   # setup makes it
        pass
    return {'default workspace folder': workspace, 'folder that a person chose': chosen,
            'app chat folder': install.chat()[1]}


@pytest.mark.parametrize('size', [CLEAN_ROOM_ROOT, budget.MAX_ROOT])
def test_the_setup_check_of_a_chat_fails_under_the_cli_name_of_a_persons_folder(stage, chosen_folder, size):
    """The failure that the stage name removes, with the app's real probe and the names it used before.

    Before, a folder that a person chose kept the CLI name inside the stage.
    For the default workspace folder that name repeats the installation path:
    the probe's session folder is twice the installation folder path plus 134.
    """
    install, made = stage(size)
    folders = chat_folders(install, chosen_folder)
    for label in ('default workspace folder', 'folder that a person chose'):
        folder = folders[label]
        name = update_stage_probe.cli_name(folder)
        session = made.shared / 'projects' / name / 'sessions' / str(uuid.uuid4())
        temporary = session / ('.history-' + budget.HEX32 + '.tmp')
        if label == 'default workspace folder':
            assert name == budget.cli_project(size, 'workspace').replace('-C-' + 'u' * (size - 3), name[:size])
            assert len(str(session)) == 2 * size + 134 and len(str(temporary)) == 2 * size + 180
            if size == budget.MAX_ROOT:
                assert len(str(session)) == 252 > budget.FOLDER_LIMIT
        # At every supported folder path a file that the probe must write passes the limit.
        assert len(str(temporary)) > budget.FILE_LIMIT
        result = run_probe(install, update_stage_probe.for_chat(made, folder), before_the_fix=True)
        assert result.returncode == 1 and result.facts, result.stdout + result.stderr
        assert result.facts.get('ok') is False and result.facts.get('stage') != 'complete', result.facts
        assert result.facts.get('errorType') in ('FileNotFoundError', 'OSError'), result.facts
        print(f'MEASURED installation folder {size}, {label} ({len(str(folder))} characters): under the CLI name '
              f'the session folder would be {len(str(session))} (limit {budget.FOLDER_LIMIT}) and its history '
              f'temporary {len(str(temporary))} (limit {budget.FILE_LIMIT}); the real probe ended with exit code '
              f'{result.returncode} and {json.dumps(result.facts, sort_keys=True)}')
        assert (folder / 'notes.txt').exists() == (label == 'folder that a person chose')


@pytest.mark.parametrize('size', [CLEAN_ROOM_ROOT, budget.MAX_ROOT])
def test_the_setup_check_of_a_chat_completes_in_a_stage_whatever_the_chats_folder_is(stage, chosen_folder, size):
    install, made = stage(size)
    longest = budget.STAGE_LONGEST[1] - (budget.MAX_ROOT - size)
    sessions = set()
    for label, folder in chat_folders(install, chosen_folder).items():
        before = update_stage_probe.created(folder)
        update_stage_probe.passed(run_probe(install, update_stage_probe.for_chat(made, folder)))
        # One project name in the stage, whatever the folder is: the stage's own.
        assert os.listdir(made.shared / 'projects') == [PROJECT], label
        assert os.listdir(install.root / 'capture') == [PROJECT], label
        new = {path.name for path in (made.shared / 'projects' / PROJECT / 'sessions').iterdir()
               if path.is_dir()} - sessions
        assert len(new) == 1, label
        session = new.pop()
        sessions.add(session)
        folder_of_session = made.shared / 'projects' / PROJECT / 'sessions' / session
        capture = install.root / 'capture' / PROJECT / 'sessions' / session / 'context-intelligence'
        rows = {name: budget.length(relative, size) for name, kind, relative in budget.update_stage(PROJECT, session)}
        assert len(str(folder_of_session)) == rows['probe session folder']
        assert len(str(folder_of_session / 'live-jobs' / 'owner.lock')) == rows['probe job-store lock']
        assert (len(str(capture)), len(str(capture / 'events.jsonl'))) == (
            rows['probe capture folder'], rows['probe event log'])
        assert (folder_of_session / 'transcript.jsonl').is_file() and (capture / 'events.jsonl').is_file()
        # The probe wrote nothing into the chat's folder.
        assert update_stage_probe.created(folder) == before, label
        files, folders = install.longest()
        assert files <= longest <= budget.FILE_LIMIT and folders <= budget.FOLDER_LIMIT
        print(f'MEASURED installation folder {size}, {label} ({len(str(folder))} characters): the real probe '
              f'completed under the project name {PROJECT}; session folder {len(str(folder_of_session))}, '
              f'capture folder {len(str(capture))}, longest file that exists {files}, longest folder {folders}')
    # Nothing else in the installation changed: the person's shared home, the ownership store, the source store.
    for name in ('shared', 'ownership', budget.SOURCE_FOLDER):
        assert os.listdir(install.root / name) == []
    assert (chosen_folder / 'notes.txt').read_text(encoding='utf-8') == 'mine'


# The deepest module source file of the default bundles, by its real name (125
# characters), and a file as deep as the deepest file of any default repository.
MODULE_FILE = ('modules/tool-context-intelligence-transcript/'
               'amplifier_module_tool_context_intelligence_transcript/session_transcript_tool.py')
DEEPEST_FILE = 'docs/lanes/' + '/'.join(['evidence-' + 'e' * 20] * 4) + '/result.txt'
SOURCE_URL = 'https://example.invalid/deep'


def git(folder, *arguments):
    return subprocess.check_output(
        ['git', '-c', 'core.longpaths=true', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
         *arguments], cwd=folder, text=True).strip()


@pytest.fixture
def repository(base, long_paths_off):
    """A clean Git repository that fits the limit where it is, like a clone outside the store."""
    assert len(MODULE_FILE) == budget.MODULE_DEPTH == 125 and len(DEEPEST_FILE) == 141
    folder = base / ('g-' + uuid.uuid4().hex[:8])
    folder.mkdir()
    try:
        git(folder, 'init', '--quiet', '-b', 'main')
        for name, text in (('bundle.md', 'bundle'), (MODULE_FILE, 'VALUE = "module source"'),
                           (str(Path(MODULE_FILE).parent.parent / 'pyproject.toml'),
                            '[project]\nname = "fixture"\nversion = "1"\n'),
                           (DEEPEST_FILE, 'deep evidence')):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text)
        git(folder, 'add', '.')
        git(folder, 'commit', '--quiet', '-m', 'fixture')
        yield folder, git(folder, 'rev-parse', 'HEAD')
    finally:
        remove_tree(EXTENDED + str(folder))


def imports_the_module(folder):
    """A new Python imports the deepest default module from this folder, as the activator's sys.path entry does."""
    package, name = Path(MODULE_FILE).parent.name, Path(MODULE_FILE).stem
    done = subprocess.run(
        [sys.executable, '-I', '-S', '-B', '-c',
         f'import sys; sys.path.insert(0, sys.argv[1]); import {package}.{name} as module; print(module.VALUE)',
         str(folder)], capture_output=True, text=True, timeout=120)
    return done.returncode == 0 and done.stdout.strip() == 'module source'


def test_source_store_stages_a_deep_repository_under_the_longest_supported_installation_folder(
        installation, repository, monkeypatch):
    """Foundation's real source store below the installation folder, long paths off.

    A new installation names the store ``s``: every ordinary path fits at the
    longest folder path that setup accepts, and Python imports the deepest
    default module. An installation with the earlier name ``source-store``
    keeps it: there Windows refuses that module at the setup limit and opens it
    at the stated lower one. Windows must refuse or accept each ordinary path
    exactly as the budget table says. The store itself must reach every file
    at any depth.
    """
    from amplifier_foundation.sources import shared
    repo, revision = repository
    limit = budget.EARLIER_SOURCE_STORE_LONGEST_ROOT[budget.SOURCE_OBJECT]
    assert limit < budget.MAX_ROOT and budget.SOURCE_STORE_LONGEST_ROOT == {}
    for size, folder, expected in ((budget.MAX_ROOT, budget.SOURCE_FOLDER, 253),
                                   (budget.MAX_ROOT, budget.EARLIER_SOURCE_FOLDER, 264),
                                   (limit, budget.EARLIER_SOURCE_FOLDER, 259)):
        rows = {name: (kind, relative) for name, kind, relative in budget.source_store(folder=folder)}
        install = installation(size, folder)
        store = shared.SharedSourceStore(install.root / folder)
        target = store.checkout(SOURCE_URL, revision)
        assert len(target.parent.name) == budget.SOURCE_NAME and target.name == revision

        # A failed attempt leaves no staging folder, also when it holds files over the limit.
        failure, record = OSError('injected after the deep tree was copied'), shared.atomic

        def fail(path, value):
            raise failure

        monkeypatch.setattr(shared, 'atomic', fail)
        with pytest.raises(OSError) as caught:
            asyncio.run(store.ensure(SOURCE_URL, revision, existing=repo))
        monkeypatch.setattr(shared, 'atomic', record)
        assert caught.value is failure and not getattr(failure, '__notes__', [])
        assert [path.name for path in target.parent.iterdir() if path.name.startswith('.source-')] == []

        assert asyncio.run(store.ensure(SOURCE_URL, revision, existing=repo)) == target
        assert store.verify(SOURCE_URL, revision) == target
        assert [path.name for path in target.parent.iterdir() if path.name.startswith('.source-')] == []

        # The store sealed the deepest file. Only the extended-length form reaches it.
        deepest = target / DEEPEST_FILE
        assert len(str(deepest)) > budget.FILE_LIMIT and not os.path.exists(deepest)
        assert Path(EXTENDED + str(deepest)).read_text() == 'deep evidence'
        assert not os.stat(EXTENDED + str(deepest)).st_mode & stat.S_IWRITE

        # Module source in the object: the path that Python imports from.
        kind, relative = rows[budget.SOURCE_OBJECT]
        module = target / MODULE_FILE
        assert len(install.below(module)) == len(relative)
        assert len(str(module)) == budget.length(relative, size) == expected
        if budget.fits(kind, relative, size):
            assert expected <= budget.FILE_LIMIT
            assert module.read_text() == 'VALUE = "module source"'
            assert imports_the_module(module.parent.parent)
        else:
            assert (size, folder) == (budget.MAX_ROOT, budget.EARLIER_SOURCE_FOLDER)
            assert refused_for_length(module.read_text)
            assert not imports_the_module(module.parent.parent)
            assert Path(EXTENDED + str(module)).read_text() == 'VALUE = "module source"'

        # The build view: a build backend reads module source here by ordinary paths.
        build, immutable = shared.build_view(module.parent.parent)
        view = build.parents[1]
        assert immutable and view.parent == store.root / 'builds' and len(view.name) == budget.SOURCE_NAME
        kind, relative = rows[budget.SOURCE_BUILD]
        assert len(install.below(view / MODULE_FILE)) == len(relative) - budget.BYTECODE
        assert budget.fits(kind, relative, size)
        assert (view / MODULE_FILE).read_text() == 'VALUE = "module source"'
        assert (build / 'pyproject.toml').is_file()
        with shared.build_lock(build):
            pass
        assert Path(EXTENDED + str(view / DEEPEST_FILE)).read_text() == 'deep evidence'
        assert [path.name for path in view.parent.iterdir() if path.name.startswith('.build-')] == []

        # The staged record is the one file the store opens by an ordinary path while staging.
        kind, relative = rows[budget.SOURCE_STAGE]
        assert len(install.below(target.parent / ('.source-' + 'r' * 8) / 'checkout' / '.amplifier_cache_meta.json')) \
            == len(relative) and budget.fits(kind, relative, size)


def test_each_installation_resolves_the_source_store_folder_it_has(installation):
    """The launcher's choice through the real private-folder checks: no folder is created or moved."""
    from amplifier_web import windows_consumer
    from amplifier_web.windows_files import directory
    assert (windows_consumer.SOURCE_STORE, windows_consumer.EARLIER_SOURCE_STORE) == (
        budget.SOURCE_FOLDER, budget.EARLIER_SOURCE_FOLDER)

    def names(install):
        return sorted(entry.name for entry in os.scandir(install.root))

    # An installation that an earlier setup file made keeps its store.
    earlier = installation(store=budget.EARLIER_SOURCE_FOLDER)
    (earlier.root / budget.EARLIER_SOURCE_FOLDER / 'objects').mkdir()
    before = names(earlier)
    assert windows_consumer.source_store(earlier.root) == earlier.root / budget.EARLIER_SOURCE_FOLDER
    assert names(earlier) == before and budget.SOURCE_FOLDER not in before
    assert (earlier.root / budget.EARLIER_SOURCE_FOLDER / 'objects').is_dir()

    # A new installation has only the short folder.
    new = installation()
    before = names(new)
    assert windows_consumer.source_store(new.root) == new.root / budget.SOURCE_FOLDER
    assert names(new) == before and budget.EARLIER_SOURCE_FOLDER not in before

    # With both folders the earlier one stays in use: an earlier launcher uses it too.
    with directory(new.root / budget.EARLIER_SOURCE_FOLDER, owned_root=new.root, create=True):
        pass
    assert windows_consumer.source_store(new.root) == new.root / budget.EARLIER_SOURCE_FOLDER

    # With neither folder the installation is damaged and nothing is created.
    damaged = installation()
    (damaged.root / budget.SOURCE_FOLDER).rmdir()
    before = names(damaged)
    with pytest.raises(FileNotFoundError):
        windows_consumer.source_store(damaged.root)
    assert names(damaged) == before
