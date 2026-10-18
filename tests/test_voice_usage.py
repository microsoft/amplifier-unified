import pytest

from amplifier_web.voice_usage import normalize_voice_usage
from amplifier_web.service import AppService
from test_service import Runtime

def test_voice_cached_tokens_not_added_twice_and_missing_price_unknown():
    usage=normalize_voice_usage({'input_tokens':100,'output_tokens':20,'input_token_details':{'cached_tokens':80}})
    assert usage['totalTokens']==120 and usage['cacheReadTokens']==80
    assert usage['costType']=='unavailable' and 'costUsd' not in usage

async def test_voice_completion_updates_same_call_in_original_turn(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    from amplifier_web.execution import ensure_turn
    ensure_turn(session,'first')
    await service.record_voice_usage(session['id'],'call','response','voice-model',{},'running')
    ensure_turn(session,'second')
    for _ in range(2):await service.record_voice_usage(session['id'],'call','response','voice-model',{'input_tokens':100,'output_tokens':20})
    tree=session['execution']
    assert len(tree['nodes'])==1 and tree['nodes'][0]['turnId']=='first'
    assert tree['turns'][0]['aggregateUsage']['totalTokens']==120
    assert tree['turns'][1]['aggregateUsage']['totalTokens']==0
    await service.close()


def test_live_seconds_are_validated_without_invented_token_or_price_totals():
    assert normalize_voice_usage({'seconds': 12.5}) == {'costType': 'unavailable', 'voiceSeconds': 12.5}
    for invalid in [True, -1, float('nan'), float('inf'), '12']:
        assert 'voiceSeconds' not in normalize_voice_usage({'seconds': invalid})


async def test_live_duration_replaces_cumulative_receipt_and_retains_incomplete_usage(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    try:
        for seconds in [12, 15, 12]:
            await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':seconds},'running')
        tree=session['execution']
        assert len(tree['nodes']) == 1
        assert tree['aggregateUsage']['voiceSeconds'] == 15
        assert tree['aggregateUsage']['voicePendingCalls'] == 1
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{},'incomplete')
        assert tree['nodes'][0]['usage']['voiceSeconds'] == 15
        assert tree['nodes'][0]['usage']['usageFinalized'] is False
        assert tree['aggregateUsage']['voiceIncompleteCalls'] == 1
        assert tree['aggregateUsage']['voicePendingCalls'] == 0
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':16},'completed')
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':99},'running')
        await service.record_voice_usage(session['id'],'live','response','backend-model',{'input_tokens':100,'output_tokens':20},'completed')
        assert tree['aggregateUsage']['voiceSeconds'] == 16
        assert tree['aggregateUsage']['voiceIncompleteCalls'] == 0
        assert tree['aggregateUsage']['totalTokens'] == 120
        assert tree['nodes'][0]['usage']['usageFinalized'] is True
        assert tree['nodes'][0]['usage']['costType'] == 'unavailable'
    finally:
        await service.close()


async def test_closed_without_final_duration_preserves_last_observation_as_incomplete(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    try:
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':12},'running')
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{},'completed')
        usage=session['execution']['nodes'][0]['usage']
        assert usage['voiceSeconds']==12 and usage['usageFinalized'] is False
        assert session['execution']['aggregateUsage']['voiceIncompleteCalls']==1
    finally:
        await service.close()


@pytest.mark.parametrize('status', ['idle', 'stopped'])
async def test_manager_settlement_and_exit_leave_live_voice_accounting_owned_by_call(tmp_path, status):
    from amplifier_web.execution import ensure_turn
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    try:
        ensure_turn(session, 'manager-input')
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':12},'running')
        tree=session['execution']
        assert tree['currentTurnId']=='manager-input'
        await service.on_runtime_event('runtime.status',{'sessionId':session['id'],'status':status})
        await service.on_runtime_event('runtime.ended',{'sessionId':session['id'],'reason':'process-exit'})
        assert session['status']==status and not session.get('error')
        manager,voice=tree['turns']
        assert manager['phase']==('completed' if status=='idle' else 'stopped')
        assert voice['phase']=='running' and not voice.get('endedAt')
        assert tree['nodes'][0]['phase']=='running' and not tree['nodes'][0].get('endedAt')
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':15},'running')
        assert tree['aggregateUsage']['voiceSeconds']==15
        assert tree['aggregateUsage']['voicePendingCalls']==1
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{},'incomplete')
        assert voice['phase']=='incomplete'
        assert tree['aggregateUsage']['voiceIncompleteCalls']==1
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':16},'completed')
        assert voice['phase']=='completed'
        assert tree['aggregateUsage']['voiceSeconds']==16
        assert tree['aggregateUsage']['voiceIncompleteCalls']==0
    finally:
        await service.close()


async def test_voice_usage_alone_does_not_become_manager_work_on_process_exit(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    try:
        session['status']='idle'
        await service.record_voice_usage(session['id'],'live','session','gpt-live-1',{'seconds':12},'running')
        assert session['execution']['currentTurnId'] is None
        await service.on_runtime_event('runtime.ended',{'sessionId':session['id'],'reason':'process-exit'})
        assert session['status']=='idle' and not session.get('error')
        assert session['execution']['turns'][0]['phase']=='running'
        assert session['execution']['nodes'][0]['usage']['voiceSeconds']==12
    finally:
        await service.close()


@pytest.mark.parametrize('cold', [False, True])
async def test_restart_and_cold_admission_retire_orphan_voice_without_interrupting_chat(tmp_path, cold):
    from amplifier_web.cold_display import execution_reference
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    sid=session['id']
    session['status']='idle'
    await service.record_voice_usage(sid,'orphan','session','gpt-live-1',{'seconds':12},'running')
    if cold:
        reference=execution_reference(service.db,session.pop('execution'))
        assert reference['pendingVoice'] is True
        session['_coldFields']={**dict.get(session,'_coldFields',{}),'execution':reference}
        service._state['selectedSessionId']=None
    await service.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        if cold:
            assert all(row['id']!=sid for row in restored._state['sessions'])
        current=restored._session(sid)
        tree=current['execution']
        assert current['status']=='idle' and not current.get('error')
        assert tree['nodes'][0]['phase']==tree['turns'][0]['phase']=='incomplete'
        assert tree['nodes'][0]['usage']['voiceSeconds']==12
        assert tree['nodes'][0]['usage']['usageFinalized'] is False
        assert tree['aggregateUsage']['voicePendingCalls']==0
        assert tree['aggregateUsage']['voiceIncompleteCalls']==1
        assert not restored.runtime.sent
        # A genuine late provider receipt can still repair the incomplete usage.
        await restored.record_voice_usage(sid,'orphan','session','gpt-live-1',{'seconds':14},'completed')
        assert tree['nodes'][0]['usage']['voiceSeconds']==14
        assert tree['nodes'][0]['usage']['usageFinalized'] is True
        assert tree['turns'][0]['phase']=='completed'
    finally:
        await restored.close()


def test_orphan_recovery_keeps_settled_cold_execution_unloaded(tmp_path, monkeypatch):
    from amplifier_web.cold_display import ColdRecord
    from amplifier_web.execution import restore_voice_usage
    row=ColdRecord({'id':'settled','status':'idle','_coldFields':{
        'execution':{'pendingVoice':False,'pendingWork':False}}},None)
    monkeypatch.setattr('amplifier_web.cold_display.load',lambda *args:pytest.fail('Settled history was loaded'))
    assert restore_voice_usage(row) is False
    assert not dict.__contains__(row,'execution')


@pytest.mark.parametrize('cold', [False, True])
@pytest.mark.parametrize('owner', ['call', 'manager-running', 'manager-completed'])
async def test_legacy_realtime_restart_only_exempts_exact_call_owned_turn(tmp_path, cold, owner):
    from amplifier_web.cold_display import execution_reference
    from amplifier_web.execution import ensure_turn, ingest
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    session=service._session()
    sid=session['id']
    session['status']='idle'
    turn_id='voice:legacy:manager-input' if owner!='call' else 'voice:legacy'
    ensure_turn(session,turn_id)
    # Persist the pre-lifecycle Realtime shape, including a genuinely separate
    # manager turn whose input also happens to originate from voice.
    ingest(session,{'id':'voice:legacy:resp_123','turnId':turn_id,'sessionId':sid,'rootSessionId':sid,
        'kind':'llm','phase':'running','provider':'OpenAI voice','model':'gpt-realtime',
        'startedAt':1,'usage':{'costType':'unavailable'}})
    if owner=='manager-completed':
        session['execution']['turns'][0].update(phase='completed',endedAt=2)
    if cold:
        reference=execution_reference(service.db,session.pop('execution'))
        reference.pop('pendingVoice')  # Legacy cold metadata has no voice bit.
        if owner=='manager-completed':
            assert reference['pendingWork'] is False and reference['pendingObservation'] is False
        session['_coldFields']={**dict.get(session,'_coldFields',{}),'execution':reference}
        service._state['selectedSessionId']=None
    await service.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        if cold:
            assert all(row['id']!=sid for row in restored._state['sessions'])
        current=restored._session(sid)
        tree=current['execution']
        assert tree['nodes'][0]['phase']=='incomplete'
        assert 'voiceSeconds' not in tree['nodes'][0]['usage']
        if owner=='manager-running':
            assert current['status']=='interrupted'
            assert tree['turns'][0]['phase']=='interrupted'
            assert tree['turns'][0].get('lifecycle')!='voice'
        elif owner=='manager-completed':
            assert current['status']=='idle' and not current.get('error')
            assert tree['turns'][0]['phase']=='completed'
            assert tree['turns'][0].get('lifecycle')!='voice'
        else:
            assert current['status']=='idle' and not current.get('error')
            assert tree['turns'][0]['phase']=='incomplete'
            assert tree['turns'][0]['lifecycle']=='voice'
        assert not restored.runtime.sent
    finally:
        await restored.close()


async def test_incomplete_realtime_receipt_retains_observed_tokens_and_price(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    sid=service._session()['id']
    try:
        await service.record_voice_usage(sid,'call','response','gpt-realtime',{
            'input_tokens':100,'output_tokens':20,'cost_usd':.03},'running')
        await service.record_voice_usage(sid,'call','response','gpt-realtime',{
            'input_tokens':110,'output_tokens':25,'cost_usd':-1},'incomplete')
        tree=service._session(sid)['execution']
        usage=tree['nodes'][0]['usage']
        assert usage['inputTokens']==110 and usage['outputTokens']==25 and usage['totalTokens']==135
        assert usage['costUsd']==.03 and usage['costType']=='reported'
        await service.record_voice_usage(sid,'call','response','gpt-realtime',{},'incomplete')
        assert tree['nodes'][0]['usage']==usage
        assert tree['aggregateUsage']['totalTokens']==135
        assert tree['aggregateUsage']['costUsd']==.03
        assert tree['aggregateUsage']['tokenPendingCalls']==0
        await service.record_voice_usage(sid,'call','response','gpt-realtime',{
            'input_tokens':120,'output_tokens':30,'cost_usd':.04},'completed')
        await service.record_voice_usage(sid,'call','response','gpt-realtime',{},'incomplete')
        assert tree['nodes'][0]['phase']=='completed'
        assert tree['aggregateUsage']['totalTokens']==150
        assert tree['aggregateUsage']['costUsd']==.04
    finally:
        await service.close()
