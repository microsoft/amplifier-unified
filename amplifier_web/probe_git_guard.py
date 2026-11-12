"""One finite POSIX Git lifetime, including a live process-group identity anchor.

The guard is outside the Git group. It can kill that group, reap its direct
children, and report cleanup before exiting. The anchor stays alive even when
Git exits first; a numeric group ID is never signalled after releasing it.
"""
import ctypes
import json
import os
import selectors
import signal
import subprocess
import sys
import time

_LIMIT = 4 * 1024 * 1024
_CLEANUP = 3


def reap_adopted():
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                return False  # An adopted child is still alive, possibly setsid.
        except ChildProcessError:
            return True


def group_exists(group):
    try:
        os.killpg(group, 0)  # POSIX-only passive observation; never used on NT.
        return True
    except ProcessLookupError:
        return False


def main():
    if sys.platform != 'linux':
        # ECHILD cannot establish escaped-descendant absence without adoption.
        # Refuse before creating either the anchor or a Git writer.
        raise RuntimeError('Git guard requires the Linux child subreaper')
    if sys.argv[1:] == ['--anchor']:
        sys.stdin.buffer.read()
        return
    line = sys.stdin.buffer.readline()
    if not line:
        return  # Creation cancelled before any Git invocation was admitted.
    request = json.loads(line)
    # Reparent descendants to this *disposable guard*, never the host.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot own Git descendant reaping')
    anchor = subprocess.Popen([sys.executable, '-I', '-S', __file__, '--anchor'],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, process_group=0)
    child = None
    result = {'kind': 'error'}
    try:
        try:
            child = subprocess.Popen(request['args'], cwd=request['cwd'], env=request['env'],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, process_group=anchor.pid)
        except OSError as exc:
            result = {'kind': 'spawn_error', 'errno': exc.errno,
                      'filename': exc.filename, 'message': exc.strerror}
        else:
            chunks = [bytearray(), bytearray()]
            deadline = time.monotonic() + request['timeout']
            with selectors.DefaultSelector() as selector:
                selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'control')
                selector.register(child.stdout, selectors.EVENT_READ, 0)
                selector.register(child.stderr, selectors.EVENT_READ, 1)
                while True:
                    if time.monotonic() >= deadline:
                        result = {'kind': 'timeout'}
                        break
                    if len(selector.get_map()) == 1 and child.poll() is not None:
                        result = {'kind': 'result', 'code': child.returncode,
                                  'stdout': chunks[0].decode(errors='replace'),
                                  'stderr': chunks[1].decode(errors='replace')}
                        break
                    cancelled = False
                    for key, _ in selector.select(.02):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if key.data == 'control':
                            cancelled = True
                            break
                        if not data:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        else:
                            chunks[key.data].extend(data)
                            if sum(map(len, chunks)) > _LIMIT:
                                result = {'kind': 'overflow'}
                                cancelled = True
                                break
                    if cancelled:
                        break
    finally:
        try:
            # Only this unreleased, live, direct-child anchor authorizes a kill.
            if anchor.poll() is not None or os.getpgid(anchor.pid) != anchor.pid:
                raise RuntimeError('Git group identity anchor was lost')
            os.killpg(anchor.pid, signal.SIGKILL)
            deadline = time.monotonic() + _CLEANUP
            if child is not None:
                child.wait(timeout=max(.01, deadline - time.monotonic()))
            anchor.wait(timeout=max(.01, deadline - time.monotonic()))
            anchor.stdin.close()
            # Reap adopted Linux helpers too; on other POSIX systems wait for
            # the OS reaper. This is observation only, never a second stale kill.
            while True:
                children_empty = reap_adopted()
                if children_empty and not group_exists(anchor.pid):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Git descendants are not confirmed empty')
                time.sleep(.01)
        except BaseException as exc:
            result = {'kind': 'cleanup_error', 'settled': False,
                      'primary': result,
                      'cleanup': {'type': type(exc).__name__, 'message': str(exc)[:500]}}
        else:
            result['settled'] = True
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result['kind'] == 'cleanup_error' and sys.platform == 'linux':
        # Retain subreaper custody on failure. Do not reparent a still-live
        # escaped writer and do not signal numeric PIDs inferred from a scan.
        # The host retains this guard and the original failure; naturally
        # exiting children can still be reaped without rewriting that result.
        while not reap_adopted():
            time.sleep(.02)


if __name__ == '__main__':
    main()
