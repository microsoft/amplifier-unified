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
a person chose somewhere else. No model is called and no conversation worker
starts.

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
import json
import os
from pathlib import Path
import re
import shutil
import uuid

import pytest
import yaml

import test_windows_path_budget as budget
import test_windows_long_paths_off as native
from test_windows_long_paths_off import base, chosen_folder, installation, long_paths_off  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows path limits need native Windows')
REQUIRED = os.environ.get('AMPLIFIER_REQUIRE_CHAT_SETUP') == '1'
TREE_FILE = os.environ.get('AMPLIFIER_CHAT_SETUP_TREE')
# A dependency override in the person's folder: the same module from the same place, declared by the folder.
OVERRIDE = {'sources': {'modules': {'tool-todo': 'git+https://github.com/microsoft/amplifier-module-tool-todo@main'}}}
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
    from amplifier_web import runtime_profiles, update_diagnostics
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
    (chosen_folder / '.amplifier').mkdir()
    (chosen_folder / '.amplifier' / 'settings.yaml').write_text(yaml.safe_dump(OVERRIDE), encoding='utf-8')
    kept = {label: native.update_stage_probe.created(folder) for label, folder in folders.items()}

    # What each command printed, for a failure here only. The app keeps none of it.
    commands, facts = [], update_diagnostics.command_facts

    def observed(stdout, stderr, returncode, duration):
        commands.append((returncode, round(duration), stdout[-3000:].decode(errors='replace'),
                         stderr[-6000:].decode(errors='replace')))
        return facts(stdout, stderr, returncode, duration)

    monkeypatch.setattr(update_diagnostics, 'command_facts', observed)

    # The component generation that the installation has: an empty one, with the worker environment of setup.
    generation = uuid.uuid4().hex
    receipt = receipt_directory(home, generation)
    with directory(receipt / 'foundation', owned_root=home, create=True):   # as setup makes its stage
        pass
    project = prepare_project(home, generation)
    uv = shutil.which('uv')
    assert uv, 'The check needs uv.'
    selected, steps = {}, {}

    async def journey():
        await process(uv, 'lock', '--project', str(project), '--python', '3.13', timeout=1200)
        await process(uv, 'sync', '--locked', '--project', str(project), '--python', '3.13', timeout=1200)
        # No bundle is offered by this generation, so every chat has its own setup.
        write_private(receipt / 'profiles-qualified.json',
                      json.dumps({'profiles': [], 'configuration': 'synthetic-parent'}), owned_root=home)
        for label, folder in folders.items():
            steps[label] = []

            async def progress(detail, label=label):
                steps[label].append(detail)

            session = {'id': str(uuid.uuid4()), 'workspace': str(folder), 'bundle': 'work'}
            selected[label] = await runtime_profiles.ensure(home, generation, session, progress=progress)
            # The next chat with the same setup waits for nothing: the prepared setup is found again.
            again = {'id': str(uuid.uuid4()), 'workspace': str(folder), 'bundle': 'work'}
            assert await runtime_profiles.ensure(home, generation, again) == selected[label]

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

    assert len({generation, *selected.values()}) == 3   # each folder's setup is its own
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
        # The copy of the keys file is the person's file, and the check read it through the private-file reader.
        assert (stage / 'shared-config' / 'keys.env').read_text(encoding='utf-8') == KEYS
        # The check saved its one session under the stage's name, in the stage. The folder's path is in no name.
        assert os.listdir(stage / 'shared-config' / 'projects') == [project_name], label
        sessions = [path for path in (stage / 'shared-config' / 'projects' / project_name / 'sessions').iterdir()
                    if path.is_dir()]
        assert len(sessions) == 1 and (sessions[0] / 'transcript.jsonl').is_file()
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
    assert sorted(os.listdir(root / 'capture')) == sorted('update-' + value[:12] for value in selected.values())

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
