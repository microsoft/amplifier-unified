"""Native Windows, long paths off: the app prepares and checks a chat that has its own setup.

A chat has its own setup when its bundle is not one of the offered ones, when
its folder declares dependency overrides, or when it has a saved plan (a fork
has one). ``runtime_profiles.ensure`` then prepares a worker environment for
that chat and checks it in an update stage, with the chat's folder as the
workspace of the check.

This file runs that whole preparation, unchanged, below an installation folder
of the longest path that setup supports, with the folders and settings that
the installed launcher gives the app. Real uv, the real worker dependencies,
the app's default components from their repositories, the real freeze and both
real probes. The chat is in the default workspace folder, and in a folder that
a person chose somewhere else. Each of the two folders declares a dependency
override. No model is called and no conversation worker starts.

Two chats with the same setup start at the same time: one prepares it and the
other waits for it. One preparation is cancelled while its first command runs,
as ``RuntimeManager.stop`` cancels it when a person stops the chat, and the
chat is then started again. The case reads the list of processes until it
finds none of the stopped preparation. That list is a sample, and the case
prints it as a measurement. ``tests/test_windows_stopped_command.py`` shows
with a controlled command and open process handles what a stop ends.

The app's decision-catalog helper is the first program that imports the
provider packages in a new worker environment. It finds no bytecode there, and
the app gives one start of it a time limit (``decision_accounts._catalog_snapshot``).
On a hosted runner that first start takes 2 to 6 seconds of the 10 that the
app allows. Twice it took longer, and the preparation then failed by timing
(pull request 2270, run 44143862317, two attempts). So this case starts the
helper itself, with the app's own command and settings, directly before the
app's first probe command in each new environment: twice, and it measures
both starts and counts the bytecode files that the first one wrote. The app's
own start then finds bytecode and has less to compile. It still has the app's
limit. This case therefore does not show that a first start is inside the
app's limit. It prints how long each first start took, the creation of the
process included, and the CI job keeps those times on the run's page and
warns when one was longer than the limit. The stop of a preparation comes
after the case's own starts of the helper: a stop while the helper compiles
is not shown. The times of the two probe phases that the case prints include
the case's two starts of the helper.

It then lists every path below the installation folder. That is the measured
answer to "which paths pass the Windows limit, and does the app need them":
the worker environments, the update stages, Foundation's source store with the
default components, and uv's cache. The setup file's own parts (the launcher
environment, the private Python, the private Git) are not in this tree.

It downloads several hundred megabytes, so it runs only where
``AMPLIFIER_REQUIRE_CHAT_SETUP`` is set: the CI job
``.github/workflows/windows-path-length.yml``. There a skip is a failure.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import pytest
import yaml

import test_windows_path_budget as budget
import test_windows_long_paths_off as native
from test_windows_long_paths_off import base, chosen_folder, installation, long_paths_off  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows path limits need native Windows')
REQUIRED = os.environ.get('AMPLIFIER_REQUIRE_CHAT_SETUP') == '1'
TREE_FILE = os.environ.get('AMPLIFIER_CHAT_SETUP_TREE')
# A dependency override that each folder declares: a module of the default bundle, from its own repository, at
# a fixed revision. The two folders override different modules, so each has a setup of its own.
OVERRIDES = {
    'default workspace folder': {'sources': {'modules': {
        'tool-exec': 'git+https://github.com/microsoft/amplifier-module-tool-exec@f267548ccd1b38a236a46afb96252bb6dd474dcd'}}},
    'folder that a person chose': {'sources': {'modules': {
        'tool-filesystem':
            'git+https://github.com/microsoft/amplifier-module-tool-filesystem@663f26a878ae71e136f439fa73ded52b2eb24174'}}},
}
KEYS = 'AMPLIFIER_FIXTURE_VALUE=fixture-not-a-secret\n'


def tree(root):
    """Every entry below ``root``: (length of its ordinary path, 'file' or 'folder', path below root).

    Reads through the extended-length form, so an entry over the limit is listed and not lost.
    """
    rows, prefix = [], len(native.EXTENDED) + len(str(root)) + 1
    pending = [native.EXTENDED + str(root)]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                ordinary = entry.path[len(native.EXTENDED):]
                folder = entry.is_dir(follow_symlinks=False)
                rows.append((len(ordinary), budget.FOLDER if folder else budget.FILE, entry.path[prefix:]))
                if folder:
                    pending.append(entry.path)
    return sorted(rows, key=lambda row: (-row[0], row[2]))


def processes():
    """Every process on this PC: {process ID: (parent process ID, name, command line)}."""
    script = ('[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process | ForEach-Object { '
              '[pscustomobject]@{ p = [int]$_.ProcessId; q = [int]$_.ParentProcessId; n = [string]$_.Name; '
              'l = [string]$_.CommandLine } } | ConvertTo-Json -Compress')
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                          capture_output=True, timeout=180, check=True)
    rows = json.loads(done.stdout.decode('utf-8-sig', errors='replace').strip())
    return {row['p']: (row['q'], row['n'], row['l']) for row in (rows if isinstance(rows, list) else [rows])}


def of_attempt(snapshot, identity, known):
    """The processes of one preparation that run now: {process ID: name}.

    A process belongs to the preparation when the preparation's ID is in its command line (the app's commands
    name the stage, ``--profiles <stage>\\profiles.json``, and the project, ``--project ...\\prepare-<ID>``),
    or when it descends from such a process. ``known`` keeps every member that an earlier list had, so a process
    is still found after its parent ended.
    """
    members = {pid for pid, (parent, name, line) in snapshot.items() if identity in line or (pid, name) in known}
    while True:
        parents = members | {pid for pid, name in known}
        more = {pid for pid, (parent, name, line) in snapshot.items() if parent in parents} - members
        if not more:
            break
        members |= more
    known |= {(pid, snapshot[pid][1]) for pid in members}
    return {pid: snapshot[pid][1] for pid in sorted(members)}


def over_the_limit(rows):
    return [row for row in rows if row[0] > (budget.FOLDER_LIMIT if row[1] == budget.FOLDER else budget.FILE_LIMIT)]


def area(relative):
    parts = relative.split('\\')
    return '\\'.join(parts[:2]) if parts[0] in ('app', budget.SOURCE_FOLDER) and len(parts) > 2 else parts[0]


def test_a_chat_with_its_own_setup_is_prepared_and_checked_under_the_longest_installation_folder(
        installation, chosen_folder, monkeypatch):  # noqa: F811
    if not REQUIRED:
        pytest.skip('Downloads the worker dependencies and the default components; the CI job opts in.')
    from fixtures.manual_app_handoff_native import token_user_default_owner
    from amplifier_web import runtime_profiles, update_diagnostics, updates
    from amplifier_web.deployment import write_private
    from amplifier_web.runtime_environment import prepare_project, project_path, receipt_directory
    from amplifier_web.updates import process
    from amplifier_web.windows_files import directory

    install = installation(budget.MAX_ROOT)
    root, home, shared = install.root, install.root / 'app', install.root / 'shared'
    assert len(str(root)) == budget.MAX_ROOT == 59
    # The folders and the settings that the installed launcher gives the app (windows_consumer.environment).
    for name in ('workspace', 'temp', 'cache'):
        with directory(root / name, owned_root=root, create=True):
            pass
    for setting, value in (('TEMP', root / 'temp'), ('TMP', root / 'temp'), ('UV_CACHE_DIR', root / 'cache'),
                           ('UV_NO_CONFIG', '1'), ('PYTHONUTF8', '1'), ('PYTHONDONTWRITEBYTECODE', '1'),
                           ('AMPLIFIER_UNIFIED_RELEASE', '')):
        monkeypatch.setenv(setting, str(value))
    # One AI connection that is never used, and a keys file: the stage gets a copy of it.
    write_private(shared / 'settings.yaml', yaml.safe_dump({
        'bundle': {'active': 'work'},
        'config': {'providers': [{'module': 'provider-openai', 'config': {
            'api_key': 'sk-fixture-never-used', 'default_model': 'fixture-no-inference'}}]},
    }), owned_root=root)
    write_private(shared / 'keys.env', KEYS, owned_root=root)
    folders = {'default workspace folder': root / 'workspace', 'folder that a person chose': chosen_folder}
    for label, folder in folders.items():
        (folder / '.amplifier').mkdir()
        (folder / '.amplifier' / 'settings.yaml').write_text(yaml.safe_dump(OVERRIDES[label]), encoding='utf-8')
    kept = {label: native.update_stage_probe.created(folder) for label, folder in folders.items()}

    # What each command printed, for a failure here only. The app keeps none of it.
    commands, facts = [], update_diagnostics.command_facts

    def observed(stdout, stderr, returncode, duration):
        commands.append((returncode, round(duration), stdout[-3000:].decode(errors='replace'),
                         stderr[-6000:].decode(errors='replace')))
        return facts(stdout, stderr, returncode, duration)

    monkeypatch.setattr(update_diagnostics, 'command_facts', observed)

    # The case's own two starts of the decision-catalog helper, directly before the app's first probe command
    # in a new worker environment (see the top of this file). The call is read from the app, before any
    # other thread of this process starts a program.
    helper = native.update_stage_probe.catalog_helper_call()
    helper_limit, helper_times, app_commands, app_process = helper[1]['timeout'], {}, set(), updates.process

    def helper_in(environment):
        """Two starts of the helper: (seconds of the first, seconds of the second, bytecode files of the
        environment's packages before the first start, and after it)."""
        python, packages = environment / '.venv' / 'Scripts' / 'python.exe', environment / '.venv' / 'Lib' / 'site-packages'

        def bytecode():
            return sum(1 for size, kind, relative in tree(packages) if relative.lower().endswith('.pyc'))

        before = bytecode()
        first, = native.update_stage_probe.catalog_helper_times(helper, python, starts=1)
        after = bytecode()
        second, = native.update_stage_probe.catalog_helper_times(helper, python, starts=1)
        return first, second, before, after

    async def process_after_the_helper(*args, **settings):
        if '--project' in args and any(Path(str(value)).name == 'update_probe.py' for value in args):
            environment = Path(args[args.index('--project') + 1])
            if environment.name not in helper_times:
                helper_times[environment.name] = await asyncio.to_thread(helper_in, environment)
            app_commands.add(environment.name)   # from here on the app's own command runs in it
        return await app_process(*args, **settings)

    monkeypatch.setattr(updates, 'process', process_after_the_helper)

    # The component generation that the installation has: an empty one, with the worker environment of setup.
    generation = uuid.uuid4().hex
    receipt = receipt_directory(home, generation)
    with directory(receipt / 'foundation', owned_root=home, create=True):   # as setup makes its stage
        pass
    project = prepare_project(home, generation)
    uv = shutil.which('uv')
    assert uv, 'The check needs uv.'
    selected, steps, stopped = {}, {}, {}
    releases, index = home / 'updates' / 'releases', home / 'updates' / 'profiles'

    def chat(folder):
        return {'id': str(uuid.uuid4()), 'workspace': str(folder), 'bundle': 'work'}

    def reporter(target):
        async def progress(detail):
            target.append(detail)
        return progress

    def keys_copy(stage):
        """The stage's copy of the keys file, read as the app reads it: the private-file reader refuses another
        owner, a link and a wider access list."""
        with directory(stage / 'shared-config', owned_root=stage / 'shared-config', create=False) as pinned:
            return pinned.read_private('keys.env', limit=1024 * 1024).decode('utf-8')

    async def two_chats_at_the_same_time(label, folder):
        """Two chats with the same setup start together: one prepares it, the other waits and uses it."""
        before = {path.name for path in releases.iterdir()}
        reports = ([], [])
        first, second = await asyncio.gather(*(
            runtime_profiles.ensure(home, generation, chat(folder), progress=reporter(target)) for target in reports))
        assert first == second and {path.name for path in releases.iterdir()} - before == {first}
        steps[label], waited = sorted(reports, key=len, reverse=True)
        assert waited == ['Checking this chat’s setup…', 'Waiting for this chat’s setup to finish…'], waited
        return first

    async def stopped_and_started_again(label, folder):
        """The preparation is cancelled while its first command runs (a stop of the chat), then started again."""
        before = {path.name for path in releases.iterdir()}
        steps[label] = []
        session, progress = chat(folder), reporter(steps[label])
        task = asyncio.create_task(runtime_profiles.ensure(home, generation, session, progress=progress))
        running, made, known, deadline = {}, set(), set(), time.monotonic() + 900
        while not running:
            assert not task.done() and time.monotonic() < deadline, f'The preparation ended before the stop: {task}'
            await asyncio.sleep(1)
            made = {path.name for path in releases.iterdir()} - before
            # The stop is for the app's own command, not for the case's starts of the helper before it.
            if (len(made) == 1 and steps[label][-1:] == ['Checking this chat’s tools…']
                    and 'prepare-' + next(iter(made)) in app_commands):
                running = of_attempt(await asyncio.to_thread(processes), next(iter(made)), known)
        stage = releases / next(iter(made))
        attempt = json.loads((stage / 'profile-attempt.json').read_text(encoding='utf-8'))
        assert (attempt['status'], attempt['phase']) == ('preparing', 'dependency-prepare'), attempt
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        at = time.monotonic()
        attempt = json.loads((stage / 'profile-attempt.json').read_text(encoding='utf-8'))
        assert (attempt['status'], attempt['errorType']) == ('cancelled', 'CancelledError'), attempt
        assert (attempt['timings'][-1]['phase'], attempt['timings'][-1]['status']) == ('dependency-prepare', 'cancelled')
        def not_admitted():
            """Nothing admits the stopped attempt: no record in its stage, and no index entry that names it."""
            return (not (stage / 'validated.json').exists() and not (stage / 'profiles-qualified.json').exists()
                    and all(json.loads(path.read_text(encoding='utf-8'))['generation'] != stage.name
                            for path in index.glob('*.json')))

        assert not_admitted()
        # The app ends the command that it started. What that command started can run on
        # (tests/test_windows_stopped_command.py). This list is a sample of it, printed and not required.
        left = of_attempt(await asyncio.to_thread(processes), stage.name, known)
        after = dict(left)
        # The person sends again at once: the lock is free, the stopped stage is not used, and a new one is
        # prepared beside it. Whether a program of the stopped command still runs then is not required.
        again = asyncio.create_task(runtime_profiles.ensure(home, generation, session, progress=progress))
        while left:   # read the list until it holds none
            assert time.monotonic() - at < 900, f'Processes of the stopped preparation still run: {left}'
            await asyncio.sleep(2)
            left = of_attempt(await asyncio.to_thread(processes), stage.name, known)
        ended = time.monotonic() - at
        # Also after its last process nothing admits the stopped attempt. Its copy of the keys file stays, and it
        # is still private. Nothing was written into the chat's folder.
        assert not_admitted() and keys_copy(stage) == KEYS
        assert native.update_stage_probe.created(folder) == kept[label], label
        stopped[label] = stage.name
        new = await again
        assert new != stage.name and {path.name for path in releases.iterdir()} - before == {stage.name, new}
        assert not_admitted()
        print(f'MEASURED a stop during the preparation ({label}): {len(running)} processes of it ran at the stop '
              f'({", ".join(sorted(set(running.values())))}); {len(after)} were listed directly after the stop '
              f'({", ".join(sorted(set(after.values()))) or "none"}); the chat was started again directly after that '
              f'list; the list (a sample) held no process of the stopped preparation {ended:.0f} s after the stop. '
              f'The attempt is '
              f'recorded as cancelled, nothing admits it, and its copy of the keys file is private. The second '
              f'start prepared the setup in a new stage.')
        return new

    async def journey():
        await process(uv, 'lock', '--project', str(project), '--python', '3.13', timeout=1200)
        await process(uv, 'sync', '--locked', '--project', str(project), '--python', '3.13', timeout=1200)
        # No bundle is offered by this generation, so every chat has its own setup.
        write_private(receipt / 'profiles-qualified.json',
                      json.dumps({'profiles': [], 'configuration': 'synthetic-parent'}), owned_root=home)
        for (label, folder), start in zip(folders.items(), (two_chats_at_the_same_time, stopped_and_started_again)):
            selected[label] = await start(label, folder)
            # The next chat with the same setup waits for nothing: the prepared setup is found again.
            assert await runtime_profiles.ensure(home, generation, chat(folder)) == selected[label]

    try:
        # A hosted runner's account is elevated: a new folder would belong to the Administrators group, and the
        # app's private-folder check refuses that owner. On a person's PC the account owns what it makes; setup
        # refuses an elevated account. This gives new files of this process and its children that same owner.
        with token_user_default_owner():
            asyncio.run(journey())
    except BaseException as error:
        print(f'FAILED with {type(error).__name__}: {error}; facts {getattr(error, "diagnostic_facts", None)}')
        for attempt in sorted(home.glob('updates/releases/*/profile-attempt.json')):
            print('ATTEMPT', attempt.parent.name, attempt.read_text(encoding='utf-8'))
        for returncode, duration, stdout, stderr in commands[-3:]:
            print(f'COMMAND exit {returncode} after {duration} s\nSTDOUT {stdout}\nSTDERR {stderr}')
        raise
    finally:
        for name, (first, second, before, after) in helper_times.items():
            print(f'MEASURED the decision-catalog helper in the new worker environment {name}: first start '
                  f'{first:.1f} s, and it wrote {after - before} bytecode files ({before} were there before it); '
                  f'second start {second:.1f} s; the app\'s time limit for one start is {helper_limit} s'
                  + ('; LONGER THAN THE APP\'S TIME LIMIT' if first > helper_limit else ''))

    assert len({generation, *selected.values()}) == 3   # each folder's setup is its own
    # The case started the helper in every environment in which the app then ran a probe command, and in no
    # other: for each setup the preparation environment and the frozen one, and the preparation environment
    # of the stopped attempt. Each later start by the app was inside the app's limit: every probe command
    # that was not stopped ended well.
    new_environments = {'prepare-' + stopped['folder that a person chose']}
    for value in selected.values():
        new_environments |= {'prepare-' + value, project_path(home, value).name}
    assert set(helper_times) == app_commands == new_environments, (sorted(helper_times), sorted(app_commands))
    # The first start added bytecode files to the environment's packages. A later start can read them and
    # has less to compile; the measured second starts show how much less. A first start that adds no
    # bytecode file fails here: the case's own starts of the helper are then of no use.
    for name, (first, second, before, after) in helper_times.items():
        assert after > before, (name, before, after)
    for label, folder in folders.items():
        stage = receipt_directory(home, selected[label])
        project_name = 'update-' + selected[label][:12]
        attempt = json.loads((stage / 'profile-attempt.json').read_text(encoding='utf-8'))
        assert attempt['status'] == 'succeeded', attempt
        phases = {row['phase']: row['status'] for row in attempt['timings']}
        assert set(phases.values()) == {'succeeded'} and {
            'source-snapshot', 'runtime-prepare', 'installer-policy', 'dependency-prepare', 'runtime-freeze',
            'compatibility-probe', 'recorded-verification'} <= set(phases), phases
        assert steps[label][0] == 'Checking this chat’s setup…' and steps[label][-1] == 'Verifying this chat’s setup…'
        for name in ('validated.json', 'profiles-qualified.json', 'profiles.json', 'runtime-install-overrides.txt'):
            assert (stage / name).is_file(), name
        assert json.loads((stage / 'profiles-qualified.json').read_text(encoding='utf-8'))['profiles'] == ['work']
        # The copy of the keys file is the person's file, and it is private. The check read it the same way.
        assert (stage / 'shared-config' / 'keys.env').read_text(encoding='utf-8') == KEYS == keys_copy(stage)
        # The check saved its one session under the stage's name, in the stage. The folder's path is in no name.
        assert os.listdir(stage / 'shared-config' / 'projects') == [project_name], label
        sessions = [path for path in (stage / 'shared-config' / 'projects' / project_name / 'sessions').iterdir()
                    if path.is_dir()]
        assert len(sessions) == 1 and (sessions[0] / 'transcript.jsonl').is_file()
        # That session is the check of THIS chat's folder: its record names the folder.
        record = json.loads((sessions[0] / 'metadata.json').read_text(encoding='utf-8'))
        assert os.path.normcase(record.get('working_dir') or '') == os.path.normcase(str(folder)), record
        rows = {name: budget.length(relative) for name, kind, relative in budget.update_stage(project_name, sessions[0].name)}
        assert len(str(sessions[0])) == rows['probe session folder'] == 202
        # The worker environment that a chat with this setup starts in.
        worker = project_path(home, selected[label])
        assert worker.parent == home / 'runtime' and worker.name.startswith('q-') and (worker / '.venv').is_dir()
        # Nothing was written into the chat's folder, and the preparation folder is gone.
        assert native.update_stage_probe.created(folder) == kept[label], label
        assert not (home / 'runtime' / ('prepare-' + selected[label])).exists()
        print(f'MEASURED installation folder {budget.MAX_ROOT}, {label} ({len(str(folder))} characters): the chat\'s '
              f'own setup was prepared and checked as generation {selected[label]}; phases '
              f'{json.dumps({row["phase"]: round(row["elapsedMs"] / 1000) for row in attempt["timings"]})} (seconds); '
              f'check session folder {len(str(sessions[0]))}; worker environment {worker.name}')
    # Nothing was saved in the person's own history, and the check's capture is under the stage names only.
    assert not (shared / 'projects').exists()
    checked = {'update-' + value[:12] for value in selected.values()}
    assert checked <= set(os.listdir(root / 'capture')) <= checked | {'update-' + value[:12] for value in stopped.values()}
    assert list(stopped) == ['folder that a person chose']

    # Every path below the installation folder, as it now is.
    rows = tree(root)
    if TREE_FILE:
        Path(TREE_FILE).write_text(
            f'# installation folder path length {len(str(root))}; long paths off; <length> TAB <kind> TAB <path below '
            f'the installation folder>\n' + ''.join(f'{size}\t{kind}\t{relative}\n' for size, kind, relative in rows),
            encoding='utf-8')
    files = [row for row in rows if row[1] == budget.FILE]
    over = over_the_limit(rows)
    print(f'MEASURED tree below an installation folder of {len(str(root))} characters, long paths off: '
          f'{len(files)} files, {len(rows) - len(files)} folders; over the limit: '
          f'{sum(1 for row in over if row[1] == budget.FILE)} files (limit {budget.FILE_LIMIT}), '
          f'{sum(1 for row in over if row[1] == budget.FOLDER)} folders (limit {budget.FOLDER_LIMIT})')
    for size, kind, relative in over[:40]:
        print(f'OVER THE LIMIT {size} {kind} {relative}')
    longest = {}
    for size, kind, relative in rows:
        longest.setdefault((area(relative), kind, budget.needed(relative)), (size, relative))
    for (name, kind, needed), (size, relative) in sorted(longest.items(), key=lambda item: -item[1][0]):
        print(f'LONGEST {size:3} {kind:6} {"needed" if needed else "not needed":10} {name}: {relative}')
    # The parts that this case builds are not deeper than the budget's measured depths, which the design
    # document states. A deeper file fails here: decide then, and change the number and the document together.
    for name, place in budget.BUILT_BY_THE_TREE_CASE.items():
        deepest = max(size for size, kind, relative in files if re.match(place, relative, re.I)) - len(str(root))
        print(f'MEASURED deepest file of the {name}: {deepest} characters below the installation folder '
              f'(the budget states {budget.MEASURED_DEPTHS[name]})')
        assert deepest <= budget.MEASURED_DEPTHS[name], name
    # The deepest file of the frozen worker environment that is not bytecode, which the document states too.
    source = max(size for size, kind, relative in files
                 if re.match(budget.BUILT_BY_THE_TREE_CASE['frozen worker environment'], relative, re.I)
                 and '__pycache__' not in relative.lower().split('\\')) - len(str(root))
    print(f'MEASURED deepest file of the frozen worker environment that is not bytecode: {source} characters below '
          f'the installation folder (the budget states {budget.FROZEN_ENVIRONMENT_SOURCE_DEPTH})')
    assert source <= budget.FROZEN_ENVIRONMENT_SOURCE_DEPTH
    # The stopped preparation left its project, under the longer name that a preparation has until its freeze.
    left_project = re.compile(r'app\\runtime\\prepare-' + stopped['folder that a person chose'] + r'\\', re.I)
    deepest = max(size for size, kind, relative in files if left_project.match(relative)) - len(str(root))
    print(f'MEASURED deepest file of the project that the stopped preparation left: {deepest} characters below the '
          f'installation folder (the budget states {budget.PREPARATION_PROJECT_DEPTH})')
    assert deepest <= budget.PREPARATION_PROJECT_DEPTH
    # The same for the source store: the deepest file that counts as needed, and the deepest file at all.
    store = re.compile(re.escape(budget.SOURCE_FOLDER) + r'\\objects\\[0-9a-f]{16}\\[0-9a-f]{40}\\(.+)', re.I)
    inside = [(len(found.group(1)), budget.needed(relative)) for size, kind, relative in files
              if (found := store.match(relative))]
    deepest_needed, deepest = max(size for size, needed in inside if needed), max(size for size, needed in inside)
    print(f'MEASURED deepest file of a staged repository: {deepest_needed} characters inside the repository for a '
          f'file that counts as needed (the budget states {budget.STORE_DEPTH}), {deepest} for any file (the budget '
          f'states {budget.STORE_TEST_DEPTH})')
    assert deepest_needed <= budget.STORE_DEPTH and deepest <= budget.STORE_TEST_DEPTH
    # No path that the app or a chat needs passes the limit. Windows refused nothing above: every step ended well.
    assert [row for row in over if budget.needed(row[2])] == []
    # What happens to each path over the limit. The source store wrote it, through the extended-length form.
    # An ordinary path does not open such a file. Windows creates no folder over its limit by an ordinary
    # path (see long_paths_off), and it still reads one that exists while the path is inside the file limit.
    for size, kind, relative in over:
        assert relative.startswith(budget.SOURCE_FOLDER + '\\objects\\'), relative
        assert os.path.lexists(native.EXTENDED + str(root / relative))
        assert os.path.lexists(root / relative) == (kind == budget.FOLDER and size <= budget.FILE_LIMIT), relative
    print('MEASURED each path over the limit exists, written by the source store through extended-length paths; '
          'an ordinary path opens none of the files')
    # The same files in the two places from which a program reads them by an ordinary path are inside the limit:
    # the build view of a module (42 characters shorter) and uv's own copy.
    for name in (budget.SOURCE_FOLDER + '\\builds', 'cache', 'app\\runtime', 'app\\updates', 'capture'):
        assert not [row for row in over if area(row[2]) == name], name
