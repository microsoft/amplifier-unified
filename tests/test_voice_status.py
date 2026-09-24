"""Public-state privacy and deterministic quiet-wait delivery contracts."""
import asyncio
import copy
import json

import pytest

from amplifier_web.voice import VoiceCall, VoiceService, compact_context
from amplifier_web.voice_status import ProgressPacer, projection
from test_voice import Service, Socket


def session(**values):
    return {'id': 'main', 'status': 'working', 'activity': {
        'phase': 'tools', 'startedAt': 100, 'updatedAt': 105,
        'label': 'PRIVATE purpose', 'activeTools': [{'tool': 'read_file', 'input': 'SECRET'}],
    }, **values}


def test_status_has_curated_facts_without_arguments_reasoning_or_raw_error():
    row = session(error='SECRET token', reasoning='PRIVATE reasoning', workers=[{'status': 'working'}])
    result = projection(row, now=123)
    assert result['elapsed_seconds'] == 20 and result['active_tools'] == ['read_file']
    assert result['active_workers'] == 1 and result['response_pending']
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
    result = projection({**row, 'status': 'idle'}, now=123)
    assert result['status'] == 'workers_running' and 'still active' in result['summary']
    result = projection({**row, 'approvals': [{'status': 'pending', 'text': 'SECRET'}]})
    assert result['user_action_required'] and result['status'] == 'waiting_for_approval'
    for raw, expected in [('error', 'failed'), ('interrupted', 'stopped'), ('cancelled', 'stopped')]:
        assert projection({**row, 'status': raw})['status'] == expected


def test_context_remains_valid_bounded_json_and_uses_only_visible_roles():
    state = Service().state
    row = session(title='\x00' * 10000, workers=[{'id': '\x00'*1000, 'title':'\x00'*1000, 'status':'working'}]*100,
                  messages=[{'role': 'user', 'text': '\x00'*10000}]*20 + [
                      {'role': 'tool', 'text':'SECRET'}, {'role':'assistant','thinking':True,'text':'SECRET'},
                      {'role':'system','text':'SECRET'}, {'role':'assistant','ephemeral':True,'text':'SECRET'}])
    state['sessions'] = [row]
    state['view'] = dict.fromkeys(('mode','panel','scheme','layout','selectedWorkerId','contextVisible'), '\x00'*10000)
    encoded = compact_context(state, 'main')
    assert len(encoded) <= 6000 and 'SECRET' not in encoded
    decoded = json.loads(encoded)
    assert decoded['task_status']['elapsed_seconds'] == 5
    assert compact_context(state, 'main') == encoded  # No timer-driven context churn.


def test_quiet_grace_changed_facts_backoff_and_no_idle_timer():
    pacer = ProgressPacer()
    status = projection(session())
    assert pacer.delay(status, now=0) == 10
    assert not pacer.due(status, now=9.99)
    assert pacer.due(status, now=10)
    assert pacer.delay(status, now=10000) is None  # Never repeat unchanged filler.
    changed = {**status, 'phase': 'model', 'active_tools': []}
    assert pacer.delay(changed, now=11) == 14
    assert pacer.due(changed, now=25)
    assert pacer.delay(status, now=26) == 29
    assert pacer.due(status, now=55)
    assert pacer.delay(changed, now=56) == 59
    assert pacer.due(changed, now=115)
    assert pacer.delay(status, now=116) == 119
    assert pacer.delay(projection({'status':'idle'}), now=117) is None
    assert pacer.delay(projection(session(activity={'phase':'model','startedAt':200})), now=118) == 10


def test_approval_failure_and_completion_have_precedence():
    pacer = ProgressPacer()
    active = projection(session())
    assert not pacer.due(active, now=0)
    approval = projection(session(approvals=[{'status':'pending'}]))
    assert pacer.due(approval, now=1)
    assert not pacer.due(approval, now=2)
    failed = projection(session(status='error'))
    pacer.suppress(failed, now=3)  # Confirmed failure result already conveyed.
    assert not pacer.due(failed, now=4)


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_progress_does_not_start_work_and_confirmed_result_is_not_repeated(provider):
    service, socket = Service(), Socket()
    service.state['sessions'] = [session()]
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket, call.provider = 'call', socket, provider
    await call.announce_progress(service.state)
    assert socket.sent == [] and service.calls == []
    call.progress_pacer.first_seen -= 11
    await call.announce_progress(service.state)
    assert len(socket.sent) == 1
    await call.announce_progress(service.state)
    assert len(socket.sent) == 1 and service.calls == []
    call.realtime_responding = False
    service.state['sessions'][0].update(status='error', generations=[{
        'generation_id':'done','event':'generation.finished','text':'Confirmed error result'}])
    await call.announce_generations(service.state)
    delivered = len(socket.sent)
    await call.announce_progress(service.state)
    await call.announce_generations(service.state)
    assert len(socket.sent) == delivered
    assert 'Confirmed error result' in str(socket.sent)


async def test_speaking_blocks_progress_and_changed_fact_discards_pending_notice():
    service, socket = Service(), Socket()
    service.state['sessions'] = [session()]
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket, call.provider = 'call', socket, 'realtime'
    await call.announce_progress(service.state)
    call.progress_pacer.first_seen -= 11
    call.realtime_speaking = True
    await call.announce_progress(service.state)
    assert socket.sent == [] and call.realtime_progress is not None
    service.state['sessions'][0]['status'] = 'idle'
    await call.handle({'type':'input_audio_buffer.speech_stopped'})
    assert socket.sent == [] and not call.realtime_pending_response
    assert service.calls == []


async def test_observer_idle_has_no_polling_timer(monkeypatch):
    service, socket = Service(), Socket()
    service.state['sessions'] = [session(status='idle')]
    queue = asyncio.Queue()
    service.subscribe = lambda: queue
    service.unsubscribe = lambda value: None
    call = VoiceCall(VoiceService(service), 'main')
    call.id, call.socket, call.provider = 'call', socket, 'realtime'
    original = asyncio.wait_for
    delays = []
    async def wait(awaitable, timeout):
        delays.append(timeout)
        return await original(awaitable, timeout)
    monkeypatch.setattr(asyncio, 'wait_for', wait)
    observer = asyncio.create_task(call.observe())
    try:
        await asyncio.sleep(0)
        assert delays == [None] and socket.sent == []
        changed = copy.deepcopy(service.state)
        changed['sessions'][0] = session()
        await queue.put(changed)
        await asyncio.sleep(.4)
        assert 9 <= delays[-1] <= 10
        assert not any(row['type'] == 'response.create' for row in socket.sent)
    finally:
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
