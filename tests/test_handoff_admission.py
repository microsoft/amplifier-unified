"""Cross-feature handoff races use the real runtime admission and process path."""
import asyncio
import copy
import sys

import pytest

from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from test_worktrees import repository


SCRIPT = r'''
import json,sys
for line in sys.stdin:
    value=json.loads(line)
    if value['op']=='start':
        config=value['session']
        print(json.dumps({'type':'runtime.ready','report':{'session_id':config['id']}}),flush=True)
    elif value['op']=='stop':break
    else:
        print(json.dumps({'op':'reply','id':value['id'],'result':{'ok':True,'executionDirectory':config.get('workingDirectory',config['workspace'])}}),flush=True)
'''


class BarrierRuntime(RuntimeManager):
    def __init__(self):
        super().__init__(command=[sys.executable, '-c', SCRIPT])
        self.retention.wake = lambda: None
        self.releasing = asyncio.Event()
        self.release = asyncio.Event()
        self.start_requested = asyncio.Event()
        self.admitting = asyncio.Event()

    async def start(self, session, emit):
        self.start_requested.set()
        return await super().start(session, emit)

    async def _start_locked(self, session, emit):
        self.admitting.set()
        return await super()._start_locked(session, emit)

    async def quiesce_for_handoff(self, session, request_id):
        # The Foundation release handshake has separate real-store tests. This
        # barrier controls only its timing; start/admit/process paths are real.
        async with self._admission(session['id']):
            await self.stop(session['id'])
            self.releasing.set()
            await self.release.wait()
            return {'quiesced': True}


async def fixture(tmp_path):
    root=repository(tmp_path)
    runtime=BarrierRuntime()
    app=AppService(tmp_path/'app',runtime,workspace=root)
    app.management=Management(app)
    await app.dispatch('session.create',{})
    sid=app._session()['id']
    inspected=(await app.dispatch('worktree.inspect',{'sessionId':sid}))['result']
    target=(await app.dispatch('worktree.create',{'sessionId':sid,'sourceRevision':inspected['repository']['sourceRevision']}))['result']
    return app,runtime,sid,target


async def pending(app,runtime,sid,target):
    receipt=(await app.dispatch('worktree.handoff',{'sessionId':sid,'id':target['id'],'expectedExecutionRevision':0}))['result']
    # Real Git preflight runs before the controlled release barrier. Allow it
    # time on a busy host; the race assertions start only after this event.
    try:
        await asyncio.wait_for(runtime.releasing.wait(),10)
    except TimeoutError:
        pytest.fail(f"Handoff did not reach release: {app.worktrees.read(sid, receipt['id'])}")
    return receipt


async def test_queued_usage_inspection_cannot_start_old_checkout_in_commit_gap(tmp_path):
    app,runtime,sid,target=await fixture(tmp_path)
    home=app._session(sid)['workspace']
    try:
        await pending(app,runtime,sid,target)
        await app.dispatch('runtime.control',{'sessionId':sid,'operation':'usage.inspect'})
        await asyncio.wait_for(runtime.start_requested.wait(),2)
        # Make release finish before the durable host state can commit. The
        # queued usage start must see the fence inside the runtime lock.
        async with app.lock:
            runtime.release.set()
            await asyncio.wait_for(runtime.admitting.wait(),2)
        await asyncio.gather(*list(app.worktrees.jobs))
        for _ in range(100):
            if app.state.get('actionStatus',{}).get('runtime.control',{}).get('phase') in {'ready','error'}:break
            await asyncio.sleep(.01)
        result=app.state['actionStatus']['runtime.control']
        assert result['phase']=='error' and 'handoff' in result['error']
        assert sid not in runtime.workers
        assert app._session(sid)['workspace']==home
        assert app._session(sid)['workingDirectory']==target['path']
        await runtime.start(copy.deepcopy(app._session(sid)),app.on_runtime_event)
        assert (await runtime.control(sid,'usage.inspect'))['executionDirectory']==target['path']
    finally:
        runtime.release.set();await app.close()


@pytest.mark.parametrize('action,args', [
    ('task.get',{}),
    ('task.create',{'objective':'Keep working','expectedRevision':0}),
    ('schedule.preview',{'prompt':'Check work','kind':'monitor','spec':{'kind':'interval','timezone':'UTC','startAt':'2099-01-01T00:00:00+00:00','intervalSeconds':60},'missedRunPolicy':'skip','notificationPolicy':'changes'}),
])
async def test_task_and_schedule_runtime_preparation_remain_fenced(tmp_path,action,args):
    app,runtime,sid,target=await fixture(tmp_path)
    try:
        receipt=await pending(app,runtime,sid,target)
        # Unknown receipts also fence admission after the release lock is gone.
        record=app.worktrees.read(sid,receipt['id'])
        record['phase']='unknown';app.worktrees.save(record);app.worktrees.sync()
        for job in list(app.worktrees.jobs):job.cancel()
        await asyncio.gather(*list(app.worktrees.jobs),return_exceptions=True)
        with pytest.raises(AppError,match='handoff'):
            await app.dispatch(action,{'sessionId':sid,**args})
        assert sid not in runtime.workers
    finally:
        runtime.release.set();await app.close()


async def test_stale_pre_handoff_start_rejected_after_commit_and_restore_unknown(tmp_path):
    app,runtime,sid,target=await fixture(tmp_path)
    stale=copy.deepcopy(app._session(sid))
    try:
        receipt=await pending(app,runtime,sid,target)
        runtime.release.set();await asyncio.gather(*list(app.worktrees.jobs))
        with pytest.raises(ValueError,match='folder changed'):
            await runtime.start(stale,app.on_runtime_event)
        assert sid not in runtime.workers
        record=app.worktrees.read(sid,receipt['id'])
        record['phase']='unknown';app.worktrees.save(record);app.worktrees.sync()
    finally:await app.close()
    restored_runtime=BarrierRuntime()
    restored=AppService(tmp_path/'app',restored_runtime,workspace=tmp_path/'repo')
    try:
        with pytest.raises(ValueError,match='handoff'):
            await restored_runtime.start(copy.deepcopy(restored._session(sid)),restored.on_runtime_event)
        assert not restored_runtime.workers
    finally:await restored.close()


async def test_existing_worker_new_work_fenced_but_cancellation_controls_stay_available(tmp_path):
    app,runtime,sid,target=await fixture(tmp_path)
    try:
        await runtime.start(copy.deepcopy(app._session(sid)),app.on_runtime_event)
        app._session(sid)['worktreeHandoffs']=[{'phase':'unknown'}]
        with pytest.raises(ValueError,match='handoff'):
            await runtime.control(sid,'tool.invoke',{'name':'compute'})
        with pytest.raises(ValueError,match='handoff'):
            await runtime.message_worker(sid,'child','new work')
        assert (await runtime.control(sid,'operations.cancel',{}))['ok']
        assert (await runtime.control(sid,'kernels.interrupt',{}))['ok']
        assert (await runtime.stop_worker(sid,'child'))['ok']
        assert (await runtime.approval(sid,'approval','deny'))['ok']
    finally:await app.close()


async def test_late_production_runtime_binding_uses_restored_host_fence(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app._session()['id']
    app._session(sid)['worktreeHandoffs']=[{'phase':'unknown'}]
    runtime=BarrierRuntime();app.runtime=runtime
    app.worktrees.start()
    try:
        with pytest.raises(ValueError,match='handoff'):
            await runtime.start(copy.deepcopy(app._session(sid)),app.on_runtime_event)
        assert not runtime.workers
    finally:await app.close()
