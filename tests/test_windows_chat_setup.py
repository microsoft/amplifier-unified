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
prints it as a measurement. A process is one of the preparation by its ID
and its creation time, not by its ID alone (``of_attempt``): Windows gives
the ID of an ended process to a new one. Once the case waited 900 seconds for
the programs of a sign-in session (run 44168885490, attempt 1).
``tests/test_windows_stopped_command.py`` shows with a controlled command and
open process handles what a stop ends.

In a new worker environment the app's decision-catalog helper finds no
bytecode of the provider packages (in each run so far this case counted 0
bytecode files before its first start). The helper imports those packages,
and the app gives one start of it a time limit
(``decision_accounts._catalog_snapshot``). On a hosted runner most first
starts take 2 to 6 seconds of the app's 10. When the app's own start was the
first one, it reached the limit twice, and the preparation then failed by
timing (pull request 2270, run 44143862317, two attempts). So this case
starts the helper itself, with the app's own command, directly before the
app's first probe command in each new environment: twice.
It measures both starts and counts the bytecode files before and after the
first one. The app's own start then finds bytecode and has less to compile.
It still has the app's limit. Of the first 55 first starts that this case
measured, one took longer than that limit (11.56 s, run 44170978228); the
case passed, and the run's page has a warning.

What this case therefore does not show:

- That a first start of the helper is inside the app's limit. The case prints
  how long its own first start took, the creation of the process included.
  The CI job keeps those times on the run's page and warns when one was longer
  than the limit.
- A probe command of the app in an environment without that bytecode. The
  times of the two probe phases that the case prints include the case's two
  starts of the helper.
- A stop while the helper compiles those packages for the first time. The
  stop of a preparation comes after the case's own starts of the helper.

The case requires that its first start added bytecode files. A first start
that adds none fails there, for a reason that is not a path limit. Possible
causes, for example: a preparation already wrote all of that bytecode; the
helper writes none; the bytecode is in another folder (the case counts below
``.venv\\Lib\\site-packages`` of the environment). Find out which it is. If
the environment already has the bytecode, the case's own starts of the helper
are of no use: remove them.

It then lists every path below the installation folder. That is the measured
answer to "which paths pass the Windows limit, and does the app need them":
the worker environments, the update stages, Foundation's source store with the
default components, and uv's cache. The setup file's own parts (the launcher
environment, the private Python, the private Git) are not in this tree.

It downloads several hundred megabytes, so it runs only where
``AMPLIFIER_REQUIRE_CHAT_SETUP`` is set: the CI job
``.github/workflows/windows-path-length.yml``. There a skip is a failure.
The cases of ``of_attempt`` with a made-up list of processes run on every
system.
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
import sys
import time
import uuid

import pytest
import yaml

import test_windows_path_budget as budget
import test_windows_long_paths_off as native
from test_windows_long_paths_off import base, chosen_folder, installation, long_paths_off  # noqa: F401  (fixtures)

# The cases with a made-up list of processes run on every system. The others need native Windows.
windows = pytest.mark.skipif(os.name != 'nt', reason='Windows path limits need native Windows')
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
    """Every process on this PC, and when the list was read:
    ({process ID: (parent process ID, name, command line, created)}, read).

    ``created`` is the time at which Windows created the process. ``read`` is a time after the whole list was
    read. Both are of the PC's clock, in units of 100 nanoseconds. A process for which Windows gives no
    creation time has 0.
    """
    # The last row is not a process: it has the time after the list was read.
    script = ('[Console]::OutputEncoding = [Text.Encoding]::UTF8; & { '
              'Get-CimInstance Win32_Process | ForEach-Object { '
              '$c = [long]0; if ($_.CreationDate) { $c = $_.CreationDate.ToUniversalTime().Ticks }; '
              '[pscustomobject]@{ p = [int]$_.ProcessId; q = [int]$_.ParentProcessId; n = [string]$_.Name; '
              'l = [string]$_.CommandLine; c = $c } }; '
              '[pscustomobject]@{ p = -1; q = -1; n = [string]::Empty; l = [string]::Empty; '
              'c = [DateTime]::UtcNow.Ticks } } | ConvertTo-Json -Compress')
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                          capture_output=True, timeout=180, check=True)
    rows = json.loads(done.stdout.decode('utf-8-sig', errors='replace').strip())
    assert rows[-1]['p'] == -1 and all(row['p'] >= 0 for row in rows[:-1]), rows[-3:]
    return {row['p']: (row['q'], row['n'], row['l'], row['c']) for row in rows[:-1]}, rows[-1]['c']


def of_attempt(listed, identity, known):
    """The processes of one preparation that run now: {process ID: name}.

    ``listed`` is what ``processes()`` returned. A process belongs to the preparation when the preparation's
    ID is in its command line (the app's commands name the stage, ``--profiles <stage>\\profiles.json``, and
    the project, ``--project ...\\prepare-<ID>``), or when it descends from such a process. ``known`` keeps
    the members that the lists before this one gave, so a process is still found after its parent ended.

    Windows keeps the ID of a process's parent after that parent ended, and it gives the ID of an ended
    process to a new one. So an equal ID does not make a member. Microsoft's text for ``ParentProcessId`` of
    ``Win32_Process`` says to use the creation time to tell whether the process with that ID was created
    after the process that names it. ``known`` is {(ID, creation time): (parent's ID, ended)}. The parent's
    ID is ``None`` for a member by its command line. ``ended`` is ``None`` when the newest list holds the
    member; otherwise it is a time before which the member ended.

    The rule rests on this: a list holds every process that ran from before the list was read until after
    it. That is not from a text of Microsoft. The native case below reads the real list four times and
    requires its two programs in each.

    - A member is the same process in a later list only with that ID and that creation time.
    - A list without a member shows that the member ended before that list was read. A list that holds
      another process with the member's ID shows that the member ended before that process was created.
      The earliest such time counts. A list that holds the member again shows that it runs.
    - A process is a child of a member only when the member had the parent's ID at the moment at which the
      process was created: the process was not created before the member, and it was created before the
      member's ``ended``, when there is one.
    - Each list decides again for every process, from the members by their command line. A process that an
      earlier list took for a child of a member is no member, and what descends from it is none, from the
      list that shows that the member ended at or before the creation of that process.
    - Without a creation time (Windows gave none: 0) the rule knows only the ID. A row without one, with
      the ID of a member that runs, is taken for that member. A member without one that runs is taken for
      the process that has its ID in a later list. A process without one is not excluded as a child, and
      its name gets "(creation time not known)". A member without one takes every process that names its
      ID and was created before its ``ended``.

    What stays open:

    - A member ends, another program gets its ID, starts a program and ends, all before the first list
      without the member is read: the program that it started counts as a child of the member.
    - A list does not hold a member that runs, against what the rule rests on: what that member starts
      after that list was read is not found, unless a later list holds the member again.
    - Two processes can have the same creation time. A process that gets the ID of an ended member with the
      creation time of that member is taken for the member. A child that a member started at the creation
      time of the process that got the member's ID later is not found.
    - The times are of the PC's clock: a clock that is set back while a member or its child runs can hide
      the child.
    """
    snapshot, read = dict(listed[0]), listed[1]
    # Without a creation time the rule knows only the ID.
    running = {pid: created for (pid, created), (parent, ended) in known.items() if ended is None}
    for pid, (parent, name, line, created) in list(snapshot.items()):
        since = running.get(pid)
        if since and not created:
            snapshot[pid] = parent, name, line, since   # the row is taken for the member that runs
        elif created and since == 0:
            known[pid, created] = known.pop((pid, 0))   # the member that runs is taken for this process
    for (pid, created), (parent, ended) in list(known.items()):
        holder = snapshot.get(pid)   # the process that has the member's ID in this list
        if holder is None:
            moment = read   # the member ended before this list was read
        elif holder[3] == created:
            moment = None   # the list holds the member: it runs
        elif not holder[3]:
            continue   # a row without a creation time shows nothing about a member that ended
        else:
            # Another process has the ID: the member ended before that process was created. A creation time
            # that is not later than the member's gives no such time: then, before this list was read.
            moment = holder[3] if holder[3] > created else read
        known[pid, created] = parent, moment if None in (ended, moment) else min(ended, moment)
    ended = {key: value[1] for key, value in known.items()}
    parents = {key: value[0] for key, value in known.items()}
    parents.update({(pid, created): parent for pid, (parent, name, line, created) in snapshot.items()})
    by_line = {key for key, value in known.items() if value[0] is None} | {
        (pid, created) for pid, (parent, name, line, created) in snapshot.items() if identity in line}

    def child_of_a_member(parent, created):
        return any(member == parent and (not created or since <= created)
                   and (ended.get((member, since)) is None or created < ended[member, since])
                   for member, since in members)

    members = set(by_line)
    while True:
        more = {key for key, parent in parents.items()
                if key not in members and child_of_a_member(parent, key[1])}
        if not more:
            break
        members |= more
    known.clear()
    known.update({key: (None if key in by_line else parents[key], ended.get(key)) for key in members})
    return {pid: row[1] + ('' if row[3] else ' (creation time not known)')
            for pid, row in sorted(snapshot.items()) if (pid, row[3]) in members}


def session_programs(listed):
    """One line for each program that Windows starts for a sign-in session, in a list of ``processes()``.

    The process that started ``csrss.exe`` or ``winlogon.exe`` can have ended. Windows then still gives its
    ID as the parent's ID. The line says what has that ID now.
    """
    snapshot, read = listed
    lines = []
    for pid, (parent, name, line, created) in sorted(snapshot.items()):
        if name.lower() in ('csrss.exe', 'winlogon.exe'):
            holder = snapshot.get(parent)
            if holder is None:
                has = 'no process'
            elif 0 in (created, holder[3]):
                has = f'{holder[1]}, and one of the two creation times is not known'
            elif holder[3] <= created:
                has = f'{holder[1]}, not created after it'
            else:
                has = f'{holder[1]}, created {(holder[3] - created) / 10_000_000:.0f} s after it'
            age = (f'created {(read - created) / 10_000_000:.0f} s before this list' if created
                   else 'creation time not known')
            lines.append(f'{name} {pid}, {age}, parent ID {parent}: {has}')
    return lines


def first_line(program, seconds):
    """The first line that ``program`` prints. '' when it printed none in ``seconds``, and when its output
    ended before a line. After a '' the reader of this call still waits for the program's output: the caller
    ends the program, and that ends the reader."""
    import threading
    lines = []
    reader = threading.Thread(target=lambda: lines.append(program.stdout.readline()), daemon=True)
    reader.start()
    reader.join(seconds)
    return lines[0].decode(errors='replace').strip() if lines else ''


ATTEMPT = 'c0ffee0123456789c0ffee0123456789'   # the ID of a made-up preparation
PROBE = f'python update_probe.py --profiles C:\\i\\app\\updates\\releases\\{ATTEMPT}\\profiles.json'


def made_up(read, *rows):
    """A made-up list as ``processes()`` returns it; a row is (process ID, parent's ID, name, created[, command
    line])."""
    return {row[0]: (row[1], row[2], row[4] if len(row) > 4 else '', row[3]) for row in rows}, read


def test_the_programs_of_a_sign_in_session_are_not_processes_of_a_preparation_that_got_their_parents_id():
    """Seen once (run 44168885490, attempt 1): the case waited 900 s for these four programs to end.

    The session manager process that started ``csrss.exe`` and ``winlogon.exe`` ended long ago. Windows still
    gives its ID as their parent's ID. Here a program of the preparation gets that ID.
    """
    session = [(7656, 7600, 'csrss.exe', 100), (7720, 7600, 'winlogon.exe', 101),
               (7800, 7720, 'fontdrvhost.exe', 102), (7824, 7720, 'dwm.exe', 103)]
    known = {}
    command = (4000, 3000, 'uv.exe', 900, f'uv run --project C:\\i\\app\\runtime\\prepare-{ATTEMPT} python')
    assert of_attempt(made_up(1000, *session, command, (7600, 4000, 'python.exe', 905)), ATTEMPT, known) == {
        4000: 'uv.exe', 7600: 'python.exe'}
    # The preparation is stopped and its programs end. The session's programs run on, with 7600 as their parent.
    assert of_attempt(made_up(1010, *session), ATTEMPT, known) == {}
    assert of_attempt(made_up(1020, *session), ATTEMPT, known) == {}


def test_the_line_of_a_session_program_says_what_has_its_parents_id_now():
    second = 10_000_000   # the list's times are in units of 100 nanoseconds
    listed = made_up(1000 * second, (500, 4, 'smss.exe', 1 * second), (640, 500, 'csrss.exe', 2 * second),
                     (7656, 7600, 'csrss.exe', 100 * second), (7720, 7604, 'winlogon.exe', 101 * second),
                     (7600, 4000, 'python.exe', 905 * second), (7800, 7720, 'dwm.exe', 103 * second),
                     (8120, 7600, 'csrss.exe', 0), (8168, 9000, 'winlogon.exe', 104 * second),
                     (9000, 4, 'smss.exe', 0))
    assert session_programs(listed) == [
        'csrss.exe 640, created 998 s before this list, parent ID 500: smss.exe, not created after it',
        'csrss.exe 7656, created 900 s before this list, parent ID 7600: python.exe, created 805 s after it',
        'winlogon.exe 7720, created 899 s before this list, parent ID 7604: no process',
        'csrss.exe 8120, creation time not known, parent ID 7600: python.exe, and one of the two creation times '
        'is not known',
        'winlogon.exe 8168, created 896 s before this list, parent ID 9000: smss.exe, and one of the two creation '
        'times is not known']


def test_a_program_that_gets_the_id_of_an_ended_member_is_not_that_member_and_what_it_starts_is_no_member():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'python.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'python.exe'}
    assert of_attempt(made_up(1010), ATTEMPT, known) == {}   # the member ended
    # Another program has the ID now. The same name does not make it the member. It starts a program.
    other, started = (4010, 600, 'python.exe', 1015, 'python -m another_program'), (5000, 4010, 'conhost.exe', 1016)
    assert of_attempt(made_up(1020, other, started), ATTEMPT, known) == {}
    # Also after the other program ended: the member ended before the list of 1010 was read.
    assert of_attempt(made_up(1030, started), ATTEMPT, known) == {}


def test_what_names_the_id_of_an_ended_member_and_was_created_after_the_list_without_it_is_no_child():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'python.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'python.exe'}
    assert of_attempt(made_up(1010), ATTEMPT, known) == {}   # the member ended before this list was read
    # No list held the program that got the member's ID after that. What it started is no child of the member.
    assert of_attempt(made_up(1020, (5000, 4010, 'conhost.exe', 1012)), ATTEMPT, known) == {}


def test_a_child_of_the_program_that_has_the_id_now_is_not_a_child_of_the_member_that_had_it():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'python.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'python.exe'}
    # Between two lists the member ended, another program got its ID (created 1006) and started a program (1007).
    started = (5000, 4010, 'conhost.exe', 1007)
    assert of_attempt(made_up(1010, (4010, 600, 'cmd.exe', 1006), started), ATTEMPT, known) == {}
    # Also after that other program ended: a list showed that the member ended before 1006.
    assert of_attempt(made_up(1020, started), ATTEMPT, known) == {}
    # And when a third program has the ID (created 1025): the earliest time stays.
    assert of_attempt(made_up(1030, (4010, 700, 'git.exe', 1025), started), ATTEMPT, known) == {}


def test_a_program_with_the_creation_time_of_the_program_that_got_the_members_id_is_not_a_child_of_the_member():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'python.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'python.exe'}
    # The program that got the ID and the program that names the ID have the same creation time.
    assert of_attempt(made_up(1010, (4010, 600, 'cmd.exe', 1006), (5000, 4010, 'conhost.exe', 1006)),
                      ATTEMPT, known) == {}


def test_a_program_that_a_list_took_for_a_child_is_no_member_when_a_later_list_shows_what_had_the_id():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'python.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'python.exe'}
    # This list has no process with the member's ID: the member ended before 1012. The two programs were
    # created before that, and nothing shows that they are not the member's.
    taken, below = (5000, 4010, 'conhost.exe', 1009), (5100, 5000, 'cmd.exe', 1011)
    assert of_attempt(made_up(1012, taken, below), ATTEMPT, known) == {5000: 'conhost.exe', 5100: 'cmd.exe'}
    # The next list holds another program with that ID, created before those two: the member ended before 1008.
    assert of_attempt(made_up(1020, (4010, 600, 'cmd.exe', 1008), taken, below), ATTEMPT, known) == {}
    assert of_attempt(made_up(1030, taken, below), ATTEMPT, known) == {}


def test_a_list_that_holds_a_member_again_shows_that_it_runs():
    member, child = (4010, 4000, 'uv.exe', 905, PROBE), (5000, 4010, 'python.exe', 906, 'python setup.py')
    known = {}
    assert of_attempt(made_up(1000, member, child), ATTEMPT, known) == {4010: 'uv.exe', 5000: 'python.exe'}
    assert of_attempt(made_up(1010, member), ATTEMPT, known) == {4010: 'uv.exe'}   # the list did not hold the child
    # A later list holds the child again, and a program that the child started after the list without it.
    assert of_attempt(made_up(1020, member, child, (5100, 5000, 'git.exe', 1015)), ATTEMPT, known) == {
        4010: 'uv.exe', 5000: 'python.exe', 5100: 'git.exe'}


def test_what_a_member_starts_after_a_list_that_did_not_hold_it_is_not_found():
    """The rule rests on this: a list holds every process that runs. Here one list does not. This case holds
    what the rule then gives; it is the second open thing of ``of_attempt``."""
    member, child = (4010, 4000, 'uv.exe', 905, PROBE), (5000, 4010, 'python.exe', 906, 'python setup.py')
    known = {}
    assert of_attempt(made_up(1000, member, child), ATTEMPT, known) == {4010: 'uv.exe', 5000: 'python.exe'}
    assert of_attempt(made_up(1010, member), ATTEMPT, known) == {4010: 'uv.exe'}   # the list did not hold the child
    # The child ran. It started a program after that list was read. Then the member and the child ended.
    assert of_attempt(made_up(1020, (5100, 5000, 'git.exe', 1015)), ATTEMPT, known) == {}


def test_without_a_creation_time_the_rule_knows_only_the_id():
    known = {}
    orphan = (7656, 5000, 'csrss.exe', 100)   # it names the ID 5000, which a process had that ended long ago
    assert of_attempt(made_up(1000, (4010, 4000, 'uv.exe', 905, PROBE), orphan), ATTEMPT, known) == {4010: 'uv.exe'}
    # The member ended. Its child runs on with the ID 5000, and Windows gives no creation time for it. So
    # nothing shows that the orphan is older than the child.
    assert of_attempt(made_up(1010, (5000, 4010, 'python.exe', 0), orphan), ATTEMPT, known) == {
        5000: 'python.exe (creation time not known)', 7656: 'csrss.exe'}
    # The next list has the child's creation time. The orphan is older, and what the child started is found.
    assert of_attempt(made_up(1020, (5000, 4010, 'python.exe', 1004), (5100, 5000, 'git.exe', 1015), orphan),
                      ATTEMPT, known) == {5000: 'python.exe', 5100: 'git.exe'}
    # A member without a creation time ends, and later another program has its ID: that one is not the member,
    # and what it starts is no child of the member.
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'uv.exe', 0, PROBE)), ATTEMPT, known) == {
        4010: 'uv.exe (creation time not known)'}
    assert of_attempt(made_up(1010), ATTEMPT, known) == {}
    assert of_attempt(made_up(1020, (4010, 600, 'cmd.exe', 1015), (5000, 4010, 'conhost.exe', 1016)),
                      ATTEMPT, known) == {}


def test_a_row_without_a_creation_time_with_the_id_of_a_member_that_runs_is_that_member():
    known = {}
    assert of_attempt(made_up(1000, (4010, 4000, 'uv.exe', 905, PROBE)), ATTEMPT, known) == {4010: 'uv.exe'}
    # One list gives no creation time for the member. That does not show that the member ended.
    assert of_attempt(made_up(1010, (4010, 4000, 'uv.exe', 0, PROBE)), ATTEMPT, known) == {4010: 'uv.exe'}
    child = (5000, 4010, 'python.exe', 1015)
    assert of_attempt(made_up(1020, (4010, 4000, 'uv.exe', 905, PROBE), child), ATTEMPT, known) == {
        4010: 'uv.exe', 5000: 'python.exe'}
    assert of_attempt(made_up(1030, child), ATTEMPT, known) == {5000: 'python.exe'}


def test_another_process_with_the_members_id_and_no_later_creation_time_shows_that_the_member_ended_before_the_read():
    known = {}
    member, child = (4010, 4000, 'uv.exe', 905, PROBE), (5000, 4010, 'python.exe', 950)
    assert of_attempt(made_up(1000, member, child), ATTEMPT, known) == {4010: 'uv.exe', 5000: 'python.exe'}
    # Another process has the member's ID, with a creation time before the member's (the clock was set back).
    # The member's child stays.
    other = (4010, 600, 'cmd.exe', 800)
    assert of_attempt(made_up(1010, other, child), ATTEMPT, known) == {5000: 'python.exe'}
    # What names that ID and was created after that list was read is no child of the member.
    assert of_attempt(made_up(1020, other, child, (5100, 4010, 'conhost.exe', 1015)), ATTEMPT, known) == {
        5000: 'python.exe'}


def test_the_wait_for_the_first_line_of_a_program_ends_when_the_program_prints_none():
    programs = []

    def start(code):
        programs.append(subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE))
        return programs[-1]

    try:
        assert first_line(start('import sys; print(7, flush=True); sys.stdin.read()'), 120) == '7'
        assert first_line(start('pass'), 120) == ''   # it ended and printed nothing
        silent, started = start('import sys; sys.stdin.read()'), time.monotonic()
        assert first_line(silent, 1) == '' and time.monotonic() - started < 60   # it runs on and prints nothing
    finally:
        for program in programs:
            program.stdin.close()
            program.wait(120)
            program.stdout.close()


def test_a_process_of_the_preparation_is_still_found_after_its_parent_ended():
    known = {}
    # The child has no ID of the preparation in its command line. It can have the creation time of its parent.
    member, child = (4010, 4000, 'uv.exe', 905, PROBE), (5000, 4010, 'python.exe', 905, 'python setup.py')
    assert of_attempt(made_up(1000, member, child), ATTEMPT, known) == {4010: 'uv.exe', 5000: 'python.exe'}
    # The member ended. Its child runs on and has a child. A program that the member started after the first
    # list and before it ended is found too: nothing else has had the member's ID since.
    grandchild, late = (5100, 5000, 'git.exe', 1005), (5200, 4010, 'conhost.exe', 1004)
    assert of_attempt(made_up(1010, child, grandchild, late), ATTEMPT, known) == {
        5000: 'python.exe', 5100: 'git.exe', 5200: 'conhost.exe'}
    # Later lists find a member by its own ID and creation time, and what it starts.
    assert of_attempt(made_up(1020, late, (5300, 5200, 'git.exe', 1015)), ATTEMPT, known) == {
        5200: 'conhost.exe', 5300: 'git.exe'}
    assert of_attempt(made_up(1030), ATTEMPT, known) == {}


@windows
def test_the_real_list_has_the_creation_time_of_each_process_and_finds_what_a_started_program_starts():
    """The real list of this PC, with a program that this case starts. That program starts a second one,
    which has nothing of the first one in its command line: it is found only as a descendant. Both run until
    their input ends. This case ends that input. Only the first program has the output pipe to this case."""
    token = uuid.uuid4().hex
    second = 'import sys; sys.stdin.read()'
    first = ("import subprocess, sys; "
             f"started = subprocess.Popen([sys.executable, '-c', {second!r}], stdin=sys.stdin, "
             "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
             "print(started.pid, flush=True); sys.stdin.read(); started.wait()")
    program = subprocess.Popen([sys.executable, '-c', first, token], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        answer = first_line(program, 120)   # the first program prints the ID of the second one
        assert answer.isdigit(), f'The first program did not print the ID of a second one in 120 s: {answer!r}'
        descendant = int(answer)
        known, deadline = {}, time.monotonic() + 600
        while True:
            snapshot, read = processes()
            found = of_attempt((snapshot, read), token, known)
            if descendant in found:
                break
            assert time.monotonic() < deadline, f'No list held the second program {descendant}: {found}'
            time.sleep(1)
        assert program.pid in snapshot, f'The list with the second program did not hold the first one: {found}'
        mine, started = snapshot[os.getpid()], snapshot[program.pid]
        # The program names this process as its parent. It was created after this process and before the read.
        assert started[0] == os.getpid() and token in started[2], started
        assert 0 < mine[3] <= started[3] <= read, (mine[3], started[3], read)
        assert program.pid in found and os.getpid() not in found, found
        # The second program has nothing of the first one in its command line.
        assert token not in snapshot[descendant][2], snapshot[descendant]
        # Each process that is found only as a descendant was created not before the member that started it.
        for pid in found:
            parent, name, line, created = snapshot[pid]
            assert token in line or parent in found and snapshot[parent][3] <= created, (pid, snapshot[pid])
        # What the rule rests on: a list holds every process that runs. Each of the next three lists holds
        # the processes that were found, with the same creation times, and they are found again.
        for _ in range(3):
            again, later = processes()
            assert set(found) <= set(again), (
                f'A list did not hold a program of this case that runs: {sorted(set(found) - set(again))}')
            assert all(again[pid][3] == snapshot[pid][3] for pid in found) and read <= later, (found, read, later)
            assert set(found) <= set(of_attempt((again, later), token, known))
        unknown = sorted(row[1] for row in snapshot.values() if not row[3])
        print(f'MEASURED the real list of processes: {len(snapshot)} processes, {len(unknown)} of them with no '
              f'creation time{" (" + ", ".join(unknown) + ")" if unknown else ""}; the program that this case '
              f'started and what it started are {len(found)} processes '
              f'({", ".join(sorted(set(found.values())))}), '
              f'{sum(1 for pid in found if token not in snapshot[pid][2])} found only as a descendant; each of '
              f'the next 3 lists held them all')
        print('MEASURED the programs of the sign-in sessions of this PC: '
              + '; '.join(session_programs((snapshot, read))))
    finally:
        program.stdin.close()   # the input of both programs ends; the first one waits for the second one
        try:
            program.wait(60)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(program.pid), '/T', '/F'], capture_output=True, timeout=60)
            program.wait(60)
        program.stdout.close()


def over_the_limit(rows):
    return [row for row in rows if row[0] > (budget.FOLDER_LIMIT if row[1] == budget.FOLDER else budget.FILE_LIMIT)]


def area(relative):
    parts = relative.split('\\')
    return '\\'.join(parts[:2]) if parts[0] in ('app', budget.SOURCE_FOLDER) and len(parts) > 2 else parts[0]


@windows
def test_a_chat_with_its_own_setup_is_prepared_and_checked_under_the_longest_installation_folder(
        installation, chosen_folder, monkeypatch):  # noqa: F811
    if not REQUIRED:
        pytest.skip('Downloads the worker dependencies and the default components; the CI job opts in.')
    from fixtures.manual_app_handoff_native import token_user_default_owner
    from amplifier_web import runtime_profiles, update_diagnostics, update_storage, updates
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
    helper_failures = {}

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
                try:
                    helper_times[environment.name] = await asyncio.to_thread(helper_in, environment)
                except Exception as error:   # the app's step fails with it; keep its text for this log.
                    # A cancelled wait is no failure of the helper: CancelledError is not an Exception.
                    helper_failures[environment.name] = f'{type(error).__name__}: {error}'
                    raise
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
        running, made, known, deadline = {}, set(), {}, time.monotonic() + 900
        while not running:
            assert not task.done() and time.monotonic() < deadline, f'The preparation ended before the stop: {task}'
            await asyncio.sleep(0.2)
            made = {path.name for path in releases.iterdir()} - before
            # The stop is for the app's own command, not for the case's starts of the helper before it. That
            # command has less to do now, so the list of processes is read as soon as the command is started.
            # While the list does not hold the command, it is read again 0.2 s after the read before.
            if (len(made) == 1 and steps[label][-1:] == ['Checking this chat’s tools…']
                    and 'prepare-' + next(iter(made)) in app_commands):
                running = of_attempt(await asyncio.to_thread(processes), next(iter(made)), known)
        stage = releases / next(iter(made))
        attempt = json.loads((stage / 'profile-attempt.json').read_text(encoding='utf-8'))
        assert (attempt['status'], attempt['phase']) == ('preparing', 'dependency-prepare'), attempt
        # While the preparation runs, its stage holds a copy of the keys file. (Not opened here: a program of
        # the preparation can read it at this moment.)
        copy = stage / 'shared-config' / 'keys.env'
        assert copy.is_file()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        at = time.monotonic()
        # The stop removes that copy (runtime_profiles._profile_attempt). The app made the stage and its
        # shared-config folder with an ordinary mkdir and the copy with an ordinary copy; the removal goes
        # through the private-file boundary from the app's home folder down. A copy that is still there
        # stays for idle maintenance. This case does not show why a copy is still there: Windows refuses
        # the removal while a program has the file open, and programs of the stopped command run at this
        # moment, but the case sees only that the file exists. That a stop removes the copy from such a
        # stage is required, with no program in the way, in tests/test_update_stage_keys.py (the native
        # cases of a chat's own setup).
        removed_at_the_stop = not copy.exists()
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
        # Also after its last process nothing admits the stopped attempt, and its copy of the keys file is gone.
        # When the stop removed it, this shows that no program of the stopped command made it again. When
        # the copy was still there at the stop, the same removal must work now (idle maintenance makes it
        # for a stage whose record says "cancelled"); the case then shows only that this removal works.
        # Nothing was written into the chat's folder.
        if not removed_at_the_stop:
            reason = update_storage.remove_stage_keys(home, stage)
            assert reason is None, f'After the last process the removal of the stage\'s copy answered {reason!r}.'
        assert not_admitted() and not copy.exists()
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
              f'recorded as cancelled, nothing admits it, and its copy of the keys file is gone ('
              f'{"the stop removed it" if removed_at_the_stop else "it was still there at the stop; this case removed it after the last process"}'
              f'). The second '
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
            print(native.update_stage_probe.catalog_helper_line(name, first, second, before, after, helper_limit))
        for name, failure in helper_failures.items():
            print(f'FAILED what the case does itself in {name} before the app\'s probe command (its starts of '
                  f'the decision-catalog helper and its counts of bytecode files): {failure}')

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
    # has less to compile. The printed time of the second start is the whole time of a start with that
    # bytecode there; it does not measure the compilation alone. A first start that adds no bytecode file
    # fails here (see the top of this file).
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
