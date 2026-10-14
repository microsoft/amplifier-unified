"""A fresh worker owns its memory budget, not its parent's pre-exec peak."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Qualified Linux memory policy')
@pytest.mark.parametrize('allocate', [False, True])
async def test_fresh_worker_under_large_parent_keeps_its_own_memory_budget(tmp_path, allocate):
    import os
    import signal
    process = await asyncio.create_subprocess_exec(sys.executable, __file__, 'parent',
        str(tmp_path), str(int(allocate)), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result['parentResident'] > result['limit']
    assert result['ready'] == {'type': 'ready'}, result
    if allocate:
        assert result['outcome']['reason'] == 'memory_limit'
        failure = result['outcome']['failure']
        assert failure['stage'] == 'model' and failure['exceptionType'] == 'MemoryError'
        assert failure['addressSpaceLimitBytes'] == result['limit']
    else:
        assert result['outcome']['status'] == 'complete'
        assert result['outcome']['resident'] < result['limit']
        assert result['outcome']['inheritedPeak'] > result['limit']
        assert result['outcome']['addressSpaceLimit'] == result['limit']


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Qualified Linux memory policy')
def test_linux_resident_guard_rechecks_current_pages_and_refuses_missing_accounting(monkeypatch):
    import builtins
    import io
    import os
    import resource
    from amplifier_web.history_projection_worker import MemoryLimit, memory_policy

    monkeypatch.setattr(resource, 'setrlimit', lambda *args: None)
    rows = iter(['10 1 0', '10 4 0', '10 -1 0', 'malformed'])
    monkeypatch.setattr(builtins, 'open', lambda *args, **kwargs: io.StringIO(next(rows)))
    check = memory_policy(os.sysconf('SC_PAGE_SIZE') * 2)
    with pytest.raises(MemoryLimit):
        check()
    with pytest.raises(ValueError):
        check()
    with pytest.raises(IndexError):
        check()
    def unavailable(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(builtins, 'open', unavailable)
    with pytest.raises(FileNotFoundError):
        check()


def resident():
    import os
    return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')


async def large_parent(home, allocate):
    limit = 128 * 1024 * 1024
    held = bytearray(160 * 1024 * 1024)
    parent_resident = resident()
    spool = home / 'spool'
    spool.mkdir()
    identity = '1' * 32
    data = json.dumps({'allocate': allocate}).encode()
    (spool / (identity + '.input')).write_bytes(data)
    child = await asyncio.create_subprocess_exec(sys.executable, __file__, 'worker',
        str(home), str(limit), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        ready = json.loads(await asyncio.wait_for(child.stdout.readline(), 5))
        outcome = None
        if ready == {'type': 'ready'}:
            child.stdin.write(json.dumps({'id': identity, 'digest': hashlib.sha256(data).hexdigest()}).encode() + b'\n')
            await child.stdin.drain()
            outcome = json.loads(await asyncio.wait_for(child.stdout.readline(), 5))
            if outcome.get('type') == 'result':
                outcome = json.loads((spool / (identity + '.output')).read_text())
        child.stdin.write(b'{"stop":true}\n')
        await child.stdin.drain()
        await asyncio.wait_for(child.communicate(), 5)
        print(json.dumps({'ready': ready, 'outcome': outcome, 'parentResident': parent_resident,
                          'heldBytes': len(held), 'limit': limit}))
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


if __name__ == '__main__':
    if sys.argv[1] == 'parent':
        asyncio.run(large_parent(Path(sys.argv[2]), bool(int(sys.argv[3]))))
    else:
        import resource
        from amplifier_web.history_projection_worker import run
        class Model:
            def __init__(self, home):
                pass
            def project(self, inputs, *, check_budget):
                if inputs['allocate']:
                    bytearray(256 * 1024 * 1024)
                check_budget()
                return {'status': 'complete', 'resident': resident(),
                    'inheritedPeak': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                    'addressSpaceLimit': resource.getrlimit(resource.RLIMIT_AS)[0]}
        home = Path(sys.argv[2])
        run(home / 'spool', home, model_factory=Model, memory_limit=int(sys.argv[3]))
