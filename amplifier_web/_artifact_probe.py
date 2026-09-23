"""Private POSIX probe supervisor, executed directly with isolated Python.

The supervisor owns its process group until the parent closes the control pipe.
Probe output has a separate pipe: a helper may exit without releasing a
descendant's stdout, but it cannot cause the supervisor's group ID to be reused.
No command output or exception is written except the bounded result frame.
"""
import base64
import json
import os
import selectors
import signal
import subprocess
import sys
import time


def emit(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def probe(plan):
    command, limit, timeout = plan['command'], plan['limit'], plan['timeout']
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) for arg in command)
            or type(limit) is not int or not 1 <= limit <= 16384
            or type(timeout) not in (int, float) or not 0 < timeout <= 15
            or type(plan['stderr']) is not bool):
        raise ValueError('invalid probe')
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if plan['stderr'] else subprocess.DEVNULL, close_fds=True)
    output = bytearray()
    deadline = time.monotonic() + timeout
    eof = False
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ, 'control')
        selector.register(child.stdout, selectors.EVENT_READ, 'output')
        while True:
            code = child.poll()
            if eof and code is not None:
                return {'status': 'completed', 'returncode': code,
                    'output': base64.b64encode(output).decode('ascii')}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'status': 'timeout'}
            for key, _ in selector.select(min(remaining, .05)):
                if key.data == 'control':
                    # EOF (including parent death/cancellation) or an unexpected
                    # additional byte both request immediate group cleanup.
                    return {'status': 'cancelled'}
                data = os.read(child.stdout.fileno(), min(4096, limit + 1 - len(output)))
                if not data:
                    eof = True
                    selector.unregister(child.stdout)
                    child.stdout.close()
                else:
                    output.extend(data)
                    if len(output) > limit:
                        return {'status': 'overflow'}


def main():
    # Never signal a group we did not create and still lead ourselves.
    if os.name != 'posix' or os.getpid() != os.getpgrp() or os.getsid(0) != os.getpid():
        return 1
    try:
        raw = sys.stdin.buffer.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise ValueError('invalid probe frame')
        result = probe(json.loads(raw))
        emit(result)
        # Retain the group identity even after a successful helper exit. Only
        # the parent owns the control writer; the helper never inherits it.
        if result['status'] != 'cancelled':
            sys.stdin.buffer.read(1)
    except BaseException:
        # The parent receives unknown if no complete result frame is available.
        pass
    finally:
        try:
            emit({'cleanup': 'group'})
        finally:
            # This process is alive at the syscall. There is no reaped numeric
            # PGID or parent-side signal fallback, even on a failed probe.
            os.killpg(os.getpgrp(), signal.SIGKILL)
    return 1  # Cleanup must end the supervisor as well as its descendants.


if __name__ == '__main__':
    raise SystemExit(main())
