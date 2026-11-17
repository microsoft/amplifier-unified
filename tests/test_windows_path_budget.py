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


# Stated limit, not fixed here: a folder that a person chose anywhere keeps the
# CLI name, which is as long as the folder's own path. Each number is the room
# of one row: the installation folder path plus the path of the person's folder
# may be at most that many characters for a root session. The rows of
# KEPT_CHAT are what keeping a chat needs; when one of them does not fit, the
# app answers with a plain sentence before it makes anything
# (``session_files.chat_folder_too_long``). So at the longest installation
# folder path a person's folder may have a path of 78 characters, and 103 at
# the shortest. The four rows with less room are lost first, in that order.
PERSONS_FOLDER_ROOM = {
    'request observation': 94,
    'oversized-request copy': 95,
    'background-job record': 112,
    'request observation temporary': 118,
    'view temporary': 137,
    'history temporary': 150,
    'hook metadata temporary': 154,
    'pending transcript append': 164,
    'event log': 170,
    'capture folder': 171,
    'transcript backup': 172,
    'metadata lock beside the folder': 181,
    'session folder': 184,
}
KEPT_CHAT = ('view temporary', 'history temporary', 'hook metadata temporary', 'pending transcript append', 'event log',
             'capture folder', 'transcript backup', 'metadata lock beside the folder', 'session folder')
KEPT_CHAT_ROOM = 137


def test_a_persons_folder_limits_are_stated_not_hidden():
    def room(name, root):
        def fitting(size):
            project = '-' + 'p' * (size - 1)   # the CLI name of a folder path of this length
            kind, relative = next(row[1:] for row in session_store(project, ROOT_ID) + capture_store(project, ROOT_ID)
                                  if row[0] == name)
            return fits(kind, relative, root)
        return root + max(size for size in range(1, 250) if fitting(size))

    names = [row[0] for row in session_store('p', ROOT_ID) + capture_store('p', ROOT_ID)]
    for root in (34, 51, MAX_ROOT):
        assert {name: room(name, root) for name in names} == PERSONS_FOLDER_ROOM
    assert min(PERSONS_FOLDER_ROOM[name] for name in KEPT_CHAT) == KEPT_CHAT_ROOM
    assert (KEPT_CHAT_ROOM - MAX_ROOT, KEPT_CHAT_ROOM - 34) == (78, 103)
    # The app's answer measures exactly these rows.
    from amplifier_web import session_files
    assert session_files._FILE_LIMIT == FILE_LIMIT
    assert FILE_LIMIT - len('\\shared\\projects\\\\sessions\\' + ROOT_ID) - session_files._SESSION_TAIL == KEPT_CHAT_ROOM
    assert FILE_LIMIT - len('\\capture\\\\sessions\\' + ROOT_ID) - session_files._CAPTURE_TAIL == \
        PERSONS_FOLDER_ROOM['hook metadata temporary']
    # The default workspace folder is the installation folder path plus 10: the same limits as above.
    for name, longest in DEFAULT_WORKSPACE_LONGEST_ROOT.items():
        assert (PERSONS_FOLDER_ROOM[name] - len('\\workspace')) // 2 == longest
    assert 2 * MAX_ROOT + len('\\workspace') <= KEPT_CHAT_ROOM   # and a chat there is always kept


# Stated limit, not fixed here: a helper (sub-agent) session has the ID
# ``<16 hex>-<16 hex>_<agent name>`` (34 characters plus the agent's name), and
# the app saves its history in the same project folder as the chat's
# (``host/children.py`` uses the chat's store). Under a CLI name that history
# has less room than the chat's own. Each number is the longest agent name
# with which every history row of a helper fits. The app's answer
# (``session_files.chat_folder_too_long``) measures the chat, not a helper.
HELPER_HISTORY = ('session folder', 'transcript backup', 'pending transcript append', 'history temporary',
                  'metadata lock beside the folder')
HELPER_ID = 34
HELPER_NAME_AT_THE_LONGEST_ACCEPTED_FOLDER = 15
HELPER_NAME_IN_THE_DEFAULT_WORKSPACE_FOLDER = {34: 74, 51: 40, 59: 24}   # by installation folder path
HELPER_NAME_IN_AN_APP_CHAT_FOLDER = 72


def helper_name(project, root=None):
    """The longest agent name with which the history of a helper session fits under this project name."""
    return max(size for size in range(1, 222) if all(
        fits(kind, relative, root) for name, kind, relative in session_store(project, 'i' * (HELPER_ID + size))
        if name in HELPER_HISTORY))


def test_a_helper_session_has_less_room_under_a_cli_name():
    from amplifier_foundation import tracing
    identity = tracing.generate_sub_session_id(agent_name='a' * 15, parent_session_id=ROOT_ID)
    assert re.fullmatch(r'[0-9a-f]{16}-[0-9a-f]{16}_a{15}', identity) and len(identity) == HELPER_ID + 15
    assert 'self.store.save(identity, messages' in (ROOT / 'amplifier_web/host/children.py').read_text(encoding='utf-8')
    # The longest folder of a person that the app accepts, at every installation folder path.
    for root in (34, 51, MAX_ROOT):
        assert helper_name('-' + 'p' * (KEPT_CHAT_ROOM - root - 1), root) == HELPER_NAME_AT_THE_LONGEST_ACCEPTED_FOLDER
    assert {root: helper_name(cli_project(root, 'workspace'), root)
            for root in (34, 51, MAX_ROOT)} == HELPER_NAME_IN_THE_DEFAULT_WORKSPACE_FOLDER
    assert helper_name(CHAT_PROJECT) == HELPER_NAME_IN_AN_APP_CHAT_FOLDER
    # The row that decides is the temporary file of the history writer.
    assert HELPER_NAME_AT_THE_LONGEST_ACCEPTED_FOLDER == (
        PERSONS_FOLDER_ROOM['history temporary'] - KEPT_CHAT_ROOM + len(ROOT_ID) - HELPER_ID)
    assert HELPER_NAME_IN_AN_APP_CHAT_FOLDER == LONGEST_SESSION_ID['history temporary'] - HELPER_ID


# Update stage. An in-app component update, and a setup run on a PC that
# already has an AI connection, prepare the components in the stage folder
# ``<root>\app\updates\releases\<32 hex>``. The compatibility probe then runs
# with the stage as its app home and ``<stage>\shared-config`` as its shared
# home, so the stage path is inside every session path of the probe. The probe
# mounts a worker in ``<stage>\qualification-workspace`` (the check folder).
#
# A first installation has no stage: ``<root>\app\updates\baseline-runtime``
# holds receipts only, and no probe runs there.
STAGE = f'app\\updates\\releases\\{HEX32}'
CHECK_FOLDER = f'{STAGE}\\qualification-workspace'
BASELINE = 'app\\updates\\baseline-runtime'
UPDATE_PROJECT = 'update-7260914d5698'   # session_files.stage_project_name (19)


def update_stage(project=UPDATE_PROJECT, identity=ROOT_ID):
    """What a stage holds, and every path shape of the one session that a probe makes there."""
    return [
        ('stage folder', FOLDER, STAGE),
        ('check folder', FOLDER, CHECK_FOLDER),
        ('copied shared settings', FILE, f'{STAGE}\\shared-config\\settings.yaml'),
        ('longest receipt', FILE, f'{STAGE}\\runtime-policy-reconciliation.json'),
        ('source binding', FILE, f'{STAGE}\\foundation\\cache\\.source-bindings\\{HEX64}.json'),
        ('probe report', FILE, f'{STAGE}\\runtime-reports\\{identity}\\module-load-failures.json'),
        ('probe job-store lock', FILE,
         f'{STAGE}\\shared-config\\projects\\{project}\\sessions\\{identity}\\live-jobs\\owner.lock'),
    ] + [('probe ' + name, kind, f'{STAGE}\\shared-config' + relative.removeprefix('shared'))
         for name, kind, relative in session_store(project, identity)
    ] + [('probe ' + name, kind, relative) for name, kind, relative in capture_store(project, identity)]


# The two session-store shapes that do not fit in a stage. A probe writes
# neither: the app saves a view only for a chat that it shows, and a probe
# starts no background job. ``tests/test_windows_long_paths_off.py`` checks the
# complete list of what the real probe leaves in a stage with its test profile.
STAGE_NEVER_WRITTEN = {'probe view temporary', 'probe background-job record'}
# The longest path that a stage and its probe create at the longest root, and
# the characters that remain below the file limit.
STAGE_LONGEST = ('probe history temporary', 248)
STAGE_SPARE = 11


def test_update_stage_and_its_probe_fit_under_the_longest_root():
    rows = update_stage()
    assert {name for name, kind, relative in rows if not fits(kind, relative)} == STAGE_NEVER_WRITTEN
    written = [(length(relative), name) for name, kind, relative in rows if name not in STAGE_NEVER_WRITTEN]
    assert max(written) == STAGE_LONGEST[::-1] and FILE_LIMIT - max(written)[0] == STAGE_SPARE
    folders = [length(relative) for name, kind, relative in rows if kind == FOLDER and name not in STAGE_NEVER_WRITTEN]
    assert max(folders) == 202 <= FOLDER_LIMIT   # the probe's session folder
    # The check folder is the app's own folder. Its name is not a CLI name and not a chat name.
    assert len(UPDATE_PROJECT) == 19 and not UPDATE_PROJECT.startswith(('-', 'chat-'))


# A chat with its own setup: dependency overrides of its workspace or session,
# a saved plan (a fork has one), a bundle that is not offered, or local code.
# ``runtime_profiles.ensure`` checks it in a stage too, with the CHAT's folder
# as the workspace of the probe. In a process that runs in a stage every
# folder has the stage's project name (``session_files.stage_project_name``),
# so the rows of that probe are the rows above, whatever the chat's folder is:
# an app chat folder, the default workspace folder, a folder a person chose.
# These are the paths that only this kind of stage adds.
SETUP_INDEX = f'app\\updates\\profiles\\{HEX64}.json'
SETUP_PROJECT = f'app\\runtime\\prepare-{HEX32}'


def chat_setup_stage(project=UPDATE_PROJECT, identity=ROOT_ID):
    return update_stage(project, identity) + [
        ('setup attempt record', FILE, f'{STAGE}\\profile-attempt.json'),
        ('setup profile list', FILE, f'{STAGE}\\profiles.json'),
        ('saved plan of the chat', FILE, f'{STAGE}\\runtime-plan.json'),
        ('setup installer policy', FILE, f'{STAGE}\\runtime-install-overrides.txt'),
        ('copied connection keys', FILE, f'{STAGE}\\shared-config\\keys.env'),
        ('setup index', FILE, SETUP_INDEX),
        ('setup index lock', FILE, SETUP_INDEX + '.lock'),
        ('setup project', FOLDER, SETUP_PROJECT),
        ('setup project lock file', FILE, f'{SETUP_PROJECT}\\pyproject.toml'),
    ]


def test_a_chat_with_its_own_setup_fits_whatever_its_folder_is():
    rows = chat_setup_stage()
    assert {name for name, kind, relative in rows if not fits(kind, relative)} == STAGE_NEVER_WRITTEN
    written = [(length(relative), name) for name, kind, relative in rows if name not in STAGE_NEVER_WRITTEN]
    # The longest path is the probe's own, as in an update: this kind of stage adds no longer one.
    assert max(written) == STAGE_LONGEST[::-1]
    assert max(length(relative) for name, kind, relative in rows[len(update_stage()):]) == 155
    # The rule that makes it so. The stage name comes from the app home setting,
    # before the folder is looked at; the probe gets the chat's folder and the stage settings.
    from amplifier_web import session_files
    slug = inspect.getsource(session_files.project_slug)
    assert slug.index('staged = stage_project_name()') < slug.index('short = chat_project_name(resolved)')
    assert 'path' not in inspect.signature(session_files.stage_project_name).parameters
    profiles = (ROOT / 'amplifier_web/runtime_profiles.py').read_text(encoding='utf-8')
    assert 'stage = receipt_directory(home, selected)' in profiles and 'workspace = config.workspace' in profiles
    assert '"AMPLIFIER_HOME": str(shared)' in profiles and 'shared = stage / "shared-config"' in profiles
    assert '"AMPLIFIER_WEB_HOME": str(stage)' in profiles
    assert 'index = Path(home) / "updates/profiles" / (identity + ".json")' in profiles
    assert 'project = Path(home) / "runtime" / ("prepare-" + selected)' in profiles


# What the rule removes. Before it, a folder that a person chose kept the CLI
# name inside the stage, and that name repeats the installation path below
# the stage. Each number is the longest installation folder path at which the
# row fitted for the default workspace folder (``<root>\workspace``). So a
# chat there with its own setup could be checked only up to a folder path of
# 39; setup accepts 34 to 59. The native case runs the probe both ways.
WITHOUT_THE_STAGE_NAME_DEFAULT_WORKSPACE_LONGEST_ROOT = {
    'probe history temporary': 39,
    'probe request observation': 42,
    'probe oversized-request copy': 42,
    'probe pending transcript append': 46,
    'probe transcript backup': 50,
    'probe job-store lock': 52,
    'probe request observation temporary': 54,
    'probe metadata lock beside the folder': 55,
    'probe session folder': 56,
}


def test_without_the_stage_name_a_persons_folder_does_not_fit_in_a_stage():
    def longest_root(name):
        for root in range(MAX_ROOT, 0, -1):
            kind, relative = next(row[1:] for row in update_stage(cli_project(root, 'workspace')) if row[0] == name)
            if fits(kind, relative, root):
                return root

    measured = {name: longest_root(name) for name, kind, relative in update_stage() if name not in STAGE_NEVER_WRITTEN}
    assert ({name: root for name, root in measured.items() if root < MAX_ROOT}
            == WITHOUT_THE_STAGE_NAME_DEFAULT_WORKSPACE_LONGEST_ROOT)
    # The probe's session folder there: twice the installation folder path plus 134.
    session = next(relative for name, kind, relative in update_stage(cli_project(MAX_ROOT, 'workspace'))
                   if name == 'probe session folder')
    assert length(session) == 2 * MAX_ROOT + 134 == 252 > FOLDER_LIMIT
    # A folder that a person chose anywhere else: the length of its own path was in every session
    # row. The longest folder path that fitted, per installation folder path (shortest, clean room, longest):
    def longest_folder(root):
        return max(size for size in range(1, 200) if all(
            fits(kind, relative, root) for name, kind, relative in update_stage('-' + 'p' * (size - 1))
            if name not in STAGE_NEVER_WRITTEN))

    assert {root: longest_folder(root) for root in (34, 51, MAX_ROOT)} == {34: 55, 51: 38, MAX_ROOT: 30}
    # With the stage name no row holds the folder's path: every row fits at every supported folder path.
    assert all(fits(kind, relative, root) for root in range(34, MAX_ROOT + 1)
               for name, kind, relative in chat_setup_stage() if name not in STAGE_NEVER_WRITTEN)


def test_the_model_reproduces_the_measured_update_stage_failure():
    # Clean-room Run B, update from 0.98.4 to 0.98.5: installation folder path 51, long paths off.
    assert (length(STAGE, 51), length(CHECK_FOLDER, 51), length(f'{STAGE}\\shared-config\\projects', 51)) == (
        105, 129, 128)                                                       # measured in the guest
    project = cli_project(51, CHECK_FOLDER)
    assert len(project) == 129
    assert length(f'{STAGE}\\shared-config\\projects\\{project}', 51) == 258 > FOLDER_LIMIT   # refused: ENOENT
    assert length(f'{STAGE}\\shared-config\\projects\\{project}\\sessions', 51) == 267

    def session_folder(root):
        return length(f'{STAGE}\\shared-config\\projects\\{cli_project(root, CHECK_FOLDER)}\\sessions\\{ROOT_ID}', root)

    # The CLI name repeats the installation path below the stage. No consumer
    # installation held a probe session under it: not the shortest folder path
    # (33 characters plus an account name of one letter), not the longest.
    assert session_folder(34) == 270 > FOLDER_LIMIT and session_folder(MAX_ROOT) == 320
    assert max(root for root in range(1, 99) if session_folder(root) <= FOLDER_LIMIT) == 22


def test_a_first_installation_has_receipts_and_no_stage():
    from amplifier_web import installation_runtime
    assert length(f'{BASELINE}\\runtime-install-overrides.txt') == 118      # its longest path
    baseline = inspect.getsource(installation_runtime._prepare_baseline)
    assert 'validate(' not in baseline and 'shared-config' not in baseline and 'qualification-workspace' not in baseline
    # With a saved connection, setup makes a stage and runs the same probe as an update.
    configured = inspect.getsource(installation_runtime.prepare)
    assert 'stage = receipt_directory(home, generation)' in configured
    assert 'await UpdateManager.validate(owner, stage, generation)' in configured


# Source store (Amplifier Foundation owns the layout; Unified supplies the root).
# On Windows the locked Foundation names a new object or build folder with the
# first SOURCE_NAME digits of its 64-digit name. The store reaches a staged
# checkout, its seal, its cleanup and the copy into a build view by
# extended-length paths, so the depth of a repository does not limit them.
# These rows are the paths that other code opens by an ordinary path.
SOURCE_NAME = 16
# AMPLIFIER_SOURCE_STORE below the installation folder. Setup names it "s" in a
# new installation. An installation that an earlier setup file made has the
# longer name and keeps its store there.
SOURCE_FOLDER, EARLIER_SOURCE_FOLDER = 's', 'source-store'
MODULE_DEPTH = 125   # deepest module source file in the default bundles (measured)
BYTECODE = 25        # allowance for the bytecode name of that module file in a build view
SOURCE_OBJECT, SOURCE_BUILD, SOURCE_STAGE = (
    'module source in a source object', 'build view module file', 'staged checkout record')


def source_store(name=SOURCE_NAME, folder=SOURCE_FOLDER):
    return [
        # Python imports module source from here: Foundation's activator puts
        # the module folder of the object on sys.path.
        (SOURCE_OBJECT, FILE, f'{folder}\\objects\\{"f" * name}\\{HEX40}\\' + 'm' * MODULE_DEPTH),
        # A build backend reads the same file in the writable build view.
        (SOURCE_BUILD, FILE, f'{folder}\\builds\\{"f" * name}\\' + 'm' * (MODULE_DEPTH + BYTECODE)),
        # The one file in a staged checkout that the store opens by an ordinary path.
        (SOURCE_STAGE, FILE,
         f'{folder}\\objects\\{"f" * name}\\.source-{"r" * 8}\\checkout\\.amplifier_cache_meta.json'),
    ]


# The longest installation folder path at which a source-store row fits, for
# each row that does not fit at the setup limit. A new installation has no such
# row: every default module can be imported at MaxRootLength. Module source in
# an object is the installation folder path plus 194 there.
SOURCE_STORE_LONGEST_ROOT = {}
# Stated limit, not fixed: an installation that an earlier setup file made keeps
# the longer folder name, and module source is the folder path plus 205. Above
# the number here Python cannot import the deepest default module. Nothing moves
# that store. Only a new installation gets the short name: a second setup run
# on a finished installation opens it and keeps its store folder.
EARLIER_SOURCE_STORE_LONGEST_ROOT = {SOURCE_OBJECT: 54}
# The fixed folder with the longest account name that Windows allows locally.
ROOT_FOR_20_CHARACTER_ACCOUNT = len('C:\\Users\\' + 'u' * 20 + '\\AppData\\Local\\Amplifier')


# Paths that neither the app nor a chat opens by an ordinary path. Only these
# may pass the limit, and the native tree check lists each one that does.
# - ``.git``: Git's own data in a staged repository. The store reaches it by
#   extended-length paths, and Git runs with its own long-path setting.
# - ``tests``, ``test``: the tests of a repository or of an installed package.
#   No bundle file, module source, context file or skill is there. The build
#   of a module reads the copy in the build view, which is 42 characters shorter.
# - ``__pycache__``: bytecode. Python writes it when it can and uses the
#   source file when it cannot.
NOT_NEEDED_FOLDERS = frozenset({'.git', 'tests', 'test', '__pycache__'})


def needed(relative):
    """True unless the path is in a folder that nothing opens by an ordinary path (NOT_NEEDED_FOLDERS)."""
    return not NOT_NEEDED_FOLDERS & set(relative.lower().split('\\'))


def test_source_store_limits_are_stated_not_hidden():
    def longest_roots(folder):
        rows = {name: (kind, relative) for name, kind, relative in source_store(folder=folder)}
        measured = {name: max(root for root in range(1, MAX_ROOT + 1) if fits(kind, relative, root))
                    for name, (kind, relative) in rows.items()}
        return {name: root for name, root in measured.items() if root < MAX_ROOT}

    rows = {name: (kind, relative) for name, kind, relative in source_store()}
    assert length(rows[SOURCE_OBJECT][1]) - MAX_ROOT - MODULE_DEPTH == 69             # \s\objects\<16>\<40>\
    assert length(rows[SOURCE_BUILD][1]) - MAX_ROOT - MODULE_DEPTH - BYTECODE == 27  # \s\builds\<16>\
    # A new installation: every row fits at the longest folder path that setup accepts.
    assert longest_roots(SOURCE_FOLDER) == SOURCE_STORE_LONGEST_ROOT == {}
    assert all(fits(kind, relative) for kind, relative in rows.values())
    assert max(length(relative) for _, relative in rows.values()) == 253
    # The deepest default module may grow by this many characters before the row fails.
    assert FILE_LIMIT - length(rows[SOURCE_OBJECT][1]) == 6
    # An installation with the earlier folder name: stated, and unchanged.
    earlier = {name: (kind, relative) for name, kind, relative in source_store(folder=EARLIER_SOURCE_FOLDER)}
    assert length(earlier[SOURCE_OBJECT][1]) - MAX_ROOT - MODULE_DEPTH == 80         # \source-store\objects\<16>\<40>\
    assert longest_roots(EARLIER_SOURCE_FOLDER) == EARLIER_SOURCE_STORE_LONGEST_ROOT
    # There an account name of 20 characters fits in every row, with one character to spare.
    assert ROOT_FOR_20_CHARACTER_ACCOUNT == 53
    assert max(length(relative, 53) for _, relative in earlier.values()) == FILE_LIMIT - 1
    # An entry that an earlier Foundation made under its 64-digit name stays
    # in use where it is. Its module source has no budget at the setup limit.
    assert not fits(*source_store(name=64)[0][1:]) and not fits(*source_store(name=64)[0][1:], 53)


def test_source_store_names_are_the_ones_foundation_writes():
    from amplifier_foundation.modules import activator
    from amplifier_foundation.sources import shared
    assert shared._SHORT_NAME == SOURCE_NAME
    text = inspect.getsource(shared)
    assert '_store_entry(self.root / "objects", source_id(url, ""), revision)' in text
    assert '_store_entry(store / "builds", key)' in text
    assert 'tempfile.mkdtemp(prefix=".source-", dir=target.parent)' in text
    assert 'stage = staging / "checkout"' in text and 'stage / ".amplifier_cache_meta.json"' in text
    # The path that the limit above rests on: module source is imported from the object.
    assert 'path_str = str(module_path)' in inspect.getsource(activator)
    assert 'sys.path.insert(0, path_str)' in inspect.getsource(activator)


# The deepest file of a default repository that is not in ``.git`` or in a
# ``tests`` folder (measured): a document of the skills repository. Nothing
# opens it in a chat; it is the longest store path that is counted as needed.
STORE_DEPTH = 127
# The deepest file of a default repository at all (measured): a test fixture of
# the context-intelligence repository. It passes the limit from an installation
# folder path of 51. NOT_NEEDED_FOLDERS says why that is accepted.
STORE_TEST_DEPTH = 140
# What setup and the app install below the installation folder: the deepest
# file of each part, in characters below the folder. Measured in a clean-room
# installation (release 0.105.0, after an in-app update and a chat). The
# design document states the same numbers. The native tree case
# (``tests/test_windows_chat_setup.py``) builds the parts of BUILT_BY_THE_TREE_CASE
# and fails when one of them is deeper than its number here.
MEASURED_DEPTHS = {
    'frozen worker environment': 194,   # app\runtime\q-<32 hex>\.venv\...: bytecode; the source file is 180
    'uv cache': 189,                    # cache\git-v1\checkouts\...: a copy of the fixture above
    'first worker environment': 187,    # app\runtime\<16 hex>\.venv\...: bytecode; the source file is 162
    'app generation': 171,              # app\updates\a\<32 hex>\tools\...
    'private Python': 142,
    'tools': 124,
    'private Git': 90,                  # the full Git for Windows
}
# The parts that the native tree case builds itself, and where each one is below the installation folder.
BUILT_BY_THE_TREE_CASE = {
    'frozen worker environment': r'app\\runtime\\q-[0-9a-f]{32}\\',
    'first worker environment': r'app\\runtime\\[0-9a-f]{16}\\',
    'uv cache': r'cache\\',
}


def test_measured_application_depths_fit_under_the_longest_root():
    design = (ROOT / 'docs/design/native-windows-installation.md').read_text(encoding='utf-8')
    sentence = re.search(r'Worst\s+observed depths below the folder:(.*?)\.', design, re.S).group(1)
    stated = {' '.join(name.split()): int(depth)
              for name, depth in re.findall(r'([a-zA-Z][a-zA-Z \n]*?)\s+(\d+)(?:\s+characters)?(?:,|$)', sentence.strip())}
    assert stated == MEASURED_DEPTHS
    assert all(MAX_ROOT + depth <= FILE_LIMIT for depth in MEASURED_DEPTHS.values())
    # The deepest part leaves this many characters at the longest folder path, as the document says.
    assert FILE_LIMIT - MAX_ROOT - max(MEASURED_DEPTHS.values()) == 6
    assert re.search(r'longest\s+is\s+253\s+characters,\s+with\s+6\s+to\s+spare', design)
    # The source store: module source, and the deepest file that is counted as needed.
    assert (length('s\\objects\\' + 'f' * SOURCE_NAME + '\\' + HEX40 + '\\' + 'm' * STORE_DEPTH),
            length('s\\objects\\' + 'f' * SOURCE_NAME + '\\' + HEX40 + '\\' + 'm' * STORE_TEST_DEPTH)) == (255, 268)
    assert MODULE_DEPTH <= STORE_DEPTH < STORE_TEST_DEPTH
    for number in ('255', '268', '267', '254'):
        assert re.search(rf'\b{number}\b', design), number


def test_the_design_document_states_the_numbers_of_this_budget():
    """Each number in the document's budget section is the number that this file computes.

    A sentence is built from the computed number and must be in the document.
    So a wrong number in the document fails here, and so does a number that
    changes here while the document keeps the old one.
    """
    design = (ROOT / 'docs/design/native-windows-installation.md').read_text(encoding='utf-8')
    # From the measured depths (the paragraph before the budget section) to the end of the budget section.
    start = re.search(r'Worst\s+observed depths below the folder', design).start()
    budget = design.index('### Path budget below the installation folder', start)
    section = ' '.join(design[start:design.index('\n### ', budget + 1)].split())
    chat_files = f'app\\chats\\{ROOT_ID}\\files'
    session = {name: relative for name, kind, relative in session_store(CHAT_PROJECT, ROOT_ID)}
    root_rows = session_store(CHAT_PROJECT, ROOT_ID) + capture_store(CHAT_PROJECT, ROOT_ID) + ownership_store(ROOT_ID)
    stage = {name: relative for name, kind, relative in update_stage()}
    store = {name: relative for name, kind, relative in source_store()}
    earlier = {name: relative for name, kind, relative in source_store(folder=EARLIER_SOURCE_FOLDER)}
    object_prefix = length(f'{SOURCE_FOLDER}\\objects\\{"f" * SOURCE_NAME}\\{HEX40}\\') - MAX_ROOT
    build_prefix = length(f'{SOURCE_FOLDER}\\builds\\{"f" * SOURCE_NAME}\\') - MAX_ROOT
    fixture = MAX_ROOT + object_prefix + STORE_TEST_DEPTH          # ...\context-intelligence\metadata.json
    check_name = len(cli_project(MAX_ROOT, CHECK_FOLDER)) - MAX_ROOT

    def under_the_cli_name(root, tail=''):
        return length(f'{STAGE}\\shared-config\\projects\\{cli_project(root, CHECK_FOLDER)}{tail}', root)

    def without_the_stage_name(root):
        return max(size for size in range(1, 200) if all(
            fits(kind, relative, root) for name, kind, relative in update_stage('-' + 'p' * (size - 1))
            if name not in STAGE_NEVER_WRITTEN))

    limits, person, workspace = LONGEST_SESSION_ID, PERSONS_FOLDER_ROOM, DEFAULT_WORKSPACE_LONGEST_ROOT
    before = WITHOUT_THE_STAGE_NAME_DEFAULT_WORKSPACE_LONGEST_ROOT
    helper = HELPER_NAME_IN_THE_DEFAULT_WORKSPACE_FOLDER
    sentences = [
        f'module source is {length(store[SOURCE_OBJECT])} characters at {MAX_ROOT}, and the deepest other file that '
        f'counts as needed is {MAX_ROOT + object_prefix + STORE_DEPTH}.',
        f'a file path of at most {FILE_LIMIT} characters and creates a folder of at most {FOLDER_LIMIT}.',
        # The app's own chat folders.
        f'is the folder path plus {len(cli_project(MAX_ROOT, chat_files)) - MAX_ROOT} characters',
        f'was {length(session_store(cli_project(51, chat_files), ROOT_ID)[5][2], 51)} characters with an '
        f'18-character account name',
        f'`chat-<16 hex digits>` ({len(CHAT_PROJECT)} characters)',
        f'at {MAX_ROOT}: the longest path is {max(length(relative) for name, kind, relative in root_rows)}.',
        # Worker sessions in an app chat folder.
        f'At {MAX_ROOT} every path shape holds an ID of {WORKER_ID} characters.',
        f'the request observation ({limits["request observation"]}, root sessions only)',
        f'diagnostic copy ({limits["oversized-request copy"]};',
        f'background-job records ({limits["background-job record"]}), the saved view ({limits["view temporary"]}).',
        # A folder that a person chose.
        f'may be at most {KEPT_CHAT_ROOM} characters.',
        f'a path of {KEPT_CHAT_ROOM - MAX_ROOT} characters at {MAX_ROOT}, and of {KEPT_CHAT_ROOM - 34} at 34.',
        f'background-job records ({person["background-job record"]} in place of {KEPT_CHAT_ROOM}) and request '
        f'diagnostics ({person["request observation"]}).',
        f'is the folder path plus {len(chr(92) + "workspace")} characters.',
        f'fit only up to a folder path of {workspace["background-job record"]}, and its request diagnostics up to '
        f'{workspace["request observation"]}.',
        f'Its ID is {HELPER_ID} characters plus the agent\'s name',
        f'fits up to an agent name of {HELPER_NAME_AT_THE_LONGEST_ACCEPTED_FOLDER} characters.',
        f'up to a name of {helper[MAX_ROOT]} characters at a folder path of {MAX_ROOT}, of {helper[51]} at 51 and of '
        f'{helper[34]} at 34.',
        f'In an app chat folder it fits up to {HELPER_NAME_IN_AN_APP_CHAT_FOLDER} at {MAX_ROOT}.',
        # The update stage.
        f'(folder path + {length(STAGE) - MAX_ROOT}).',
        f'The CLI name of that folder is the folder path plus {check_name} characters',
        f'is twice the folder path plus {under_the_cli_name(MAX_ROOT, chr(92) + "sessions") - 2 * MAX_ROOT}, which '
        f'Windows creates only up to a folder path of '
        f'{max(root for root in range(1, 99) if under_the_cli_name(root, chr(92) + "sessions") <= FOLDER_LIMIT)}.',
        f'fits only up to '
        f'{max(r for r in range(1, 99) if under_the_cli_name(r, f"{chr(92)}sessions{chr(92)}{ROOT_ID}") <= FOLDER_LIMIT)})',
        f'({under_the_cli_name(51)} characters at a folder path of 51;',
        f'({len(UPDATE_PROJECT)} characters).',
        f'create is {STAGE_LONGEST[1]}, with {STAGE_SPARE} characters to spare; the probe\'s session folder is '
        f'{length(stage["probe session folder"])}.',
        f'(folder {length(stage["probe capture folder"])} at {MAX_ROOT}).',
        f'(the saved view, {length(stage["probe view temporary"])}, and a background-job record, '
        f'{length(stage["probe background-job record"])})',
        f'(longest path {length(BASELINE + chr(92) + "runtime-install-overrides.txt")}).',
        # A chat with its own setup.
        f'adds only shorter paths ({max(length(row[2]) for row in chat_setup_stage()[len(update_stage()):])} at '
        f'{MAX_ROOT}).',
        f'was twice the folder path plus {length(update_stage(cli_project(MAX_ROOT, "workspace"))[7][2]) - 2 * MAX_ROOT}'
        f' ({length(update_stage(cli_project(MAX_ROOT, "workspace"))[7][2])} at {MAX_ROOT})',
        f'only up to a folder path of {before["probe history temporary"]}.',
        f'at most {without_the_stage_name(MAX_ROOT)} characters at {MAX_ROOT}, {without_the_stage_name(51)} at 51 and '
        f'{without_the_stage_name(34)} at 34.',
        # The source store.
        f'path + {object_prefix} (`\\s\\objects\\<16>\\<40>\\`)',
        f'is {MODULE_DEPTH} characters (measured), so the path is folder path + {object_prefix + MODULE_DEPTH}: '
        f'**{length(store[SOURCE_OBJECT])} at {MAX_ROOT}**, with {FILE_LIMIT - length(store[SOURCE_OBJECT])} characters '
        f'to spare.',
        f'more than {FILE_LIMIT - length(store[SOURCE_OBJECT])} characters deeper',
        f'is {len(HEX64) - SOURCE_NAME} characters longer.',
        f'`metadata.json` is {fixture} characters, `events.jsonl` is {fixture - 1}, and the folder is '
        f'{fixture - len(chr(92) + "metadata.json")}.',
        f'Their depth in the repository is {STORE_TEST_DEPTH}.',
        f'At a folder path of 51 the two `metadata.json` files are {fixture - MAX_ROOT + 51}',
        f'is {object_prefix - build_prefix} characters shorter ({fixture - object_prefix + build_prefix}).',
        f'(depth {STORE_DEPTH}): {MAX_ROOT + object_prefix + STORE_DEPTH} at {MAX_ROOT}, with '
        f'{FILE_LIMIT - MAX_ROOT - object_prefix - STORE_DEPTH} characters to spare.',
        f'`source-store`, {len(EARLIER_SOURCE_FOLDER) - len(SOURCE_FOLDER)} characters longer.',
        f'path + {length(earlier[SOURCE_OBJECT]) - MAX_ROOT}.',
        f'a folder path of **{EARLIER_SOURCE_STORE_LONGEST_ROOT[SOURCE_OBJECT]}** and is '
        f'{length(earlier[SOURCE_OBJECT])} at {MAX_ROOT}.',
        f'limit of {EARLIER_SOURCE_STORE_LONGEST_ROOT[SOURCE_OBJECT]}.',
        # The native check.
        f'opens at a folder path of {MAX_ROOT} ({length(store[SOURCE_OBJECT])} characters).',
        f'it is refused at {MAX_ROOT} ({length(earlier[SOURCE_OBJECT])}) and opens at '
        f'{EARLIER_SOURCE_STORE_LONGEST_ROOT[SOURCE_OBJECT]} '
        f'({length(earlier[SOURCE_OBJECT], EARLIER_SOURCE_STORE_LONGEST_ROOT[SOURCE_OBJECT])}).',
        f'at {MAX_ROOT} a folder path of {KEPT_CHAT_ROOM - MAX_ROOT} characters keeps a chat, and at '
        f'{KEPT_CHAT_ROOM - MAX_ROOT + 1} Windows refuses the saved view.',
    ]
    assert session['view temporary'] == session_store(CHAT_PROJECT, ROOT_ID)[5][2]   # the row that index 5 names
    assert update_stage()[7][0] == 'probe session folder'                            # the row that index 7 names
    missing = [sentence for sentence in sentences if sentence not in section]
    assert not missing, missing


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
                            ('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', 'capture')):
        assert f'"{setting}": "{folder}"' in consumer


def test_stage_names_are_the_ones_the_updater_uses(tmp_path, monkeypatch):
    from amplifier_web import runtime_environment, session_files
    from amplifier_web.host.storage import SessionStore

    def below(path):
        return '\\'.join(Path(path).relative_to(tmp_path.resolve()).parts)

    app = tmp_path.resolve() / 'app'
    stage = runtime_environment.receipt_directory(app, HEX32)
    assert below(stage) == STAGE and below(runtime_environment.receipt_directory(app)) == BASELINE
    updates = (ROOT / 'amplifier_web/updates.py').read_text(encoding='utf-8')
    assert "stage=self.directory/'releases'/release" in updates and 'release=uuid.uuid4().hex' in updates
    assert "workspace = stage / 'qualification-workspace'" in updates
    assert "'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config')" in updates
    assert "shared_stage=stage/'shared-config'" in updates
    # The probe's own session store, with the settings that the updater gives the probe.
    workspace = stage / 'qualification-workspace'
    workspace.mkdir(parents=True)
    assert below(workspace) == CHECK_FOLDER
    monkeypatch.setattr(session_files, '_SHORT_CHAT_PROJECTS', True)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(stage))
    monkeypatch.setenv('AMPLIFIER_HOME', str(stage / 'shared-config'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    project = 'update-' + HEX32[:12]
    rows = {name: relative for name, kind, relative in update_stage(project)}
    assert session_files.project_slug(workspace) == project and len(project) == len(UPDATE_PROJECT)
    store = SessionStore.for_app(stage, workspace)
    assert below(store.directory(ROOT_ID)) == rows['probe session folder']
    assert below(session_files.capture_dir(workspace, ROOT_ID)) == rows['probe capture folder']
    # Names that the probe writes in its stage; the session rows are Foundation's and loop-live's.
    session = (ROOT / 'amplifier_web/host/session.py').read_text(encoding='utf-8')
    assert 'JobStore(store.base_dir / runtime.session_id / "live-jobs")' in session
    for name in ('runtime-policy-reconciliation.json', 'module-load-failures.json', 'runtime-reports'):
        assert any(name in path.read_text(encoding='utf-8') for path in (ROOT / 'amplifier_web').rglob('*.py')), name


def test_source_store_folder_names_are_the_ones_setup_and_the_launcher_use():
    from amplifier_web import windows_consumer
    assert (windows_consumer.SOURCE_STORE, windows_consumer.EARLIER_SOURCE_STORE) == (
        SOURCE_FOLDER, EARLIER_SOURCE_FOLDER)
    consumer = inspect.getsource(windows_consumer.environment)
    assert 'env["AMPLIFIER_SOURCE_STORE"] = str(source_store(root))' in consumer
    assert 'AMPLIFIER_SOURCE_STORE"' not in consumer.replace('env["AMPLIFIER_SOURCE_STORE"]', '')
    setup = (ROOT / 'scripts/windows_bootstrap_native.cs').read_text(encoding='utf-8')
    assert (f'internal const string SourceStore="{SOURCE_FOLDER}", '
            f'EarlierSourceStore="{EARLIER_SOURCE_FOLDER}";') in setup
    # Setup creates the short folder and gives the first installation step the same one.
    assert '"import",SourceStore,"workspace"' in setup
    assert 'env["AMPLIFIER_SOURCE_STORE"]=Path.Combine(root,SourceStore);' in setup
    assert '"source-store"' not in setup.replace('EarlierSourceStore="source-store"', '')


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
    lines += ['', 'Update stage and the one session of its compatibility probe. Path length, longest first',
              '("never written": the shape does not fit, and a probe does not write it):']
    for name, kind, relative in sorted(update_stage(), key=lambda row: -length(row[2])):
        note = '   never written' if name in STAGE_NEVER_WRITTEN else ''
        lines.append(f'  {name:38} {kind:6} +{len(relative) + 1:3} = {length(relative):3}{note}')
    for title, folder in ((f'Source store "{SOURCE_FOLDER}" (new installation)', SOURCE_FOLDER),
                          (f'Source store "{EARLIER_SOURCE_FOLDER}" (installation made by an earlier setup file)',
                           EARLIER_SOURCE_FOLDER)):
        lines += ['', f'{title}. Foundation, {SOURCE_NAME}-digit folder names. Path length, and the longest',
                  'installation folder path that fits:']
        for name, kind, relative in source_store(folder=folder):
            longest = FILE_LIMIT - 1 - len(relative)
            lines.append(f'  {name:34} {kind:6} +{len(relative) + 1:3} = {length(relative):3}   fits up to {longest}')
    return '\n'.join(lines)


if __name__ == '__main__':
    print(table())
