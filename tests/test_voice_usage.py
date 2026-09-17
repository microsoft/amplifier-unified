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
