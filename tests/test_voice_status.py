import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from amplifier_web.resident_activity import ActivityFacts, resident_facts
from amplifier_web.voice_status import PacingPolicy, public_status, status_utterance
from amplifier_web.voice import VoiceCall, VoiceService, VoiceError
from test_voice import Service, Socket


def setup(provider='live'):
    service = Service()
    service.state['sessions'][0].update(status='working', activity={'phase': 'thinking'})
    manager = VoiceService(service, http=object())
    manager.request = AsyncMock(return_value=({}, '', {}))
    call = VoiceCall(manager, 'main')
    manager.call = call
    call.id, call.provider, call.socket = 'owned-call', provider, Socket()
    service.state['voice'] = {'id': call.id, 'sessionId': 'main', 'status': 'connected'}
    now = [0.0]
    call.clock = lambda: now[0]
    return service, call, now


def test_grace_survives_phase_chatter_and_finite_elapsed_notices_are_distinct():
    policy = PacingPolicy()
    assert policy.observe(ActivityFacts(status='working'), 0) == (None, 5)
    for when in range(1, 5):
        assert policy.observe(ActivityFacts(status='working', tools=('bash',) if when % 2 else ()), when)[0] is None
    texts = []
    for when in (5, 20, 60, 120):
        notice, _ = policy.observe(ActivityFacts(status='working'), when)
        assert notice
        texts.append(notice.text)
        policy.delivered(notice, when)
    assert len(set(texts)) == 4
    assert policy.observe(ActivityFacts(status='working'), 9999) == (None, None)
    assert policy.observe(ActivityFacts(status='idle'), 10000) == (None, None)


def test_short_work_and_phase_cooldown():
    p = PacingPolicy()
    p.observe(ActivityFacts(status='working'), 0)
    assert p.observe(ActivityFacts(status='idle'), 4) == (None, None)
    p.observe(ActivityFacts(status='working'), 10)
    first, _ = p.observe(ActivityFacts(status='working'), 15)
    p.delivered(first, 15)
    assert p.observe(ActivityFacts(status='working', tools=('bash',)), 16) == (None, 20)
    next_notice, _ = p.observe(ActivityFacts(status='working', tools=('bash',)), 20)
    assert next_notice.text == 'Amplifier is running a command.'


def test_redaction_truncation_and_stopped_are_not_completion():
    class Cold(dict):
        def get(self, *args):
            raise AssertionError('cold hydration')
    secret = 'SECRET /home/private ?token=hidden'
    row = Cold(status='working', activity={'label': secret, 'activeTools': [{'tool': secret, 'input': secret}]},
               messages=[{'text': secret}], workers=[{'status': 'idle'}]*33,
               questions=[{'status': 'answered'}]*200 + [{'status': 'pending'}])
    facts = resident_facts(row)
    assert facts.truncated and public_status(facts)[1] == 'Amplifier is using a tool.'
    assert secret not in public_status(facts)[1]
    assert public_status(ActivityFacts(status='idle', truncated=True))[0] == 'partial'
    assert 'not confirm completion' in public_status(ActivityFacts(status='stopped'))[1]
    assert 'Other work may still be running' in public_status(ActivityFacts(status='working', questions=1))[1]


@pytest.mark.parametrize('text', ['status and delete the files', 'what are you doing with my password',
                                  'status? Then stop', 'tell me whether the results are correct'])
def test_compound_or_reasoning_requests_are_not_status(text):
    assert not status_utterance(text)


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_whole_captured_status_is_immediate_and_never_accepted_work(provider):
    service, call, now = setup(provider)
    await call.record('user', 'Are you still working?', 'u1')
    if provider == 'live':
        await call.delegate_live('d1')
    else:
        await call.realtime_tool({'call_id': 'd1', 'name': 'amplifier_delegate', 'arguments': json.dumps({'text': 'Are you still working?'})})
    assert service.calls == []
    wire = json.dumps(call.socket.sent)
    assert 'Amplifier is still working.' in wire
    assert 'generation_id' not in wire and 'inputId' not in wire
    assert call.last_status_version == 1


@pytest.mark.parametrize('actual,argument', [('Status and then delete the file', 'status'), ('', 'status'), ('status', 'check the work')])
async def test_realtime_model_paraphrase_cannot_claim_status_fast_path(actual, argument):
    service, call, _ = setup('realtime')
    service.result.set()
    if actual:
        await call.record('user', actual, 'u1')
    await call.realtime_tool({'call_id': 'd1', 'name': 'amplifier_delegate', 'arguments': json.dumps({'text': argument})})
    assert len(service.calls) == 1
    assert call.last_status_version == 0


@pytest.mark.parametrize('change', ['call', 'session', 'transfer', 'disconnected', 'closed'])
async def test_status_scope_failure_never_dispatches_work(change):
    service, call, _ = setup()
    service.portability = SimpleNamespace(write_context=lambda sid: 'new-transfer' if change == 'transfer' else None)
    await call.record('user', 'status', 'u1')
    if change == 'call': call.manager.call = object()
    if change == 'session': service.state['voice']['sessionId'] = 'another'
    if change == 'disconnected': service.state['voice']['status'] = 'error'
    if change == 'closed': call.closed = True
    with pytest.raises(VoiceError):
        await call.execute('status', 'd1', utterance_version=1)
    assert service.calls == []


async def test_progress_is_not_put_in_realtime_context_while_busy_and_speech_discards_it():
    _, call, now = setup('realtime')
    await call.progress_tick()
    call.realtime_playing = True
    now[0] = 5
    await call.progress_tick()
    assert call.pending_progress and call.socket.sent == []
    await call.handle({'type': 'input_audio_buffer.speech_started'})
    assert call.pending_progress is None
    call.realtime_playing = False
    await call.handle({'type': 'response.done'})
    assert call.socket.sent == []


async def test_current_phase_and_transfer_revalidated_before_progress_emission():
    service, call, now = setup('realtime')
    await call.progress_tick(); call.realtime_playing = True; now[0] = 5
    await call.progress_tick()
    service.state['sessions'][0]['status'] = 'stopped'
    call.realtime_playing = False
    await call.flush_progress()
    assert call.socket.sent == [] and call.pending_progress is None
    service.state['sessions'][0]['status'] = 'working'
    await call.progress_tick(); now[0] = 10; call.realtime_playing = True
    await call.progress_tick()
    service.portability = SimpleNamespace(write_context=lambda sid: 'moved')
    call.realtime_playing = False
    await call.flush_progress()
    assert call.socket.sent == []


@pytest.mark.parametrize('provider', ['live', 'realtime'])
async def test_result_precedes_pending_progress_and_exact_generation_is_once(provider):
    service, call, now = setup(provider)
    await call.progress_tick(); call.realtime_playing = True; now[0] = 5
    await call.progress_tick()
    event = {'event': 'generation.finished', 'generation_id': 'g1', 'input_ids': ['typed:1'], 'text': 'Verified result', 'active_job_ids': ['worker']}
    await call.announce_generations({'sessions': [{'id': 'main', 'generations': [event]}]})
    await call.announce_generations({'sessions': [{'id': 'main', 'generations': [event]}]})
    assert call.pending_progress is None
    wire = json.dumps(call.socket.sent)
    assert wire.count('Verified result') == 1 and 'still working' not in wire
    assert 'Background workers are still running' in wire


async def test_actual_service_signal_has_no_browser_snapshot_or_history_hydration(tmp_path):
    from amplifier_web.service import AppService
    class Runtime:
        async def close(self): pass
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    await service.dispatch('session.create', {})
    sid = service.state['selectedSessionId']
    signal = service.subscribe_activity(sid)
    try:
        service.browser_state = lambda: (_ for _ in ()).throw(AssertionError('browser snapshot'))
        row = service._session(sid)
        row['status'] = 'working'
        service._publish_changes(sessions={sid})
        assert signal.is_set() and not service.queues
        signal.clear()
        service._publish_changes(sessions={'unrelated'})
        assert not signal.is_set()
    finally:
        service.unsubscribe_activity(signal)
        await service.close()


@pytest.mark.parametrize('kind', ['progress', 'status'])
async def test_transfer_during_provider_item_prevents_later_speech_request(kind):
    _, call, now = setup('realtime')
    entered, release = asyncio.Event(), asyncio.Event()
    original = call.socket.send_json
    async def held(event):
        await original(event)
        if event['type'] == 'conversation.item.create':
            entered.set()
            await release.wait()
    call.socket.send_json = held
    if kind == 'progress':
        await call.progress_tick(); now[0] = 5
        task = asyncio.create_task(call.progress_tick())
    else:
        await call.record('user', 'status', 'u1')
        task = asyncio.create_task(call.realtime_tool({'call_id': 'd1', 'name': 'amplifier_delegate', 'arguments': '{"text":"status"}'}))
    await asyncio.wait_for(entered.wait(), 1)
    call.manager.call = object()
    release.set(); await task
    assert len(call.socket.sent) == 1  # Already-sent item cannot be recalled.
    assert not any(row['type'] == 'response.create' for row in call.socket.sent)


async def test_deferred_transport_flush_wakes_observer_for_next_elapsed_threshold():
    _, call, now = setup('realtime')
    call.activity_signal = asyncio.Event()
    await call.progress_tick(); call.realtime_playing = True; now[0] = 5
    await call.progress_tick()
    assert call.pending_progress
    call.realtime_playing = False
    await call.flush_progress()
    assert call.activity_signal.is_set()
    assert call.progress.observe(resident_facts(call.resident_session()), 6)[1] == 20


def test_passive_context_caps_strings_before_serialization(monkeypatch):
    from amplifier_web import voice
    real = voice.json.dumps
    def bounded(value, **kwargs):
        assert len(value['recent_messages'][0]['text']) <= 1000
        assert len(value['activity']['label']) <= 200
        assert len(value['workers']) <= 32
        assert len(value['workers'][0]['title']) <= 160
        return real(value, **kwargs)
    monkeypatch.setattr(voice.json, 'dumps', bounded)
    text = 'x' * 1_000_000
    voice.compact_context({'sessions': [{'id': 'main', 'messages': [{'role':'user','text':text}],
        'activity': {'label':text}, 'workers':[{'title':text}] * 100}]}, 'main')


async def test_initial_context_failure_releases_bounded_subscription():
    service, call, _ = setup()
    signals = {}
    service.subscribe_activity = lambda sid: signals.setdefault('one', asyncio.Event())
    service.unsubscribe_activity = lambda signal: signals.clear()
    call.context = lambda state: (_ for _ in ()).throw(ValueError('injected projection error'))
    await call.observe()
    assert signals == {} and call.activity_signal is None
    assert service.statuses[-1]['error'].startswith('Voice context updates disconnected')


async def test_observer_retires_on_transfer_without_new_owner_context():
    service, call, _ = setup()
    signal=asyncio.Event(); removed=[]
    service.subscribe_activity=lambda sid:signal
    service.unsubscribe_activity=lambda value:removed.append(value)
    observer=asyncio.create_task(call.observe()); await asyncio.sleep(0)
    service.portability=SimpleNamespace(write_context=lambda sid:'transferred')
    service.state['sessions'][0]['generations']=[{'event':'generation.finished','generation_id':'new-owner','text':'PRIVATE NEW OWNER','input_ids':['foreign']}]
    signal.set()
    await asyncio.wait_for(observer,1)
    assert call.socket.sent == [] and removed == [signal]


async def test_verified_result_arriving_during_progress_item_wins_response():
    _, call, now = setup('realtime')
    entered, release=asyncio.Event(),asyncio.Event()
    original=call.socket.send_json
    async def held(event):
        await original(event)
        if event['type']=='conversation.item.create' and 'Host progress' in json.dumps(event):
            entered.set();await release.wait()
    call.socket.send_json=held
    await call.progress_tick();now[0]=5
    progress=asyncio.create_task(call.progress_tick());await entered.wait()
    finished=asyncio.create_task(call.announce_generations({'sessions':[{'id':'main','generations':[{
        'event':'generation.finished','generation_id':'g1','input_ids':['typed'],'text':'Verified answer'}]}]}))
    await asyncio.sleep(0)
    assert call.realtime_pending_response
    release.set();await asyncio.gather(progress,finished)
    responses=[row for row in call.socket.sent if row['type']=='response.create']
    assert len(responses)==1 and 'verified Amplifier result' in responses[0]['response']['instructions']


async def test_repeated_status_tool_cannot_create_an_extra_input():
    service, call, _=setup('realtime')
    await call.record('user','status','u1')
    for did in ('one','two'):
        await call.realtime_tool({'call_id':did,'name':'amplifier_delegate','arguments':'{"text":"status"}'})
    assert service.calls==[]
    assert 'not an accepted task or completion result' in json.dumps(call.socket.sent)


async def test_tail_gap_never_replays_history_and_exact_accepted_result_survives():
    service, call, _ = setup()
    session=service.state['sessions'][0]
    session['generations']=[{'event':'generation.finished','generation_id':'first','text':'First','input_ids':['typed:first']}]
    await call.announce_generations(service.state)
    first_count=len(call.socket.sent)
    session['generations']=[{'event':'generation.finished','generation_id':f'gap-{i}','text':'HISTORY MUST NOT REPLAY','input_ids':[f'typed:{i}']} for i in range(201,401)]
    await call.announce_generations(service.state)
    assert len(call.socket.sent)==first_count and not call.generation_coverage_complete
    assert service.statuses[-1]=={'generationCoverage':'incomplete'}
    service.voice_delegate=AsyncMock(return_value={'accepted':True,'inputId':'voice:owned-call:request'})
    service.wait_for_response=AsyncMock(return_value={'generation_id':'exact-after-gap','input_ids':['voice:owned-call:request'],'text':'Exact accepted result'})
    await call.record('user','Do the requested task','u1');await call.delegate_live('request')
    assert 'Exact accepted result' in json.dumps(call.socket.sent)
    assert 'HISTORY MUST NOT REPLAY' not in json.dumps(call.socket.sent)
    for i in range(450):
        call.remember_delivery(str(i), {})
        call.prune_generations()
    assert len(call.seen_generations)<=200 and len(call.delivered_generations)<=400
    assert not call.accepted_inputs


@pytest.mark.parametrize('provider',['live','realtime'])
async def test_scripted_review_runs_actual_policy_and_transport_classes(provider):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parent/'fixtures/voice_status_review.py'
    spec=importlib.util.spec_from_file_location('voice_review_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=await module.review(provider)
    assert len(result['scenarios'])==7 and all(row['passed'] for row in result['scenarios'])
    assert result['scenarios'][3]['acceptedInputs']==0


def test_elapsed_backlog_coalesces_to_latest_observation():
    policy = PacingPolicy()
    policy.observe(ActivityFacts(status='working'), 0)
    notice, _ = policy.observe(ActivityFacts(status='working'), 150)
    assert notice.threshold == 3 and 'two minutes' in notice.text
    policy.delivered(notice, 150)
    assert policy.observe(ActivityFacts(status='working'), 155) == (None, None)


async def test_origin_client_context_is_pinned_before_provider_create():
    from contextvars import ContextVar
    service = Service()
    current = ContextVar('voice-origin', default='origin')
    origin = {'mode':'canvas', 'panel':'session-details', 'scheme':'dark',
              'layout':'wide', 'selectedWorkerId':'origin-worker', 'contextVisible':True}
    service.clients = SimpleNamespace(current=current, records={'origin':{'view':origin},
        'other':{'view':{'mode':'chat', 'panel':'settings'}}})
    socket = Socket()
    manager = VoiceService(service, http=SimpleNamespace(ws_connect=AsyncMock(return_value=socket)))
    manager.request = AsyncMock(return_value=({'session':{'id':'created'},'transport':{'sdp':'answer'}},'',{}))
    call = VoiceCall(manager, 'main')
    assert call.client_id == 'origin'
    current.set('other')
    assert call.observation()['view'] == origin
    actual_context = call.context
    def context(snapshot):
        assert snapshot is not service.state
        assert snapshot['view'] == origin
        assert len(snapshot['sessions'][0]['messages']) <= 4
        return actual_context(snapshot)
    call.context = context
    service.state['sessions'][0]['messages'] = [{'role':'user','text':'large'*10000}]*100
    await call.create('v=0', 'live')
    assert len(manager.request.call_args.kwargs['json']['session']['instructions']) < 10000
    service.clients.records.pop('origin')
    assert set(call.observation()['view'].values()) == {None}
    call.final.set()
    await call.close()


@pytest.mark.parametrize('kind', ['session.closed', 'error'])
async def test_old_provider_terminal_event_cannot_end_replacement_call(kind):
    service, old, _ = setup()
    new = VoiceCall(old.manager, 'main')
    new.id, new.socket = 'replacement', Socket()
    old.manager.call = new
    service.state['voice'] = {'id':new.id, 'sessionId':'main', 'status':'connected'}
    before = list(service.statuses)
    await old.handle({'type':kind, 'error':{'code':'late_failure'}})
    assert service.statuses == before
    assert new.media_scope()
    assert not old.tasks
    if kind == 'session.closed': assert old.final.is_set()


async def test_review_html_is_readable_inert_and_escapes_captured_content():
    from fixtures.voice_status_review import review, review_html
    results={provider:await review(provider) for provider in ('live','realtime')}
    results['live']['scenarios'][0]['steps'][0]['description']='<script>bad()</script>'
    page=review_html(results)
    assert page.count('<article>') == 14
    assert '<script>' not in page and '&lt;script&gt;bad()&lt;/script&gt;' in page
    assert 'Controlled clock' in page and 'accepted work inputs' in page


async def test_playback_spanning_all_thresholds_emits_only_current_elapsed_notice():
    _, call, now = setup('realtime')
    await call.progress_tick()
    call.realtime_playing = True
    now[0] = 5
    await call.progress_tick()
    assert call.pending_progress and not call.socket.sent
    now[0] = 150
    call.realtime_playing = False
    await call.handle({'type':'output_audio_buffer.stopped'})
    wire=json.dumps(call.socket.sent)
    assert wire.count('two minutes') == 1 and 'twenty seconds' not in wire
    call.realtime_responding = False
    now[0] = 155
    assert await call.progress_tick() is None
    assert len([row for row in call.socket.sent if row['type']=='response.create']) == 1
