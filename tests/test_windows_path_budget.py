"""The Windows path budget: what each store may add below the installation folder.

Windows refuses a file path over 259 characters and creates no folder over 247
when long paths are off, which is the Windows default. Setup limits the
installation folder path (``MaxRootLength``). This file is the one table of what
the app adds below that folder, and it fails when the sum no longer fits:

- someone raises the setup limit;
- someone lengthens a store path or a temporary name.

It is arithmetic only. ``tests/test_windows_long_paths_off.py`` runs the real
stores on native Windows with long paths off. Print the table with
``python tests/test_windows_path_budget.py``.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import re
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILE_LIMIT = 259    # longest file path Windows accepts with long paths off
FOLDER_LIMIT = 247  # longest folder path that Windows creates with long paths off
# The single number that setup uses. Nothing here repeats it.
MAX_ROOT = int(re.search(r'internal const int MaxRootLength=(\d+);',
                         (ROOT / 'scripts/windows_bootstrap_native.cs').read_text(encoding='utf-8')).group(1))

HEX64, HEX40, HEX32 = 'f' * 64, 'f' * 40, 'f' * 32
ROOT_ID = '8c7973a8-38de-5423-b16c-ff0d26c3f2f1'       # a root session ID is a UUID (36)
CHAT_PROJECT = 'chat-8c7973a838de5423'                  # session_files.chat_project_name (21)
FILE, FOLDER = 'file', 'folder'


def session_store(project, identity):
    """``<root>\\shared`` is AMPLIFIER_HOME. Names are Foundation's, loop-live's and Unified's."""
    sessions = f'shared\\projects\\{project}\\sessions'
    session = f'{sessions}\\{identity}'
    return [
        ('session folder', FOLDER, session),
        ('transcript backup', FILE, f'{session}\\transcript.jsonl.backup'),
        ('pending transcript append', FILE, f'{session}\\transcript.jsonl.append-pending'),
        ('history temporary', FILE, f'{session}\\.history-{HEX32}.tmp'),
        ('metadata lock beside the folder', FILE, f'{sessions}\\.{identity}.metadata.lock'),
        ('view temporary', FILE, f'{session}\\unified\\.presentation-{HEX32}.tmp'),
        ('background-job record', FILE, f'{session}\\live-jobs\\job-{HEX64}.json'),
    ]


def capture_store(project, identity):
    """``<root>\\capture`` is AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH."""
    capture = f'capture\\{project}\\sessions\\{identity}\\context-intelligence'
    return [
        ('capture folder', FOLDER, capture),
        ('event log', FILE, f'{capture}\\events.jsonl'),
        ('hook metadata temporary', FILE, f'{capture}\\metadata.json.{"9" * 10}.tmp'),
        ('request observation', FILE, f'{capture}\\context-inspection\\{HEX64}.json'),
        ('request observation temporary', FILE, f'{capture}\\context-inspection\\.private-{HEX32}.tmp'),
        ('oversized-request copy', FILE, f'{capture}\\provider-payloads\\{HEX64}.json'),
    ]


def ownership_store(identity):
    """``<root>\\ownership`` is AMPLIFIER_SESSION_STATE_HOME. Root sessions only."""
    owner = f'ownership\\v1\\{HEX64}\\{identity}'
    return [
        ('ownership folder', FOLDER, owner),
        ('session lock', FILE, f'{owner}\\session.lock'),
        ('owner temporary', FILE, f'{owner}\\.owner-{HEX32}.tmp'),
    ]


def length(relative, root=None):
    return (MAX_ROOT if root is None else root) + 1 + len(relative)


def fits(kind, relative, root=None):
    return length(relative, root) <= (FOLDER_LIMIT if kind == FOLDER else FILE_LIMIT)


def cli_project(root, below):
    """The shared CLI name of ``<root>\\<below>``: separators become '-', the colon goes."""
    return '-' + ('C:\\' + 'u' * (root - 3) + '\\' + below).replace('\\', '-').replace(':', '')


def longest_identity(shape, project):
    """The longest session ID for which this path shape fits under the longest root."""
    return max(size for size in range(1, 222) if fits(*shape(project, 'i' * size)[1:]))


def per_session(project):
    """One callable per path shape that depends on the session ID."""
    names = [row[0] for row in session_store(project, ROOT_ID) + capture_store(project, ROOT_ID)]
    return {name: (lambda project, identity, name=name: next(
        row for row in session_store(project, identity) + capture_store(project, identity) if row[0] == name))
        for name in names}


# The longest session ID that each path shape holds in an app chat when the
# installation folder path has the longest supported length. A root session ID
# has 36 characters. A worker (sub-agent) session ID is
# ``<16 hex>-<16 hex>_<agent name>``: 34 characters plus the agent name.
#
# These are the budget. A change to the setup limit, to a store folder or to a
# temporary name changes a number here and fails the test. Decide then, do not
# only update the number: every row must hold a root session, and the lowest
# rows decide which worker sessions lose a diagnostic file.
LONGEST_SESSION_ID = {
    'request observation': 50,
    'oversized-request copy': 51,
    'background-job record': 68,
    'request observation temporary': 74,
    'view temporary': 93,
    'history temporary': 106,
    'hook metadata temporary': 110,
    'pending transcript append': 120,
    'event log': 126,
    'capture folder': 127,
    'transcript backup': 128,
    'metadata lock beside the folder': 137,
    'session folder': 140,
}
# Every shape holds a worker session ID of this length at the longest root.
# Longer IDs lose files in the order above. The first two are diagnostics: the
# request observation exists for root sessions only, and the oversized-request
# copy records "unavailable" and the turn continues (provider_recording.py).
WORKER_ID = 50


def test_setup_limit_is_the_number_this_budget_was_made_for():
    # The installer design states the same number; tests/test_windows_setup_folder.py keeps them equal.
    assert MAX_ROOT == 59


def test_app_chat_root_session_fits_in_every_store_under_the_longest_root():
    rows = session_store(CHAT_PROJECT, ROOT_ID) + capture_store(CHAT_PROJECT, ROOT_ID) + ownership_store(ROOT_ID)
    over = [(name, length(relative)) for name, kind, relative in rows if not fits(kind, relative)]
    assert not over
    # The first-chat failure of release 0.94.1: this temporary file, 277 characters.
    assert length(f'shared\\projects\\{CHAT_PROJECT}\\sessions\\{ROOT_ID}\\unified\\.presentation-{HEX32}.tmp') == 202


def test_longest_session_id_per_path_shape_is_the_recorded_budget():
    shapes = per_session(CHAT_PROJECT)
    measured = {name: longest_identity(shape, CHAT_PROJECT) for name, shape in shapes.items()}
    assert measured == LONGEST_SESSION_ID
    assert min(measured.values()) == WORKER_ID >= len(ROOT_ID)


def test_the_model_reproduces_the_measured_clean_room_failures():
    # Run 3, release 0.94.1: installation folder path 51, CLI name, long paths off.
    project = cli_project(51, f'app\\chats\\{ROOT_ID}\\files')
    rows = {name: (kind, relative) for name, kind, relative in session_store(project, ROOT_ID)}
    assert length(rows['session folder'][1], 51) == 218           # created
    assert length(rows['view temporary'][1], 51) == 277           # refused: the 500
    # Run 2, release 0.94.0: installation folder path 92. The project folder itself was refused.
    project = cli_project(92, f'app\\chats\\{ROOT_ID}\\files')
    assert length(f'shared\\projects\\{project}', 92) == 254 > FOLDER_LIMIT


def test_without_the_short_name_an_app_chat_does_not_fit():
    # Why the short name exists: the CLI name repeats the installation path.
    project = cli_project(MAX_ROOT, f'app\\chats\\{ROOT_ID}\\files')
    over = {name for name, kind, relative in session_store(project, ROOT_ID) + capture_store(project, ROOT_ID)
            if not fits(kind, relative)}
    assert {'history temporary', 'view temporary', 'background-job record', 'request observation'} <= over


# Documented limit, not fixed here: a chat that a person starts in the app's
# default workspace folder (``<root>\workspace``) keeps the CLI name, like every
# folder a person chooses. That name also repeats the installation path. Each
# number is the longest installation folder path at which a root session fits.
DEFAULT_WORKSPACE_LONGEST_ROOT = {
    'request observation': 42,
    'oversized-request copy': 42,
    'background-job record': 51,
    'request observation temporary': 54,
}


def test_default_workspace_folder_limits_are_stated_not_hidden():
    def longest_root(name):
        for root in range(MAX_ROOT, 0, -1):
            project = cli_project(root, 'workspace')
            kind, relative = next(row[1:] for row in session_store(project, ROOT_ID) + capture_store(project, ROOT_ID)
                                  if row[0] == name)
            if fits(kind, relative, root):
                return root

    names = [row[0] for row in session_store('p', ROOT_ID) + capture_store('p', ROOT_ID)]
    measured = {name: longest_root(name) for name in names}
    assert {name: root for name, root in measured.items() if root < MAX_ROOT} == DEFAULT_WORKSPACE_LONGEST_ROOT


# Source store (Amplifier Foundation owns the layout; Unified supplies the root).
# SOURCE_NAME is the length of an object or build folder name. The locked
# Foundation revision uses the full SHA-256 (64). Foundation's Windows change
# uses 16 for new entries. The Unified change that moves the Foundation lock
# sets SOURCE_NAME from Foundation's constant and empties PENDING_FOUNDATION.
SOURCE_NAME = 64
SOURCE_NAME_AFTER_FOUNDATION_CHANGE = 16
CONTENT_DEPTH = 78   # deepest file the app opens by an ordinary path in an object (default bundles, measured)
MODULE_DEPTH = 125   # deepest module source file a build backend reads in a build view (measured)
BYTECODE = 25        # allowance for the bytecode name of that module file
PENDING_FOUNDATION = {'source object content', 'build view module file'}


def source_store(name):
    return [
        ('source object content', FILE, f'source-store\\objects\\{"f" * name}\\{HEX40}\\' + 'c' * CONTENT_DEPTH),
        ('build view module file', FILE, f'source-store\\builds\\{"f" * name}\\' + 'm' * (MODULE_DEPTH + BYTECODE)),
        # After Foundation's change the store walks a staged checkout by
        # extended-length paths; only this file is opened by an ordinary path.
        ('staged checkout record', FILE,
         f'source-store\\objects\\{"f" * name}\\.source-{"r" * 8}\\checkout\\.amplifier_cache_meta.json'),
    ]


def test_source_store_fits_once_foundation_shortens_its_folder_names():
    after = source_store(SOURCE_NAME_AFTER_FOUNDATION_CHANGE)
    assert all(fits(kind, relative) for _, kind, relative in after)
    assert length(after[0][2]) - MAX_ROOT - CONTENT_DEPTH == 80   # \source-store\objects\<16>\<40>\
    assert length(after[1][2]) - MAX_ROOT - MODULE_DEPTH - BYTECODE == 38  # \source-store\builds\<16>\
    # Today's state, stated so that it cannot change unnoticed in either direction.
    over = {name for name, kind, relative in source_store(SOURCE_NAME) if not fits(kind, relative)}
    assert over == PENDING_FOUNDATION


def test_measured_application_depths_fit_under_the_longest_root():
    design = (ROOT / 'docs/design/native-windows-installation.md').read_text(encoding='utf-8')
    sentence = re.search(r'Worst\s+observed depths below the folder:(.*?)\.', design, re.S).group(1)
    depths = [int(value) for value in re.findall(r'\b(\d+)\b', sentence)]
    assert len(depths) == 5 and all(MAX_ROOT + depth <= FILE_LIMIT for depth in depths)


# The table above is only as true as the names in the code. Each check below
# reads the name from its owner, so a longer name there fails here.


def test_store_folders_are_the_ones_the_app_really_uses(tmp_path, monkeypatch):
    from amplifier_web import session_files
    from amplifier_web.managed_chats import allocate
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    (tmp_path / 'app').mkdir()
    workspace = Path(allocate(tmp_path / 'app', ROOT_ID, 'receipt'))

    def below(path):
        return '\\'.join(Path(path).relative_to(tmp_path.resolve()).parts)

    assert session_files.project_slug(workspace) == CHAT_PROJECT
    assert below(workspace) == f'app\\chats\\{ROOT_ID}\\files'
    assert below(session_files.sessions_dir(workspace) / ROOT_ID) == session_store(CHAT_PROJECT, ROOT_ID)[0][2]
    assert below(session_files.capture_dir(workspace, ROOT_ID)) == capture_store(CHAT_PROJECT, ROOT_ID)[0][2]
    from amplifier_web.context_inspection_store import InspectionStore, key
    from amplifier_web import provider_payloads
    capture = session_files.capture_dir(workspace, ROOT_ID)
    assert below(InspectionStore(capture).directory / (key('llm:1') + '.json')) == \
        capture_store(CHAT_PROJECT, ROOT_ID)[3][2].replace(HEX64, key('llm:1'))
    assert below(capture / provider_payloads.DIRECTORY / (HEX64 + '.json')) == capture_store(CHAT_PROJECT, ROOT_ID)[5][2]
    consumer = (ROOT / 'amplifier_web/windows_consumer.py').read_text(encoding='utf-8')
    for setting, folder in (('AMPLIFIER_WEB_HOME', 'app'), ('AMPLIFIER_HOME', 'shared'),
                            ('AMPLIFIER_SESSION_STATE_HOME', 'ownership'),
                            ('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', 'capture'),
                            ('AMPLIFIER_SOURCE_STORE', 'source-store')):
        assert f'"{setting}": "{folder}"' in consumer


def test_temporary_and_sibling_names_are_the_ones_the_code_writes():
    def source(path):
        return (ROOT / path).read_text(encoding='utf-8')

    assert "destination.with_name(f'.presentation-{uuid.uuid4().hex}.tmp')" in source('amplifier_web/presentation_files.py')
    assert 'f".private-{uuid.uuid4().hex}.tmp"' in source('amplifier_web/windows_files.py')
    assert "identity + '.json'" in source('amplifier_web/provider_payloads.py')
    # Temporary files that tempfile names: a short prefix plus eight characters.
    assert 'tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)' in source('amplifier_web/host/storage.py')
    assert 'tempfile.NamedTemporaryFile(dir=directory, delete=False)' in source('amplifier_web/provider_payloads.py')
    assert len(uuid.uuid4().hex) == len(HEX32)
    # Amplifier Foundation at the locked revision.
    from amplifier_foundation.session import _windows_history, _windows_ownership, jsonl, metadata
    assert _windows_history._TEMPORARY.pattern == r'\.history-[0-9a-f]{32}\.tmp'
    assert 'f".history-{uuid.uuid4().hex}.tmp"' in inspect.getsource(_windows_history)
    assert 'f".owner-{uuid.uuid4().hex}.tmp"' in inspect.getsource(_windows_ownership)
    assert 'f".{session_dir.name}.metadata.lock"' in inspect.getsource(metadata)
    assert 'path.name + ".append-pending"' in inspect.getsource(jsonl)
    from amplifier_foundation.session.shared_state import SharedSessionStore
    assert 'self.root / "v1" / _workspace_key(self.workspace) / self.session_id' in inspect.getsource(SharedSessionStore)


def test_dependency_names_are_the_ones_their_modules_write():
    job_store = pytest.importorskip('amplifier_module_loop_live.job_store')
    text = inspect.getsource(job_store)
    assert '"job-" + hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"' in text
    assert 'tempfile.mkstemp(prefix=".pending-", dir=self.directory)' in text
    handler = pytest.importorskip('amplifier_module_hook_context_intelligence.handlers.logging_handler')
    assert 'meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")' in inspect.getsource(handler)


def table():
    """The budget as text, for a review or a report."""
    lines = [f'Longest installation folder path: {MAX_ROOT}. File limit {FILE_LIMIT}, folder limit {FOLDER_LIMIT}.',
             '', 'App chat, root session (36 characters). Characters below the installation folder, and the path length:']
    for name, kind, relative in (session_store(CHAT_PROJECT, ROOT_ID) + capture_store(CHAT_PROJECT, ROOT_ID)
                                 + ownership_store(ROOT_ID)):
        lines.append(f'  {name:34} {kind:6} +{len(relative) + 1:3} = {length(relative):3}')
    lines += ['', 'Longest session ID that each shape holds (worker sessions):']
    lines += [f'  {name:34} {size:3}' for name, size in sorted(LONGEST_SESSION_ID.items(), key=lambda item: item[1])]
    lines += ['', 'Chat in the default workspace folder (CLI name): longest installation folder path that fits:']
    lines += [f'  {name:34} {size:3}' for name, size in DEFAULT_WORKSPACE_LONGEST_ROOT.items()]
    lines += ['', 'Source store (Foundation). Folder name width today / after the Foundation change:']
    for (name, kind, today), (_, _, after) in zip(source_store(SOURCE_NAME),
                                                 source_store(SOURCE_NAME_AFTER_FOUNDATION_CHANGE)):
        lines.append(f'  {name:34} {length(today):3} / {length(after):3}')
    return '\n'.join(lines)


if __name__ == '__main__':
    print(table())
