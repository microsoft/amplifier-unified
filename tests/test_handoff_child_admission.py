"""Preparation and nested execution retain exact admitted runs across a move."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('amplifier_module_loop_live')
from amplifier_core import AmplifierSession
from amplifier_module_loop_live.runtime import Runtime
from amplifier_module_loop_live.scope import JOB_CALL
from amplifier_web.handoff_boundary import inspect
from amplifier_web.handoff_drain import begin, native_child
from amplifier_web.host.children import Children
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime_worker import Worker


@pytest.fixture
async def worker(tmp_path):
    session = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live'},
        'context': {'module': 'context-simple'}}, 'providers': []}, session_id='root')
    await session.initialize()
    live = Runtime('root')
    live.generation = {'id': 'original', 'input_ids': ['original-input']}
    value = Worker()
    value.session, value.runtime = session, live
    value.controls = SimpleNamespace(persist=Mock())
    children = Children(live, SessionStore(tmp_path / 'children'), None)
    children.sessions['root'] = session
    session.coordinator.register_capability('live.children', children)
    yield value, children
    marker = getattr(value, '_handoff_boundary', None)
    if marker:
        await inspect(value, marker['id'], cancel=True)
    await session.cleanup()


@pytest.mark.parametrize('nested', [False, True])
async def test_existing_preparation_drains_but_new_spawn_and_resume_stay_fenced(worker, nested):
    value, children = worker
    admitted, release = asyncio.Event(), asyncio.Event()
    completed = []
    async def body(identity, instruction, parent, agent, overlay, *, admission, **kwargs):
        if nested and identity == 'outer':
            child = SimpleNamespace(session_id='outer', coordinator=parent.coordinator)
            await children._execute('inner', 'already admitted', child, 'fixture', {})
        else:
            admitted.set()
            await release.wait()
            row = {'rootSessionId': 'root', 'sessionId': identity, 'lifecycle': 'turn'}
            assert native_child(value, row)['runId'] == admission['runId']
            # Ordinary work in an admitted child continues. A new nested child
            # cannot borrow its JOB_CALL even though that context is inherited.
            completed.append('ordinary-tool')
            child = SimpleNamespace(session_id=identity, coordinator=parent.coordinator)
            for resumed in (False, True):
                with pytest.raises(ValueError, match='waiting for existing workers'):
                    await children._execute('new-grandchild', 'new work', child, 'fixture', {}, resumed=resumed)
        completed.append(identity)
        return {'done': identity}
    children._execute_admitted = body
    token = JOB_CALL.set('original-native-call')
    task = asyncio.create_task(children._execute('outer', 'existing work', value.session, 'fixture', {}))
    JOB_CALL.reset(token)
    await admitted.wait()
    snapshot = await begin(value, 'move')
    assert {entry['sessionId'] for entry in snapshot['children']} == ({'outer', 'inner'} if nested else {'outer'})
    assert not children.rows  # All still preparing, before their first UI row.
    release.set()
    await task
    assert completed == (['ordinary-tool', 'inner', 'outer'] if nested else ['ordinary-tool', 'outer'])
    assert children.admissions == {}
    await inspect(value, value._handoff_boundary['id'], cancel=True)
    assert children.admission_gate is None


async def test_cancelled_preparation_drops_only_its_transient_identity(worker):
    value, children = worker
    admitted = asyncio.Event()
    async def body(*args, **kwargs):
        admitted.set()
        await asyncio.Event().wait()
    children._execute_admitted = body
    task = asyncio.create_task(children._execute('child', 'existing', value.session, 'fixture', {}))
    await admitted.wait()
    snapshot = await begin(value, 'move')
    assert len(snapshot['children']) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children.admissions == {} and children.rows == {}


async def test_accepted_job_before_preparation_can_enter_once_but_not_create_grandchild(worker):
    value, children = worker
    loop = value.session.coordinator.get('orchestrator')
    admitted, release = asyncio.Event(), asyncio.Event()
    async def job():
        await release.wait()
        return await children._execute('child', 'accepted job', value.session, 'fixture', {})
    async def body(identity, instruction, parent, agent, overlay, *, admission, **kwargs):
        admitted.set()
        assert native_child(value, {'sessionId': identity, 'lifecycle': 'turn'})['job']['jobId'] == 'old-job'
        with pytest.raises(ValueError, match='waiting for existing workers'):
            await children._execute('other-child', 'duplicate', parent, 'fixture', {})
        return {'done': identity}
    children._execute_admitted = body
    token = JOB_CALL.set('old-call')
    task = asyncio.create_task(job())
    JOB_CALL.reset(token)
    loop.jobs['old-job'] = {'task': task, 'call_id': 'old-call', 'status': 'pending'}
    snapshot = await begin(value, 'move')
    assert snapshot['children'] == [] and len(snapshot['jobs']) == 1
    release.set()
    await task
    assert admitted.is_set() and not children.admissions
