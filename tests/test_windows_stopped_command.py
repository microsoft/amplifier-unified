"""Native Windows: what a stop or a timeout of a preparation command ends, and what ends the rest.

The app starts every command of a preparation through ``updates.process`` (uv,
Git, the check of a chat's setup). When a person stops the chat,
``RuntimeManager.stop`` cancels the preparation, and ``process`` ends the
command that it started. A timeout takes the same way. On Windows that ends
one process: a program that the command started runs on. On an installed PC
the launcher puts the app into a kill-on-close Job before it starts any child
(``windows_jobs.contain_current_host``), so Windows ends such a program when
the app process ends.

This file shows both with a controlled command, and the same app without the
launcher's Job as the control. It does not follow processes by a list of what
runs. The command starts a program through a parent that
ends at once, so the program has no living parent. The case holds an open
handle of that program from the moment it is known; Windows signals the
handle when the program ends. The program also counts in a file while it runs.

Nothing is downloaded. The CI job ``.github/workflows/windows-path-length.yml``
runs this file and fails when a case did not run.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows process lifetime needs native Windows')
ROOT = Path(__file__).resolve().parents[1]
# The interpreter itself, not a venv redirector: ending the command then ends exactly one process.
PYTHON = getattr(sys, '_base_executable', None) or sys.executable

NOTE = '''
def note(folder, name):
    with open(os.path.join(folder, name + '.tmp'), 'w') as stream:
        stream.write(str(os.getpid()))
    os.replace(os.path.join(folder, name + '.tmp'), os.path.join(folder, name))
'''
# The command that the app starts. It starts a parent, waits for it, and then runs on.
COMMAND = 'import os, subprocess, sys, time\n' + NOTE + '''
folder = sys.argv[1]
note(folder, 'command.pid')
subprocess.run([sys.executable, os.path.join(folder, 'parent.py'), folder], stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(600)
'''
# The parent starts the program and ends at once.
PARENT = '''import os, subprocess, sys
folder = sys.argv[1]
subprocess.Popen([sys.executable, os.path.join(folder, 'left.py'), folder], stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''
# The program: it counts in a file while it runs, for at most 10 minutes.
LEFT = 'import os, sys, time\n' + NOTE + '''
folder = sys.argv[1]
note(folder, 'left.pid')
count, end = 0, time.monotonic() + 600
while time.monotonic() < end:
    count += 1
    try:
        with open(os.path.join(folder, 'count'), 'w') as stream:
            stream.write(str(count))
    except OSError:
        pass
    time.sleep(0.1)
'''
# The app, as the installed launcher runs it: the launcher's own containment call first, then a
# preparation command, then a stop. It runs on until the case lets it end. Without the call
# ("free") it is the same process with no Job of the launcher.
HOST = '''import asyncio, json, os, sys, threading
guard = threading.Timer(300, os._exit, (3,))   # never outlive a broken case
guard.daemon = True
guard.start()
from amplifier_web.updates import process
from amplifier_web.windows_jobs import contain_current_host
folder, python, contained = sys.argv[1], sys.argv[2], sys.argv[3] == 'contained'
job = contain_current_host() if contained else None


async def main():
    task = asyncio.create_task(process(python, os.path.join(folder, 'command.py'), folder, timeout=600))
    while not all(os.path.exists(os.path.join(folder, name)) for name in ('command.pid', 'left.pid', 'count')):
        await asyncio.sleep(0.1)
    print('RUNNING ' + json.dumps({'job': job}), flush=True)
    await asyncio.to_thread(sys.stdin.readline)      # the case now holds both processes
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return True
    return False


stopped = asyncio.run(main())
print('STOPPED ' + json.dumps({'stopped': stopped}), flush=True)
sys.stdin.readline()
'''


def scripts(folder):
    for name, text in (('command.py', COMMAND), ('parent.py', PARENT), ('left.py', LEFT), ('host.py', HOST)):
        (folder / name).write_text(text, encoding='utf-8')
    return folder


def windows():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD
    kernel.TerminateProcess.argtypes, kernel.TerminateProcess.restype = (wintypes.HANDLE, wintypes.UINT), wintypes.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = (wintypes.HANDLE,), wintypes.BOOL
    return kernel


class Program:
    """One process of the controlled command, held by an open handle from the moment it is known."""

    def __init__(self, kernel, folder, name):
        self.kernel, deadline = kernel, time.monotonic() + 60
        while True:
            try:
                self.pid = int((folder / name).read_text(encoding='utf-8'))
                break
            except (OSError, ValueError):
                assert time.monotonic() < deadline, f'{name} did not appear'
                time.sleep(0.05)
        # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
        self.handle = kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, self.pid)
        assert self.handle, f'{name}: process {self.pid} could not be opened'

    def ended(self, within=0.0):
        """Windows signals the handle of a process when that process has ended."""
        status = self.kernel.WaitForSingleObject(self.handle, int(within * 1000))
        assert status in (0, 258), status   # signalled, or the time passed
        return status == 0

    def close(self):
        if not self.ended():
            self.kernel.TerminateProcess(self.handle, 1)   # the case's own program; it must not outlive the case
        self.kernel.CloseHandle(self.handle)


def counts(folder, seconds=2.0):
    """True when the program still counts in its file."""
    def read():
        for attempt in range(100):
            try:
                return int((folder / 'count').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                time.sleep(0.02)
        return None

    first = read()
    time.sleep(seconds)
    second = read()
    return first is not None and second is not None and second > first


@pytest.mark.parametrize('kind', ['stop', 'timeout'])
def test_a_stop_and_a_timeout_end_the_command_and_not_a_program_that_it_started(tmp_path, kind):
    from amplifier_web.update_diagnostics import CommandTimeout
    from amplifier_web.updates import process
    kernel, folder, held = windows(), scripts(tmp_path), []

    limit = 600 if kind == 'stop' else 30

    async def journey():
        began = time.monotonic()
        task = asyncio.create_task(process(PYTHON, folder / 'command.py', folder, timeout=limit))
        for name in ('command.pid', 'left.pid'):
            held.append(await asyncio.to_thread(Program, kernel, folder, name))
        command, left = held
        assert await asyncio.to_thread(counts, folder) and not command.ended() and not left.ended() and not task.done()
        stopped = time.monotonic()
        if kind == 'stop':
            task.cancel()
        with pytest.raises(asyncio.CancelledError if kind == 'stop' else CommandTimeout):
            await task
        return time.monotonic() - (stopped if kind == 'stop' else began)

    try:
        waited = asyncio.run(journey())
        command, left = held
        # The app ended the command that it started, and it waited for that.
        assert command.ended(10)
        assert waited >= limit if kind == 'timeout' else waited < 30
        # The program that the command started runs on. Nothing of the app ends it.
        assert not left.ended(3) and counts(folder)
        print(f'MEASURED a {kind} of a preparation command: the command (process {command.pid}) ended, '
              + (f'{waited:.1f} s after the stop' if kind == 'stop' else
                 f'{waited:.1f} s after its start with a time limit of {limit} s')
              + f'; the program that the command started through a parent that had ended (process {left.pid}) '
                f'still ran afterwards')
    finally:
        for program in held:
            program.close()


@pytest.mark.parametrize('contained', [True, False])
def test_the_launchers_job_ends_such_a_program_when_the_app_ends(tmp_path, contained):
    """With the launcher's Job the program ends with the app. Without it (the control) the program outlives the app."""
    kernel, folder, held = windows(), scripts(tmp_path), []
    host = subprocess.Popen([sys.executable, '-B', str(folder / 'host.py'), str(folder), PYTHON,
                             'contained' if contained else 'free'], cwd=str(ROOT),
                            env={**os.environ, 'PYTHONPATH': str(ROOT)}, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    def said(word):
        line = host.stdout.readline()
        if not line.startswith(word + ' '):
            host.kill()
            pytest.fail(f'The app did not reach {word}: {line!r} {host.stderr.read()[-4000:]}')
        return json.loads(line[len(word) + 1:])

    try:
        # The launcher's own Job, made by the launcher's own call, before the app starts any child.
        job = said('RUNNING')['job']
        assert (job['killOnClose'] is True and job['inheritable'] is False) if contained else job is None
        for name in ('command.pid', 'left.pid'):
            held.append(Program(kernel, folder, name))
        command, left = held
        assert counts(folder) and not command.ended() and not left.ended()
        host.stdin.write('\n')
        host.stdin.flush()
        assert said('STOPPED')['stopped'] is True
        # As without a Job: the stop ended the command, and the program runs on while the app runs.
        assert command.ended(10)
        assert not left.ended(3) and counts(folder) and host.poll() is None
        # The app ends. Nothing ends the program but Windows, which closes the Job with the app's process.
        host.stdin.write('\n')
        host.stdin.close()
        started = time.monotonic()
        assert host.wait(60) == 0
        if contained:
            assert left.ended(30), 'The program outlived the app: the Job did not hold it'
            waited = time.monotonic() - started
            assert not counts(folder)
            print(f'MEASURED the launcher\'s kill-on-close Job: after the stop the program that the command started '
                  f'(process {left.pid}) ran on while the app ran; {waited:.1f} s after the app was told to end, '
                  f'Windows had ended it')
        else:
            # The control: the same app without the launcher's Job. The program outlives it.
            assert not left.ended(10) and counts(folder)
            print(f'MEASURED without the launcher\'s Job: the program that the command started (process {left.pid}) '
                  f'still ran 10 s after the app had ended')
    finally:
        if not host.stdin.closed:
            host.stdin.close()     # the app reads to the end of its input and then ends
        if host.poll() is None:
            host.kill()
        for program in held:
            program.close()
        host.stdout.close()
        host.stderr.close()
