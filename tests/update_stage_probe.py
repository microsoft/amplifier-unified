"""An update stage with the updater's layout, and the app's real compatibility probe in it.

The probe is ``amplifier_web/update_probe.py``, unchanged, as a child process
with the command and the stage settings of ``UpdateManager.validate``. It mounts
real modules from the installed test dependencies and one provider that never
calls a model. Nothing is downloaded.

Differences from the probe of an installed app. They are why this is a check of
the probe's paths and not acceptance of an in-app update:

- The child runs in the test environment's Python, not in a frozen worker
  environment.
- It checks one offline profile, with no default behavior and no other bundle.
  The profiles of a real installation mount more modules.
- On Windows the stage folders are made by the app's private-folder creator
  (see ``build``); the updater uses an ordinary ``mkdir``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from amplifier_web import builtin_behaviors
from amplifier_web.host.config import PRECONFIGURED_BUNDLES
from amplifier_web.update_diagnostics import PROBE_PREFIX

STAGE_ID = '7260914d5698489dbbdd80d8a4c4ee40'
ROOT = Path(__file__).resolve().parents[1]
# Machine facts that a Windows child needs. No account folder and no credential.
WINDOWS_MACHINE = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT',
                   'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
PROVIDER = '''from amplifier_core import ProviderInfo, ModelInfo
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'fixture'
    def get_info(self):
        return ProviderInfo(id='fixture', display_name='Offline fixture',
                            capabilities=[], defaults={'model': 'fixture'})
    async def list_models(self):
        return [ModelInfo(id='fixture', display_name='Fixture', context_window=10000,
                          max_output_tokens=100, capabilities=[])]
    async def complete(self, *args, **kwargs):
        raise AssertionError('The update probe must not call a model')
    def parse_tool_calls(self, response):
        return []
async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(), name='fixture')
'''


@dataclass(frozen=True)
class Stage:
    app_home: Path
    stage: Path
    workspace: Path
    shared: Path
    profile: Path
    overrides: Path


@dataclass(frozen=True)
class Result:
    returncode: int
    facts: dict | None
    stdout: str
    stderr: str


def _installed(package):
    spec = importlib.util.find_spec(package)
    assert spec and spec.submodule_search_locations, f'The test environment lacks {package}.'
    return str(Path(next(iter(spec.submodule_search_locations))).resolve())


def build(app_home, fixtures, *, stage_id=STAGE_ID):
    """Make ``<app_home>/updates/releases/<stage_id>`` with the layout of an in-app update's stage.

    ``fixtures`` is an existing folder outside ``app_home``. The profile, the
    provider module and the overrides file go there, so the stage holds only
    what a real stage holds.
    """
    app_home, fixtures = Path(app_home), Path(fixtures)
    stage = app_home / 'updates' / 'releases' / stage_id
    shared = stage / 'shared-config'
    workspace = stage / 'qualification-workspace'
    if os.name == 'nt':
        # The probe checks that its app home, the stage, is a private folder of
        # this account. The updater makes the stage with an ordinary mkdir below
        # the private ``updates`` folder, and on a person's PC the check accepts
        # it (clean room, 0.98.5: the probe passed this check). On the CI runner
        # the check refused a stage that an ordinary mkdir made (PermissionError
        # in windows_files.validate_acl); the likely cause is that the runner's
        # account is elevated, so the folder gets another owner. The app's
        # private creator sets the owner explicitly. Setup makes its stage this
        # way too (installation_runtime.prepare).
        from amplifier_web.windows_files import directory
        for folder in (shared, workspace):
            with directory(folder, owned_root=app_home, create=True):
                pass
    else:
        (app_home / 'updates').mkdir(mode=0o700, exist_ok=True)   # UpdateManager.__init__
        stage.mkdir(parents=True, mode=0o700)                     # UpdateManager.install
        shared.mkdir(mode=0o700)                                  # update_storage.snapshot_shared_config
        workspace.mkdir()                                         # UpdateManager.validate
    provider = fixtures / 'amplifier_module_provider_update_fixture'
    provider.mkdir()
    (provider / '__init__.py').write_text(PROVIDER, encoding='utf-8')
    profile = fixtures / 'profile.yaml'
    profile.write_text(json.dumps({
        'bundle': {'name': 'fixture'},
        'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
        'providers': [{'module': 'provider-update-fixture', 'source': str(fixtures),
                       'config': {'default_model': 'fixture'}}],
    }), encoding='utf-8')
    from amplifier_web.deployment import write_private
    write_private(shared / 'settings.yaml', json.dumps({
        'bundle': {'active': 'fixture', 'added': {'fixture': str(profile)}},
        'web_bundles': {
            'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS),
            'entries': [{'name': name, 'uri': uri, 'role': 'standalone', 'enabled': False}
                        for name, uri in PRECONFIGURED_BUNDLES.items()],
        },
        'sources': {'modules': {
            'loop-live': _installed('amplifier_module_loop_live'),
            'context-simple': _installed('amplifier_module_context_simple'),
            # The real capture hook: the probe also writes below the capture folder.
            'hook-context-intelligence': _installed('amplifier_module_hook_context_intelligence'),
        }},
    }), owned_root=app_home)
    overrides = fixtures / 'install-overrides.txt'
    overrides.write_text('', encoding='utf-8')
    return Stage(app_home, stage, workspace, shared, profile, overrides)


# The same probe with the project names that it used before the update's check
# folder had its own name. Only for the reproduction of that failure: it loads
# the app package the way the probe does, turns the Windows naming rule off and
# then runs the unchanged probe file.
BEFORE_THE_FIX = '''import os, runpy, sys
probe = sys.argv[1]
sys.path.insert(0, os.path.dirname(probe))   # as "python update_probe.py" does
from runtime_bootstrap import bootstrap_app_package
bootstrap_app_package()
from amplifier_web import session_files
session_files._SHORT_CHAT_PROJECTS = False
sys.argv = [probe, *sys.argv[2:]]
runpy.run_path(probe, run_name='__main__')
'''


def run(stage, *, capture_root, source_store, state_home, timeout=300, before_the_fix=False):
    """Start the real probe with the updater's command and stage settings.

    The child gets a new environment: no setting of the calling process that
    names an Amplifier folder, and no credential.
    """
    fixtures = stage.profile.parent
    home, temporary = fixtures / 'home', fixtures / 'tmp'
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    environment = {'PATH': os.environ.get('PATH', os.defpath)}
    if os.name == 'nt':
        environment.update({name: os.environ[name] for name in WINDOWS_MACHINE if name in os.environ})
        for name, folder in (('USERPROFILE', home), ('LOCALAPPDATA', home / 'AppData' / 'Local'),
                             ('APPDATA', home / 'AppData' / 'Roaming')):
            folder.mkdir(parents=True, exist_ok=True)
            environment[name] = str(folder)
    environment.update({
        'HOME': str(home), 'TEMP': str(temporary), 'TMP': str(temporary), 'TMPDIR': str(temporary),
        # UpdateManager.validate: the stage is the probe's app home and holds its shared home.
        'AMPLIFIER_WEB_HOME': str(stage.stage), 'AMPLIFIER_HOME': str(stage.shared),
        'AMPLIFIER_UNIFIED_RELEASE': '', 'AMPLIFIER_INSTALL_PREPARATION': uuid.uuid4().hex,
        'AMPLIFIER_UPDATE_ATTEMPT': uuid.uuid4().hex,
        # Inherited from the app in a real update: the installation's own folders.
        'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH': str(capture_root),
        'AMPLIFIER_SOURCE_STORE': str(source_store),
        'AMPLIFIER_SESSION_STATE_HOME': str(state_home),
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        'UV_OFFLINE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
    })
    script = str(ROOT / 'amplifier_web' / 'update_probe.py')
    arguments = [str(stage.workspace), 'fixture', '--install-overrides', str(stage.overrides),
                 '--global-only', '--read-only']
    start = ['-c', BEFORE_THE_FIX, script] if before_the_fix else [script]
    completed = subprocess.run(
        [sys.executable, '-B', *start, *arguments],
        cwd=fixtures, env=environment, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    lines = [line[len(PROBE_PREFIX):] for line in completed.stdout.splitlines() if line.startswith(PROBE_PREFIX)]
    return Result(completed.returncode, json.loads(lines[-1]) if lines else None,
                  completed.stdout, completed.stderr)


def catalog_helper_call():
    """How the app starts its decision-catalog helper: the command, and the settings of ``subprocess.run``.

    The probe needs the catalog of decision adapters, as a conversation worker does. The first time, the app
    starts ``decision_account_catalog.py`` in a new interpreter, with a time limit
    (``decision_accounts._catalog_snapshot``). That helper loads every entry point of the group
    ``amplifier.decision_backends``, and so it imports the provider packages of the environment.

    This reads the call from the app's own function. For that one call the name ``subprocess`` in the module
    ``decision_accounts`` is a recorder: its ``run`` keeps the call and starts nothing, and every other name
    is the real module's. No other module sees the recorder, and the answer that the app keeps for this
    process is not touched.
    """
    import inspect
    from unittest import mock
    from amplifier_web import decision_accounts
    calls = []

    class Recorder:
        def __getattr__(self, name):
            return getattr(subprocess, name)

        def run(self, command, **settings):
            calls.append((list(command), settings))
            raise subprocess.SubprocessError('recorded, not started')

    with mock.patch.object(decision_accounts, 'subprocess', Recorder()):
        value, failure = inspect.unwrap(decision_accounts._catalog_snapshot)()
    assert value is None and failure['category'] == 'subprocess-error' and len(calls) == 1, (value, failure, calls)
    command, settings = calls[0]
    assert command[0] == sys.executable and settings.get('timeout', 0) > 0, (command, settings)
    # ``catalog_helper_times`` reads the helper's output as bytes from a pipe, as the app does.
    assert settings.get('stdout') is subprocess.PIPE and not any(
        settings.get(name) for name in ('text', 'universal_newlines', 'encoding', 'errors')), settings
    return command, settings


def catalog_helper_times(call, python, *, starts=2, limit=300):
    """Start the app's decision-catalog helper with ``python``; the seconds that each start took.

    ``call`` is ``catalog_helper_call()``. The interpreter and the time limit are the caller's, and the
    helper's error output is kept. The values of the settings (``PATH`` among them) and the current folder
    are those of the test process; the app's own start has those of its probe command. In a new
    environment the first start finds no bytecode of the provider packages. The helper runs with ``-I``, so
    ``PYTHONDONTWRITEBYTECODE`` does not reach it, and it can write bytecode. A later start can read that
    bytecode. Each start must print the catalog, a JSON object.

    The seconds are from before the call until the helper has ended. They include the creation of the
    process. The app's time limit counts from the moment at which the process exists. So a time a little
    over that limit does not prove that the app's own start would have reached it.

    A start that fails is the test's own start, not a command of the app. The failure says so, with the
    helper's error output, because a caller can run this inside a step of the app.
    """
    command, settings = call
    seconds = []
    for _ in range(starts):
        started = time.monotonic()
        try:
            done = subprocess.run([str(python), *command[1:]],
                                  **{**settings, 'timeout': limit, 'stderr': subprocess.PIPE})
        except (subprocess.SubprocessError, OSError) as error:
            output = (getattr(error, 'stderr', None) or b'')[-2000:].decode(errors='replace')
            raise AssertionError(
                f"The test's own start of the decision-catalog helper with {python} failed "
                f'({type(error).__name__}: {error}). It is not a command of the app. '
                f'Error output of the helper: {output or "none"}') from error
        seconds.append(time.monotonic() - started)
        try:
            catalog = json.loads(done.stdout)
        except ValueError:
            catalog = None
        assert isinstance(catalog, dict), (
            f"The test's own start of the decision-catalog helper with {python} printed no catalog: "
            f'{done.stdout[:200]!r}; error output of the helper: {done.stderr[-2000:]!r}')
    return seconds


HELPER_LINE = 'MEASURED the decision-catalog helper'
HELPER_LONGER = 'LONGER THAN THE TIME LIMIT OF THE APP'


def catalog_helper_line(environment, first, second, before, after, limit):
    """The line that a case prints for its own two starts of the helper in one environment.

    ``before`` and ``after`` are the numbers of bytecode files in the environment's packages before and after
    the first start. The CI job reads these lines (``.github/workflows/windows-path-length.yml``): it keeps
    each one on the run's page, and it warns for a line that ends with ``HELPER_LONGER``.
    """
    return (f'{HELPER_LINE} in the new worker environment {environment}: the test\'s first start took '
            f'{first:.2f} s; {after - before} more bytecode files were there after it ({before} before it); '
            f'the test\'s second start took {second:.2f} s; the app\'s time limit for one start is {limit} s'
            + (f'; {HELPER_LONGER}' if first > limit else ''))


def for_chat(stage, folder):
    """The same stage as the setup check of a chat uses it: the probe's workspace is the chat's folder.

    ``runtime_profiles.ensure`` gives the probe the stage settings of the
    updater and the folder of the chat. The stage keeps its own check folder
    here, unused.
    """
    return replace(stage, workspace=Path(folder))


def created(root):
    """Every entry below ``root``, relative, with '/' as the separator and after each folder."""
    root = Path(root)
    return sorted(path.relative_to(root).as_posix() + ('/' if path.is_dir() else '') for path in root.rglob('*'))


def cli_name(path):
    """The shared CLI project name, written out independently of the code under test."""
    name = str(Path(path).resolve()).replace('/', '-').replace('\\', '-').replace(':', '')
    return name if name.startswith('-') else '-' + name


def expected_project(stage):
    """The project name of the probe's workspace on this platform."""
    return 'update-' + stage.stage.name[:12] if os.name == 'nt' else cli_name(stage.workspace)


# The lock beside a session folder, held while the session record is written.
# On Windows the lock library removes its file after release, so it is not left
# there (seen on the native runner); it stays when Windows refuses the removal.
METADATA_LOCK = 'shared-config/projects/<project>/sessions/.<session>.metadata.lock'
# What one passing probe leaves in its stage, and below the capture folder.
STAGE_AFTER_A_PROBE = sorted([
    'foundation/', 'foundation/cache/', 'foundation/cache/install-state.json',
    'qualification-workspace/',
    'runtime-reports/', 'runtime-reports/<session>/',
    'runtime-reports/<session>/baseline-mount-plan.json', 'runtime-reports/<session>/live-mount-plan.json',
    'runtime-reports/<session>/module-load-failures.json', 'runtime-reports/<session>/mounted.json',
    'runtime-reports/<session>/provider-choices.json',
    'shared-config/', 'shared-config/settings.yaml', 'shared-config/projects/',
    'shared-config/projects/<project>/', 'shared-config/projects/<project>/sessions/',
    'shared-config/projects/<project>/sessions/<session>/',
    'shared-config/projects/<project>/sessions/<session>/live-jobs/',
    'shared-config/projects/<project>/sessions/<session>/live-jobs/owner.lock',
    'shared-config/projects/<project>/sessions/<session>/metadata.json',
    'shared-config/projects/<project>/sessions/<session>/transcript.jsonl',
] + ([] if os.name == 'nt' else [METADATA_LOCK]))
CAPTURE_AFTER_A_PROBE = [
    '<project>/', '<project>/sessions/', '<project>/sessions/<session>/',
    '<project>/sessions/<session>/context-intelligence/',
    '<project>/sessions/<session>/context-intelligence/events.jsonl',
    '<project>/sessions/<session>/context-intelligence/metadata.json',
]


def passed(result):
    assert result.returncode == 0 and result.facts, result.stdout + result.stderr
    assert result.facts['ok'] is True and result.facts['stage'] == 'complete', result.facts
    assert all(result.facts[name] is True for name in ('standalone', 'providersPresent', 'cliAbsent')), result.facts


def session_of(stage, project):
    """The ID of the one session that a probe made."""
    sessions = [path.name for path in (stage.shared / 'projects' / project / 'sessions').iterdir() if path.is_dir()]
    assert len(sessions) == 1 and str(uuid.UUID(sessions[0])) == sessions[0]
    return sessions[0]


def names(root, project, session):
    return sorted(name.replace(project, '<project>').replace(session, '<session>') for name in created(root))


def left_in_stage(stage, project, session):
    """Every entry of the stage, to compare with STAGE_AFTER_A_PROBE."""
    return [name for name in names(stage.stage, project, session) if os.name != 'nt' or name != METADATA_LOCK]
