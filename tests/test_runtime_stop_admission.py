"""Real worker process races: stopped events never authorize the closing owner."""
import asyncio
import json
from pathlib import Path
import sys

import pytest

from amplifier_web.runtime import RuntimeManager

FIXTURE = r'''
import json, os, sys, time
from pathlib import Path
release, log = map(Path, sys.argv[1:])
for line in sys.stdin:
    data = json.loads(line)
    with log.open('a') as output:
        output.write(json.dumps({'pid': os.getpid(), 'op': data['op']}) + '\n')
    if data['op'] == 'start':
        print(json.dumps({'type':'runtime.ready', 'report':{}}), flush=True)
    elif data['op'] == 'stop':
        print(json.dumps({'type':'session.closed'}), flush=True)
        while not release.exists(): time.sleep(.01)
        break
    else:
        print(json.dumps({'op':'reply', 'id':data['id'], 'result':{'pid':os.getpid()}}), flush=True)
'''


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout', [False, True])
async def test_stop_event_precedes_exit_new_admission_waits_without_replay(tmp_path, timeout):
    fixture, release, log = (tmp_path / name for name in ('worker.py', 'release', 'commands.jsonl'))
    fixture.write_text(FIXTURE)
    manager = RuntimeManager(command=[sys.executable, str(fixture), str(release), str(log)])
    manager.retention.reply_timeout = .05 if timeout else 10
    closed = asyncio.Event()
    async def emit(kind, data):
        if kind == 'runtime.status' and data.get('event') == 'session.closed':
            closed.set()
    session = {'id':'owned', 'workspace':str(tmp_path), 'bundle':'fixture'}
    try:
        await manager.start(session, emit)
        original = manager.workers['owned']['process']
        stop = asyncio.create_task(manager.stop('owned'))
        await asyncio.wait_for(closed.wait(), 10)
        async def new_control():
            await manager.start(session, emit)
            return await manager.control('owned', 'fixture.inspect')
        admission = asyncio.create_task(new_control())
        if timeout:
            with pytest.raises(RuntimeError, match='shutdown is still being confirmed'):
                await admission
        else:
            await asyncio.sleep(.05)
            assert not admission.done()
        assert original.returncode is None
        assert [json.loads(line)['op'] for line in log.read_text().splitlines()] == ['start', 'stop']
        release.touch()
        await asyncio.wait_for(stop, 10)
        if timeout:
            # An explicit retry may start fresh only after exit is observed.
            response = await new_control()
        else:
            response = await asyncio.wait_for(admission, 10)
        assert original.returncode == 0
        assert response['pid'] != original.pid
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row['op'] for row in rows] == ['start', 'stop', 'start', 'control']
        assert rows[-1]['pid'] == response['pid']
    finally:
        release.touch()
        await manager.close()
