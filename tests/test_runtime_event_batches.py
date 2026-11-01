"""Terminal bursts preserve ordered accounting and durable bridge barriers."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_event_batches import BufferedLines, EventSink, MAX_BYTES, finish_owned
from amplifier_web.state_records import load as load_saved_state
from amplifier_web.session_projection import hydrate, stored_execution
from test_automatic_history import app_factory


def saved_state(app):
    state = load_saved_state(app.db)
    hydrate(app.data_dir, state, app.db)
    return state


def event(identity, *, root='root', **extra):
    return ('execution.event', {'id': identity, 'sessionId': root, 'rootSessionId': root,
        'kind': 'llm', 'phase': 'completed', 'turnId': 'turn', 'startedAt': 1,
        'endedAt': 2, 'revision': 1, 'liveObservation': True, 'usage': {'totalTokens': 3}, **extra})


def wire(value):
    kind, payload = value
    return json.dumps({'type': kind, 'event': payload}).encode() + b'\n'


async def reader(events, *, batch=None, single=None, extra=b'', eof=True, root='root'):
    stream = asyncio.StreamReader(limit=2**20)
    stream.feed_data(b''.join(wire(item) for item in events) + extra)
    if eof:
        stream.feed_eof()
    observed = []
    async def emit(kind, payload):
        observed.append((kind, payload))
        if single:
            await single(kind, payload)
    async def combined(values):
        observed.extend(values)
        if batch:
            await batch(values)
    ready = asyncio.get_running_loop().create_future(); ready.set_result({})
    async def exited(): return 0
    row = {'process': SimpleNamespace(stdout=stream, returncode=0, wait=exited),
        'emit': EventSink(emit, combined), 'closing': True, 'ready': ready,
        'runtime_id': root, 'inputId': 'turn', 'pending': {}, 'bridge_tasks': set()}
    manager = RuntimeManager()
    manager.workers[root] = row
    task = asyncio.create_task(manager._read(root, row))
    return manager, row, task, observed


async def test_event_and_byte_bounds_oversized_singleton_and_order():
    batches, singles = [], []
    values = [event(str(i)) for i in range(35)]
    values += [event('large', kind='tool', output='x' * MAX_BYTES), event('last')]
    async def batch(rows): batches.append([p['id'] for _, p in rows])
    async def single(kind, payload):
        if kind == 'execution.event': singles.append(payload['id'])
    _, _, task, observed = await reader(values, batch=batch, single=single)
    await task
    assert batches == [[str(i) for i in range(16)], [str(i) for i in range(16, 32)], ['32', '33', '34'], ['last']]
    assert singles == ['large']
    assert [p['id'] for k, p in observed if k == 'execution.event'] == [p['id'] for _, p in values]


async def test_aggregate_bytes_split_before_existing_singleton_limit():
    batches = []
    values = [event(str(i), kind='tool', output='x' * 25000) for i in range(5)]
    async def batch(rows): batches.append([p['id'] for _, p in rows])
    _, _, task, _ = await reader(values, batch=batch)
    await task
    assert batches == [['0', '1'], ['2', '3'], ['4']]


@pytest.mark.parametrize('barrier', [
    {'type':'runtime.status','status':'idle'},
    {'type':'runtime.tool','tool':'read','phase':'post'},
    {'type':'approval.requested','id':'approval'},
    {'type':'generation.finished','sessionId':'root','rootSessionId':'root'},
    {'type':'runtime.ownership','status':'yielded'},
    {'op':'reply','id':'reply','result':{}},
    {'type':'execution.event','event':event('running',phase='running')[1]},
    {'type':'execution.event','event':event('other',root='other')[1]},
    {'type':'worker.updated','id':'worker','status':'completed'},
])
async def test_every_noneligible_frame_is_a_barrier(barrier):
    batches = []
    async def batch(rows): batches.append([p['id'] for _, p in rows])
    _, row, task, _ = await reader([event('before')], batch=batch,
        extra=json.dumps(barrier).encode()+b'\n'+wire(event('after')))
    row['inflight'] = set()
    await task
    assert batches == [['before'], ['after']]


async def test_partial_line_does_not_delay_commit_and_read_is_reaped():
    delivered = asyncio.Event()
    async def batch(_): delivered.set()
    _, row, task, _ = await reader([event('one')], batch=batch, extra=b'{"type":', eof=False)
    await asyncio.wait_for(delivered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert row['process'].stdout._waiter is None


@pytest.mark.parametrize('count', [2, 16])
async def test_buffered_collection_has_one_fairness_turn_under_replenished_load(count):
    stream = asyncio.StreamReader()
    frames = [f'{index}\n'.encode() for index in range(count)]
    stream.feed_data(b''.join(frames) + b'barrier\n'); stream.feed_eof()
    lines = BufferedLines(stream)
    first = await lines.next()
    turns = 0
    running = True
    loop = asyncio.get_running_loop()
    def background():
        nonlocal turns
        turns += 1
        if running:
            loop.call_soon(background)
    loop.call_soon(background)
    try:
        rows = await lines.collect(first, first, lambda line: line if line in frames else None)
        # An always-ready background owner gets a turn, but not another turn
        # for every already-buffered receipt. No wall-clock threshold is used.
        assert 1 <= turns <= 2
        assert rows == frames
        assert await lines.next() == b'barrier\n'
        assert await lines.next() == b''
    finally:
        running = False
        await lines.close()


def starting_frames(count=5, root='root'):
    frames = []
    for index in range(count):
        start = event(f'tool-{index}', root=root, kind='tool', phase='running',
                      endedAt=None, toolCallId=f'call-{index}', label='read_file')
        start[1].pop('usage')
        frames.extend([wire(start),
            observation_wire('runtime.activity', phase='tools', detail='Running read_file.'),
            observation_wire('tool.pre', tool='read_file', call_id=f'call-{index}')])
    return frames


async def test_buffered_tool_starts_share_bounded_turns_before_effect_barrier():
    frames = starting_frames(13)
    turns, running, observed_turns, commands = 0, True, [], []
    loop = asyncio.get_running_loop()
    def background():
        nonlocal turns
        turns += 1
        if running: loop.call_soon(background)
    async def single(kind, payload):
        if kind != 'runtime.ended': observed_turns.append((kind, turns))
    manager, row, task, observed = await reader([], single=single,
        extra=b''.join(frames) + b'{"op":"bridge","id":"effect","operation":"work.effect.check","args":{}}\n')
    async def bridge(*args, **kwargs): commands.append(len(observed_turns))
    manager._bridge = bridge
    loop.call_soon(background)
    try:
        await task
        await asyncio.gather(*row['bridge_tasks'])
    finally: running = False
    assert commands == [39]
    assert [kind for kind, _ in observed_turns] == ['execution.event', 'runtime.status', 'runtime.tool'] * 13
    # Exactly three bounded prefixes, not one competing scheduler turn per
    # already-buffered observation. This measures scheduling, not wall time.
    assert len({turn for _, turn in observed_turns}) == 3
    assert all(len([1 for _, seen in observed_turns if seen == turn]) <= 16
               for _, turn in observed_turns)


@pytest.mark.parametrize('barrier', [
    {'op': 'bridge', 'operation': 'work.effect.check'},
    {'type': 'approval.requested', 'id': 'approval'},
    {'type': 'generation.started', 'sessionId': 'root', 'rootSessionId': 'root'},
    {'type': 'input.delivered', 'input_id': 'new'},
    {'type': 'runtime.ownership', 'status': 'yielded'},
    {'type': 'collaboration.checkpoint', 'generation_id': 'generation'},
    {'type': 'unrecognized.observation'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Running', 'report': {}},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Running', 'inputId': 'new'},
    {'type': 'runtime.activity', 'phase': [], 'detail': 'Running'},
    {'type': 'tool.pre', 'tool': 'read_file', 'call_id': 'call', 'session_id': 'child'},
    {'type': 'tool.pre', 'tool': 'read_file', 'call_id': 'call', 'root_session_id': 'child'},
    {'type': 'execution.event', 'event': event('child', root='root', sessionId='child', kind='tool', phase='running')[1]},
    {'type': 'execution.event', 'event': event('bad', root='root', kind='tool', phase='running', statusPlan={})[1]},
])
def test_start_prefix_authority_unknown_and_child_frames_reset_fairness(barrier):
    from amplifier_web.runtime_event_batches import ObservationTurns, starting_observation
    progress = {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Running'}
    roots = ('root', 'native-root')
    turns = ObservationTurns()
    assert turns.yield_before(100, starting_observation(progress, roots))
    assert not turns.yield_before(100, starting_observation(progress, roots))
    assert not starting_observation(barrier, roots)
    assert turns.yield_before(100, starting_observation(barrier, roots))
    assert turns.yield_before(100, starting_observation(progress, roots))


def test_start_prefix_bytes_and_native_alias_are_bounded():
    from amplifier_web.runtime_event_batches import ObservationTurns, starting_observation
    progress = {'type': 'tool.pre', 'tool': 'read_file', 'call_id': 'call', 'session_id': 'native-root'}
    assert starting_observation(progress, ('root', 'native-root'))
    turns = ObservationTurns()
    assert turns.yield_before(40000, True)
    assert turns.yield_before(40000, True)
    assert not turns.yield_before(100, True)
    assert turns.yield_before(MAX_BYTES + 1, True)
    assert turns.yield_before(100, True)


@pytest.mark.parametrize('missing', ['sessionId', 'rootSessionId'])
@pytest.mark.parametrize('value', ['absent', None, '', [], {}])
def test_start_prefix_missing_or_invalid_identity_is_a_barrier(missing, value):
    from amplifier_web.runtime_event_batches import starting_observation
    data = json.loads(starting_frames(1)[0])
    if value == 'absent': data['event'].pop(missing)
    else: data['event'][missing] = value
    assert not starting_observation(data, ('root', None))


@pytest.mark.parametrize('barrier', [
    {'op': 'bridge', 'id': 'check', 'operation': 'work.effect.check', 'args': {}},
    {'type': 'approval.requested', 'id': 'approval'},
    {'type': 'generation.started', 'sessionId': 'root', 'rootSessionId': 'root'},
    {'type': 'input.delivered', 'input_id': 'new'},
    {'type': 'unrecognized.observation'},
    {'type': 'execution.event', 'event': event('child', root='root', sessionId='child', kind='tool', phase='running')[1]},
])
async def test_start_prefix_keeps_barrier_between_exact_observations(barrier):
    turns, running, order = 0, True, []
    loop = asyncio.get_running_loop()
    def background():
        nonlocal turns
        turns += 1
        if running: loop.call_soon(background)
    async def single(kind, payload):
        if kind != 'runtime.ended': order.append((payload.get('id', kind), turns))
    before = json.loads(starting_frames(1)[0]); before['event']['id'] = 'before'
    after = copy.deepcopy(before); after['event']['id'] = 'after'
    manager, row, task, _ = await reader([], single=single,
        extra=b''.join(json.dumps(frame).encode() + b'\n' for frame in (before, barrier, after)))
    async def bridge(*args, **kwargs): order.append(('command', turns))
    manager._bridge = bridge
    loop.call_soon(background)
    try:
        await task; await asyncio.gather(*row['bridge_tasks'])
    finally: running = False
    assert order[0][0] == 'before' and order[-1][0] == 'after'
    assert order[-1][1] - order[0][1] >= 2
    if barrier.get('op'): assert order[1][0] == 'command'
    elif barrier['type'] != 'unrecognized.observation': assert len(order) == 3


async def test_start_prefix_callback_takeover_and_fault_do_not_replay_or_cross_command():
    old, new, commands = [], [], []
    async def first(kind, payload):
        old.append(kind)
        row['emit'] = replacement
    async def replacement(kind, payload):
        if kind == 'runtime.ended': return
        new.append(kind)
        if kind == 'runtime.tool': raise ValueError('observer failed')
    manager, row, task, _ = await reader([], single=first,
        extra=b''.join(starting_frames(2)) + b'{"op":"bridge","id":"blocked","operation":"list_actions","args":{}}\n')
    async def bridge(*args, **kwargs): commands.append('called')
    manager._bridge = bridge
    await task
    assert old == ['execution.event']
    assert new[:2] == ['runtime.status', 'runtime.tool']
    assert new.count('execution.event') == 0 and not commands


async def test_start_prefix_cancellation_preserves_applied_prefix_without_command():
    entered, release, observed = asyncio.Event(), asyncio.Event(), []
    async def single(kind, payload):
        if kind == 'execution.event':
            observed.append(payload['id']); entered.set(); await release.wait()
    manager, row, task, _ = await reader([], single=single,
        extra=b''.join(starting_frames(2)) + b'{"op":"bridge","id":"blocked","operation":"list_actions","args":{}}\n')
    calls = []
    async def bridge(*args, **kwargs): calls.append('called')
    manager._bridge = bridge
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert observed == ['tool-0'] and not calls and row['process'].stdout._waiter is None


async def test_start_prefix_keeps_single_reducers_progress_and_saved_state(app_factory, monkeypatch):
    app = app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']; session = app._session(sid)
    session['status'] = 'working'
    monkeypatch.setattr('amplifier_web.service.time.time', lambda: 1234.0)
    initial = copy.deepcopy(session)
    frames = starting_frames(5, sid)
    from amplifier_web.runtime import normalize_event
    for line in frames:
        await app.on_runtime_event(*normalize_event(json.loads(line), sid, 'turn'))
    await app._flush_pending_progress()
    expected = copy.deepcopy(session)
    session.clear(); session.update(initial); app._publish_changes(sessions={sid})
    commits = []
    original = app._commit_pending_progress
    def commit(): commits.append(1); return original()
    monkeypatch.setattr(app, '_commit_pending_progress', commit)
    async def single(kind, payload):
        if kind != 'runtime.ended':
            await app.on_runtime_event(kind, {**payload, 'sessionId': sid})
    _, _, task, _ = await reader([], single=single, extra=b''.join(frames), root=sid)
    await task
    await app._flush_pending_progress()
    assert session == expected and len(commits) == 1
    saved = next(s for s in saved_state(app)['sessions'] if s['id'] == sid)
    assert saved['execution'] == stored_execution(expected['execution'])


async def test_late_read_is_retained_without_mutating_the_committed_prefix():
    stream = asyncio.StreamReader()
    stream.feed_data(b'first\nsecond\npar')
    lines = BufferedLines(stream)
    accepted = []
    def accept(line):
        accepted.append(line)
        return line
    rows = await lines.collect(await lines.next(), b'first\n', accept)
    assert rows == [b'first\n', b'second\n']
    stream.feed_data(b'tial\nafter\n'); stream.feed_eof()
    await asyncio.sleep(0)
    assert rows == [b'first\n', b'second\n'] and accepted == [b'second\n']
    assert await lines.next() == b'partial\n'
    assert await lines.next() == b'after\n'
    assert await lines.next() == b''
    await lines.close()


@pytest.mark.parametrize('outcome', ['error', 'cancel', 'eof'])
async def test_pending_read_outcome_remains_after_returned_prefix(outcome):
    stream = asyncio.StreamReader()
    stream.feed_data(b'first\npartial')
    lines = BufferedLines(stream)
    accepted = []
    rows = await lines.collect(await lines.next(), b'first\n', accepted.append)
    assert rows == [b'first\n'] and not lines.pending.done()
    if outcome == 'error':
        stream.set_exception(OSError('late read failed'))
        with pytest.raises(OSError, match='late read failed'):
            await lines.next()
    elif outcome == 'cancel':
        lines.pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await lines.next()
    else:
        stream.feed_eof()
        assert await lines.next() == b'partial'
        assert await lines.next() == b''
    assert rows == [b'first\n'] and accepted == []
    await lines.close()
    assert stream._waiter is None


async def test_repeated_cancellation_finishes_consumed_batch_but_not_bridge():
    entered, release, committed = asyncio.Event(), asyncio.Event(), []
    async def batch(rows):
        entered.set(); await release.wait(); committed.extend(p['id'] for _, p in rows)
    manager, row, task, _ = await reader([event('one'), event('two')], batch=batch,
        extra=b'{"op":"bridge","id":"never","operation":"capacity.admit"}\n')
    calls = []
    async def bridge(*args): calls.append(args)
    manager.app_bridge = bridge
    await entered.wait()
    task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
    assert not task.done() and not calls
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert committed == ['one', 'two'] and not calls and not row['bridge_tasks']


async def test_callback_replacement_during_collection_uses_current_owner():
    old, new = [], []
    async def batch(rows): old.extend(p['id'] for _, p in rows)
    manager, row, task, _ = await reader([event('one'), event('two')], batch=batch)
    original = row['process'].stdout.readline
    count = 0
    async def replacement(kind, payload):
        if kind == 'execution.event': new.append(payload['id'])
    async def read():
        nonlocal count
        line = await original(); count += 1
        if count == 2: row['emit'] = replacement
        return line
    row['process'].stdout.readline = read
    await task
    assert old == [] and new == ['one', 'two']


async def test_service_batch_equals_single_events_and_preserves_observer_order(app_factory, monkeypatch):
    app = app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']; session = app._session(sid)
    initial = copy.deepcopy(session)
    values = [event('same',root=sid), event('same',root=sid,revision=2,usage={'totalTokens':7}),
        event('child',root=sid,sessionId='child',kind='tool',output='bounded result')]
    for item in values: await app.on_runtime_event(*item)
    expected = copy.deepcopy(session['execution'])
    session.clear(); session.update(initial)
    app._publish_changes(sessions={sid})
    order = []
    original = app.collaboration.observe
    def observe(session, kind, payload):
        order.append((payload['id'],payload['revision']))
        return original(session,kind,payload)
    monkeypatch.setattr(app.collaboration,'observe',observe)
    revision = app.state['revision']; queue = app.subscribe()
    await app.on_runtime_event.batch(values)
    assert session['execution'] == expected
    assert order == [('same',1),('same',2),('child',1)]
    assert app.state['revision'] == revision+1 and queue.qsize()==1
    saved = saved_state(app)
    assert next(s for s in saved['sessions'] if s['id']==sid)['execution'] == stored_execution(expected)
    app.unsubscribe(queue)


async def test_failed_commit_does_not_replay_observers_or_publish_and_flushes_pending_union(app_factory, monkeypatch):
    app = app_factory(); await app.history.close(); await app.diagnostics.close()
    rows = []
    for _ in range(12):
        await app.dispatch('session.create', {}); rows.append(app.state['selectedSessionId'])
    for sid in rows: await app.on_runtime_event('assistant.delta',{'sessionId':sid,'text':'pending'})
    seen=[]; original_observe=app.collaboration.observe
    def observe(session,kind,payload):
        seen.append(payload['id']); return original_observe(session,kind,payload)
    monkeypatch.setattr(app.collaboration,'observe',observe)
    save=app._save; revision=app.state['revision']; queue=app.subscribe()
    def fail(): raise OSError('synthetic disk failure')
    monkeypatch.setattr(app,'_save',fail)
    with pytest.raises(OSError): await app.on_runtime_event.batch([event('a',root=rows[0]),event('b',root=rows[0])])
    assert app.state['revision']==revision and queue.empty()
    assert app._progress_session_ids==set(rows) and seen==['a','b']
    monkeypatch.setattr(app,'_save',save)
    app._commit_pending_progress()
    assert seen==['a','b'] and not app._progress_dirty
    saved=saved_state(app)
    assert all(s.get('streaming')=='pending' for s in saved['sessions'])
    assert len(next(s for s in saved['sessions'] if s['id']==rows[0])['execution']['retiredUsageNodes'])==2
    app.unsubscribe(queue)

async def test_valid_prefix_commits_before_next_read_or_decoder_failure():
    for decode_failure in (False, True):
        stream=asyncio.StreamReader(limit=100)
        stream.feed_data(b'ok\n'+b'x'*200+b'\n'); stream.feed_eof()
        lines=BufferedLines(stream); first=await lines.next()
        def accept(_): raise ValueError('decoder failed')
        if decode_failure:
            # Exercise an accept error independently of the StreamReader limit.
            stream._limit=1000
        rows=await lines.collect(first, 'receipt', accept)
        assert rows==['receipt']
        with pytest.raises(ValueError): await lines.next()
        await lines.close()


@pytest.mark.parametrize('observer', ['diagnostics','ingest','collaboration'])
async def test_reducer_fault_preserves_prefix_without_replay(app_factory,monkeypatch,observer):
    app=app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create',{}); sid=app.state['selectedSessionId']
    if observer=='diagnostics': owner,name=app.diagnostics,'runtime_event'
    elif observer=='ingest': owner,name=app,'_ingest_runtime_execution'
    else: owner,name=app.collaboration,'observe'
    original=getattr(owner,name); calls=[]
    def fail(*args):
        payload=args[1] if observer=='ingest' else args[1] if observer=='diagnostics' else args[2]
        calls.append(payload['id'])
        if payload['id']=='bad': raise ValueError('observer failed')
        return original(*args)
    monkeypatch.setattr(owner,name,fail)
    with pytest.raises(ValueError,match='observer failed'):
        await app.on_runtime_event.batch([event('first',root=sid),event('bad',root=sid),event('unapplied',root=sid)])
    saved=saved_state(app)
    nodes=next(s for s in saved['sessions'] if s['id']==sid)['execution']['retiredUsageNodes']
    assert 'first' in {n['id'] for n in nodes} and 'unapplied' not in {n['id'] for n in nodes}
    assert calls==['first','bad']


async def test_bridge_never_advances_after_failed_batch():
    async def fail(_): raise OSError('save failed')
    manager,row,task,_=await reader([event('first')],batch=fail,
        extra=b'{"op":"bridge","id":"blocked","operation":"capacity.admit"}\n')
    calls=[]
    async def bridge(*args): calls.append(args)
    manager.app_bridge=bridge
    await task
    assert not calls and not row['bridge_tasks']


async def test_actual_stop_reaps_reader_after_contended_service_commit(app_factory,tmp_path):
    app=app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create',{}); sid=app.state['selectedSessionId']
    fixture=tmp_path/'worker.py'
    fixture.write_text('''import sys,json
for line in sys.stdin:
 d=json.loads(line)
 if d['op']=='start':
  s=d['session']['id'];print(json.dumps({'type':'runtime.ready'}),flush=True)
 elif d['op']=='send':
  print(json.dumps({'type':'execution.event','event':{'id':'terminal','kind':'llm','phase':'completed','sessionId':s,'rootSessionId':s,'liveObservation':True,'usage':{'totalTokens':9}}}),flush=True)
 elif d['op']=='stop':break
''')
    manager=RuntimeManager(command=[sys.executable,str(fixture)],startup_timeout=2)
    try:
        await manager.start({'id':sid,'workspace':str(tmp_path)},app.on_runtime_event)
        row=manager.workers[sid]; entered=asyncio.Event(); original=app.on_runtime_event.batch
        async def batch(events): entered.set(); await original(events)
        row['emit']=EventSink(app.on_runtime_event,batch)
        await app.lock.acquire()
        try:
            await manager._write(row,{'op':'send'})
            await asyncio.wait_for(entered.wait(),2)
            stopping=asyncio.create_task(manager.stop(sid,reason='shutdown'))
            await asyncio.sleep(.02)
            assert not stopping.done()
        finally: app.lock.release()
        await asyncio.wait_for(stopping,2)
        assert row['reader'].done() and row['process'].returncode==0
        saved=saved_state(app)
        assert next(s for s in saved['sessions'] if s['id']==sid)['execution']['retiredUsageNodes'][0]['usage']['totalTokens']==9
    finally:
        if app.lock.locked(): app.lock.release()
        await manager.close()

async def test_production_callback_start_wrapper_send_takeover_and_budget_barrier(app_factory,tmp_path,monkeypatch):
    from amplifier_web import capacity
    from test_runtime import FIXTURE
    app=app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create',{}); sid=app.state['selectedSessionId']
    # Persisted policy is fixed for this test; the actual admission/usage/save
    # path must see both preceding root and child completions before deciding.
    monkeypatch.setattr(capacity,'saved_policy',lambda *_: {'enabled':True,'revision':1,
        'maxTotalTokens':5,'maxCostUsd':None,'unknownPolicy':'allow','warningFraction':.8})
    body=FIXTURE.replace("'phase':'completed','sessionId':identity", "'phase':'completed','usage':{'totalTokens':3},'sessionId':identity")
    body=body.replace("'operation':'get_state','args':{}", "'operation':'capacity.admit','args':{'call':{'id':'blocked','kind':'llm','phase':'running','sessionId':root,'rootSessionId':root}}")
    body=body.replace("elif op=='stop':", "elif op=='resume':\n        print(json.dumps({'op':'reply','id':data['id'],'result':{}}),flush=True)\n    elif op=='stop':")
    fixture=tmp_path/'worker.py'; fixture.write_text(body)
    replies=[]; bridge_done=asyncio.Event()
    async def bridge(op,args,identity):
        result=await app.app_bridge(op,args,identity)
        replies.append(result); bridge_done.set(); return result
    manager=RuntimeManager(bridge,command=[sys.executable,str(fixture)],startup_timeout=2)
    session={'id':sid,'workspace':str(tmp_path)}
    app._session(sid)['workers']=[{'id':'child','sessionId':sid+'-child'}]
    async def wrapped(kind,payload): await app.on_runtime_event(kind,payload)
    try:
        await manager.start(session,wrapped)
        await manager.start(session,app.on_runtime_event,preserve_emit=True)
        assert manager.workers[sid]['emit'] is wrapped
        await manager.send(session,'synthetic','turn',app.on_runtime_event)
        await asyncio.wait_for(bridge_done.wait(),2)
        assert manager.workers[sid]['emit'] is app.on_runtime_event
        assert replies[0]['allowed'] is False and replies[0]['state']=='paused'
        assert 'blocked' not in {n['id'] for n in app._session(sid)['execution']['nodes']}
        replacement=EventSink(app.on_runtime_event,app.on_runtime_events)
        await manager.takeover(session,replacement)
        assert manager.workers[sid]['emit'] is replacement
    finally: await manager.close()


async def test_parse_once_for_progress_and_retained_barrier(monkeypatch):
    import amplifier_web.runtime as runtime
    original=runtime.json.loads; parsed=[]
    def decode(value,*args,**kw):
        parsed.append(value); return original(value,*args,**kw)
    monkeypatch.setattr(runtime.json,'loads',decode)
    frames=[wire(event('one')),b'{"type":"assistant.delta","text":"a"}\n',
        b'{"type":"assistant.delta","text":"b"}\n',wire(event('two'))]
    _,_,task,_=await reader([],extra=b''.join(frames))
    await task
    assert parsed==frames

async def test_repeated_cancel_during_read_cleanup_still_settles_pending(monkeypatch):
    entered,release=asyncio.Event(),asyncio.Event()
    original=BufferedLines.close
    async def held_close(self):
        entered.set(); await release.wait(); await original(self)
    monkeypatch.setattr(BufferedLines,'close',held_close)
    _,row,task,_=await reader([])
    future=asyncio.get_running_loop().create_future()
    row['pending']['command']=future;row['sendInputs']={'command':'input'}
    await entered.wait()
    task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
    assert not future.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert not row['sendInputs']
    with pytest.raises(RuntimeError,match='disconnected'):future.result()


@pytest.mark.parametrize('failure',['read','normalize'])
async def test_valid_receipt_durable_before_next_transport_fault(app_factory,monkeypatch,failure):
    import amplifier_web.runtime as runtime
    app=app_factory();await app.history.close();await app.diagnostics.close()
    await app.dispatch('session.create',{})
    sid=app.state['selectedSessionId']
    if failure=='normalize':
        original=runtime.normalize_event
        def normalize(data,*args):
            if data.get('event',{}).get('id')=='bad': raise ValueError('normalizer failed')
            return original(data,*args)
        monkeypatch.setattr(runtime,'normalize_event',normalize)
        tail=wire(event('bad'))
    else:tail=b'x'*2048+b'\n'
    async def commit(events):
        await app.on_runtime_events([(kind,{**payload,'sessionId':sid,'rootSessionId':sid}) for kind,payload in events])
    manager,row,task,_=await reader([event('first')],batch=commit,
        extra=tail+b'{"op":"bridge","id":"blocked","operation":"capacity.admit"}\n')
    if failure=='read':row['process'].stdout._limit=1024
    calls=[]
    async def bridge(*args):calls.append(args)
    manager.app_bridge=bridge
    await task
    saved=saved_state(app)
    receipts=next(s for s in saved['sessions'] if s['id']==sid)['execution']['retiredUsageNodes']
    assert [p['id'] for p in receipts]==['first'] and not calls

async def test_batch_commits_cross_session_observer_effects(app_factory,monkeypatch):
    app=app_factory();await app.history.close();await app.diagnostics.close()
    for _ in range(2):await app.dispatch('session.create',{})
    sid,peer=[s['id'] for s in app.state['sessions']]
    original=app.collaboration.observe
    def observe(session,kind,payload):
        changed=original(session,kind,payload)
        app._session(peer)['description']='observed terminal receipt'
        return changed|{peer}
    monkeypatch.setattr(app.collaboration,'observe',observe)
    await app.on_runtime_event.batch([event('one',root=sid),event('two',root=sid)])
    saved=saved_state(app)
    assert next(s for s in saved['sessions'] if s['id']==peer)['description']=='observed terminal receipt'
    assert len(next(s for s in saved['sessions'] if s['id']==sid)['execution']['retiredUsageNodes'])==2


def observation_wire(kind, **fields):
    return json.dumps({'type': kind, **fields}).encode() + b'\n'


async def test_terminal_batch_keeps_observations_ordered_before_command_barrier():
    frames = (observation_wire('runtime.activity', phase='tools', detail='Reading')
        + observation_wire('runtime.bridge', bridgeId='bridge', operation='work.effect.check',
                           runtimeSessionId='root', phase='completed', elapsedSeconds=0.1)
        + observation_wire('runtime.timing', timing={})
        + wire(event('second'))
        + b'{"op":"bridge","id":"next","operation":"work.effect.check","args":{}}\n')
    order = []
    async def batch(rows):
        order.append(('commit', [kind for kind, _ in rows]))
    manager, row, task, observed = await reader([event('first')], batch=batch, extra=frames)
    async def bridge(*args, **kwargs):
        order.append(('command',))
    manager._bridge = bridge
    await task
    await asyncio.gather(*row['bridge_tasks'])
    assert order == [('commit', ['execution.event', 'runtime.status', 'runtime.bridge',
                                 'runtime.timing', 'execution.event']), ('command',)]
    assert all(payload.get('rootSessionId', payload.get('sessionId')) == 'root'
               for kind, payload in observed if kind != 'runtime.ended')


@pytest.mark.parametrize('barrier', [
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'report': {}},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'inputId': 'input'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'status': 'ready'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'rootSessionId': 'other'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'runtimeSessionId': 'other'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'session_id': 'other'},
    {'type': 'runtime.activity', 'phase': 'tools', 'detail': 'Reading', 'root_session_id': 'other'},
    {'type': 'runtime.bridge', 'bridgeId': 'bridge', 'runtimeSessionId': 'child'},
    {'type': 'runtime.bridge', 'bridgeId': 'bridge', 'args': {'action': 'anything'}},
    {'type': 'runtime.timing', 'timing': {}, 'unknown': True},
    {'type': 'runtime.timing', 'timing': {}, 'sessionId': 'child'},
    {'type': 'unrecognized.observation', 'detail': 'Do not guess'},
])
async def test_unknown_fields_and_nonroot_observations_remain_barriers(barrier):
    batches = []
    async def batch(rows): batches.append([p['id'] for _, p in rows])
    _, _, task, _ = await reader([event('before')], batch=batch,
        extra=json.dumps(barrier).encode() + b'\n' + wire(event('after')))
    await task
    assert batches == [['before'], ['after']]


def mixed_receipts(sid):
    return [event('first', root=sid),
        ('runtime.status', {'sessionId': sid, 'status': 'working', 'activityOnly': True,
                            'phase': 'tools', 'detail': '', 'activeWorkers': 0}),
        ('runtime.bridge', {'sessionId': sid, 'type': 'runtime.bridge', 'bridgeId': 'bridge',
                            'operation': 'work.effect.check', 'phase': 'completed'}),
        ('runtime.timing', {'sessionId': sid, 'timing': {}}),
        event('second', root=sid)]


@pytest.mark.parametrize('status', ['working', 'starting', 'idle', 'error'])
async def test_mixed_batch_uses_same_reducer_and_order_as_single_delivery(app_factory, monkeypatch, status):
    from amplifier_web import conversation_steering
    app = app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']; session = app._session(sid)
    session['status'] = status
    app._activity(session, 'tools', 'Running tools')['activeTools'] = [{'tool': 'read_file'}]
    monkeypatch.setattr('amplifier_web.service.time.time', lambda: 1234.0)
    initial = copy.deepcopy(session); order = []
    for owner, name in ((app.diagnostics, 'runtime_event'), (app.collaboration, 'observe'),
                        (conversation_steering, 'observe')):
        original = getattr(owner, name)
        def observe(*args, _original=original, _name=name, _owner=owner):
            if _owner is app.diagnostics: kind, payload = args[:2]
            elif _owner is conversation_steering: kind, payload = args[2:]
            else: kind, payload = args[1:]
            order.append((_name, kind, payload.get('id', payload.get('bridgeId'))))
            return _original(*args)
        monkeypatch.setattr(owner, name, observe)
    values = mixed_receipts(sid)
    for item in values: await app.on_runtime_event(*item)
    await app._flush_pending_progress()
    expected = copy.deepcopy(session); expected_order = list(order)
    session.clear(); session.update(initial); app._publish_changes(sessions={sid}); order.clear()
    revision = app.state['revision']; queue = app.subscribe()
    await app.on_runtime_event.batch(values)
    assert session == expected
    assert order == expected_order
    assert app.state['revision'] == revision + 1 and queue.qsize() == 1
    saved = saved_state(app)
    assert next(s for s in saved['sessions'] if s['id'] == sid)['execution'] == stored_execution(expected['execution'])
    assert session['status'] == ('working' if status == 'starting' else status)
    app.unsubscribe(queue)


async def test_observation_failure_commits_terminal_prefix_without_replay(app_factory, monkeypatch):
    app = app_factory(); await app.history.close(); await app.diagnostics.close()
    await app.dispatch('session.create', {}); sid = app.state['selectedSessionId']
    app._session(sid)['status'] = 'working'
    original = app.diagnostics.runtime_event; seen = []
    def fail(kind, payload, session):
        seen.append(kind)
        if kind == 'runtime.bridge': raise ValueError('observation failed')
        return original(kind, payload, session)
    monkeypatch.setattr(app.diagnostics, 'runtime_event', fail)
    with pytest.raises(ValueError, match='observation failed'):
        await app.on_runtime_event.batch(mixed_receipts(sid))
    saved = saved_state(app)
    nodes = next(s for s in saved['sessions'] if s['id'] == sid)['execution']['retiredUsageNodes']
    assert {n['id'] for n in nodes} == {'first'}
    assert seen == ['execution.event', 'runtime.status', 'runtime.bridge']


async def test_cancellation_and_callback_takeover_keep_mixed_consumed_prefix():
    entered, release = asyncio.Event(), asyncio.Event()
    old, new = [], []
    async def batch(rows):
        entered.set(); await release.wait(); old.extend(kind for kind, _ in rows)
    manager, row, task, _ = await reader([event('first')], batch=batch,
        extra=observation_wire('runtime.activity', phase='tools', detail='Reading') + wire(event('second'))
        + b'{"op":"bridge","id":"next","operation":"work.effect.check","args":{}}\n')
    commands = []
    async def bridge(*args, **kwargs): commands.append(args)
    manager._bridge = bridge
    await entered.wait(); task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
    assert not task.done() and not commands
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert old == ['execution.event', 'runtime.status', 'execution.event'] and not commands

    async def current(kind, payload): new.append(kind)
    _, row, task, _ = await reader([event('first')],
        extra=observation_wire('runtime.activity', phase='tools', detail='Reading') + wire(event('second')))
    original = row['process'].stdout.readline; reads = 0
    async def replacement():
        nonlocal reads
        result = await original(); reads += 1
        if reads == 3: row['emit'] = current
        return result
    row['process'].stdout.readline = replacement
    await task
    assert new[:3] == ['execution.event', 'runtime.status', 'execution.event']
