"""Private Linux probe-to-host Git execution; the host owns every launched PID.

The disposable probe never starts a detached Git writer. Requests cross a private
Unix socket to the host that outlives it. This is not a public execution service.
Windows retains Job containment; other POSIX systems retain their existing path.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import sys
from contextlib import nullcontext

_REQUEST_LIMIT = 1024 * 1024
_OUTPUT_LIMIT = 4 * 1024 * 1024
_CLEANUP_SECONDS = 5
_failed_owners = set()


class GitProbeCleanupError(RuntimeError):
    """Cleanup is unconfirmed; retain this owner and do not remove its home."""

    def __init__(self, owner):
        super().__init__('Probe Git cleanup is unconfirmed; process custody is retained.')
        self.owner = owner


def run_git(args, cwd, timeout_s):
    """Foundation's synchronous runner in the disposable probe."""
    from amplifier_foundation.sources.git import GitCloneTimeoutError

    request = json.dumps({'args': args, 'cwd': os.path.abspath(cwd or os.getcwd()),
                          'timeout': timeout_s, 'env': dict(os.environ)}).encode() + b'\n'
    if len(request) > _REQUEST_LIMIT:
        raise ValueError('Probe Git request exceeded its byte limit.')
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(timeout_s + 4 * _CLEANUP_SECONDS)
            connection.connect(os.environ['AMPLIFIER_PROBE_GIT_SOCKET'])
            connection.sendall(request)
            with connection.makefile('rb') as stream:
                response = stream.readline(8 * _OUTPUT_LIMIT + 4096)
        result = terminal(json.loads(response))
    except BaseException:
        # No Python unwind is safe here: Foundation stage cleanup is allowed
        # only after positive settlement, not EOF, malformed IPC or host death.
        # Exit ONLY this disposable caller, never a guessed PID/group.
        os._exit(74)
    if result['kind'] == 'timeout':
        raise GitCloneTimeoutError(args, timeout_s)
    if result['kind'] == 'spawn_error':
        raise OSError(result['errno'], result['message'], result['filename'])
    if result['kind'] != 'result':
        raise RuntimeError('Host Git execution failed.')
    if result['code']:
        raise subprocess.CalledProcessError(result['code'], args,
                                            result['stdout'], result['stderr'])
    return subprocess.CompletedProcess(args, result['code'], result['stdout'], result['stderr'])


def terminal(result):
    """Validate positive, closed terminal evidence before any caller unwind."""
    if not isinstance(result, dict) or result.get('settled') is not True:
        raise ValueError('Git guard did not confirm settlement')
    kind = result.get('kind')
    if kind == 'result':
        if (type(result.get('code')) is not int or
                not isinstance(result.get('stdout'), str) or
                not isinstance(result.get('stderr'), str)):
            raise ValueError('Invalid Git guard result')
    elif kind == 'spawn_error':
        if (type(result.get('errno')) is not int or
                not isinstance(result.get('message'), str) or
                result.get('filename') is not None and not isinstance(result['filename'], str)):
            raise ValueError('Invalid Git guard spawn error')
    elif kind not in {'timeout', 'overflow', 'error'}:
        raise ValueError('Invalid Git guard terminal outcome')
    return result


async def query(operation):
    """Install only the Linux subreaper-backed runner; preserve other platforms."""
    if sys.platform == 'linux' and os.environ.get('AMPLIFIER_PROBE_GIT_SOCKET'):
        from amplifier_foundation.sources.git import git_subprocess_runner
        scope = git_subprocess_runner(run_git)
    else:
        scope = nullcontext()
    with scope:
        return await operation


class GitProbeOwner:
    """One broker per finite probe, with a bounded cancellation-safe finalizer."""

    def __init__(self):
        if sys.platform != 'linux':
            raise RuntimeError('Probe Git ownership requires the Linux child subreaper.')
        # Private mode-0700 directory and a short sockaddr_un path.
        self.directory = tempfile.TemporaryDirectory(prefix='amplifier-probe-git-', dir='/tmp')
        self.path = str(Path(self.directory.name) / 'control')
        self.server = None
        self.tasks = set()
        self.processes = set()
        self.closing = False
        self.cleanup_complete = False
        self._close_task = None
        self.errors = []
        self.abort_probe = None
        self.pending_starts = set()
        self.finalizers = set()
        self.retained_connections = set()
        self.failures = []  # Private command/spawn outcome plus cleanup cause.

    async def start(self):
        self.server = await asyncio.start_unix_server(self._accept, path=self.path,
                                                     limit=_REQUEST_LIMIT)
        return self

    def _accept(self, reader, writer):
        if self.closing:
            writer.close()
            return
        task = asyncio.create_task(self._request(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    async def _request(self, reader, writer):
        release = True
        try:
            result = await self._execute(reader)
            writer.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')
            async with asyncio.timeout(_CLEANUP_SECONDS):
                await writer.drain()
        except (ConnectionError, TimeoutError):
            pass  # The caller may have exited; process cleanup already settled.
        except GitProbeCleanupError:
            # Do not release the synchronous caller into Foundation's stage
            # finally/rmtree while writer custody is unconfirmed. Kill the
            # disposable caller first, preserving its stage and the host error.
            if self.abort_probe is not None:
                try:
                    async with asyncio.timeout(_CLEANUP_SECONDS):
                        await self.abort_probe()
                except BaseException as exc:
                    release = False
                    self.errors.append(exc)
            else:
                release = False
            if not release:
                self.retained_connections.add(writer)
                _failed_owners.add(self)
            raise
        finally:
            if release:
                writer.close()

    async def _cleanup(self, process, response):
        try:
            # EOF requests cooperative anchored-group kill/reaping by the guard.
            # Never SIGKILL the guard before it can settle its Git descendants.
            process.stdin.close()
            async with asyncio.timeout(_CLEANUP_SECONDS):
                result = await asyncio.shield(response)
                if result.get('kind') == 'cleanup_error':
                    self.failures.append(result)
                    raise RuntimeError('Git guard did not confirm writer settlement')
                await process.wait()
            if process.returncode:
                raise RuntimeError('Git guard did not confirm writer settlement')
            self.processes.discard(process)
        except BaseException as exc:
            self.errors.append(exc)
            _failed_owners.add(self)
            raise GitProbeCleanupError(self) from exc

    async def _execute(self, reader):
        from .probe_process import _settle

        process = None
        response = None
        try:
            async with asyncio.timeout(_CLEANUP_SECONDS):
                request = json.loads(await reader.readline())
            args, cwd, seconds, env = (request[key] for key in ('args', 'cwd', 'timeout', 'env'))
            if (not isinstance(args, list) or not args or
                    not all(isinstance(arg, str) for arg in args) or
                    cwd is not None and not isinstance(cwd, str) or
                    not isinstance(env, dict) or
                    not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()) or
                    not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0):
                raise ValueError('Invalid finite Git invocation.')
            # Keep pending creation owned even if pipes cannot connect in time.
            # No replay and no cancellation of asyncio's partly-created child.
            start = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, '-I', '-S', str(Path(__file__).with_name('probe_git_guard.py')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=8 * _OUTPUT_LIMIT + 4096,
                start_new_session=True))
            self.pending_starts.add(start)
            async def adopt():
                proc = await start
                self.processes.add(proc)
                # Creation alone cannot start Git: the request is sent only
                # after ownership transfer, below.
                return proc
            adoption = asyncio.create_task(adopt())
            try:
                async with asyncio.timeout(_CLEANUP_SECONDS):
                    process = await asyncio.shield(adoption)
            except BaseException:
                async def late_cleanup():
                    try:
                        proc = await adoption
                    except OSError:
                        self.pending_starts.discard(start)
                        return
                    proc.stdin.close()
                    # No request was admitted: EOF exits before any Git spawn.
                    await proc.communicate()
                    self.processes.discard(proc)
                    self.pending_starts.discard(start)
                finalizer = asyncio.create_task(late_cleanup())
                self.finalizers.add(finalizer)
                finalizer.add_done_callback(self.finalizers.discard)
                finalizer.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception())
                raise
            self.pending_starts.discard(start)
            self.processes.add(process)
            async def read_response():
                result = json.loads(await process.stdout.readline())
                if isinstance(result, dict) and result.get('kind') == 'cleanup_error':
                    return result  # Keep original outcome and cleanup cause.
                return terminal(result)
            response = asyncio.create_task(read_response())
            try:
                # Execution and cleanup have separate budgets. Only the guard
                # declares a command timeout; missing confirmation is custody
                # failure, not a replacement for an original command result.
                async with asyncio.timeout(seconds + _CLEANUP_SECONDS):
                    process.stdin.write(json.dumps(request).encode() + b'\n')
                    await process.stdin.drain()
                    result = await asyncio.shield(response)
            except TimeoutError:
                raise RuntimeError('Git guard control deadline expired')
        except OSError as exc:
            result = {'kind': 'spawn_error', 'errno': exc.errno,
                      'filename': exc.filename, 'message': exc.strerror or str(exc), 'settled': True}
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {'kind': 'error', 'settled': True}
        finally:
            if process is not None:
                await _settle(asyncio.create_task(self._cleanup(process, response)))
        return result

    async def _close(self):
        self.closing = True
        if self.server is not None:
            self.server.close()
        pending = tuple(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            try:
                async with asyncio.timeout(_CLEANUP_SECONDS * 2):
                    await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))
            except TimeoutError as exc:
                self.errors.append(exc)
        if self.finalizers:
            try:
                async with asyncio.timeout(_CLEANUP_SECONDS):
                    await asyncio.shield(asyncio.gather(*self.finalizers, return_exceptions=True))
            except TimeoutError as exc:
                self.errors.append(exc)
        if self.errors or self.processes or self.pending_starts or self.retained_connections:
            _failed_owners.add(self)
            raise GitProbeCleanupError(self) from (self.errors[0] if self.errors else None)
        if self.server is not None:
            async with asyncio.timeout(_CLEANUP_SECONDS):
                await self.server.wait_closed()
        self.cleanup_complete = True
        self.directory.cleanup()

    async def close(self):
        from .probe_process import _settle

        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        return await _settle(self._close_task)
