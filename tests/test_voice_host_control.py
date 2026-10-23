"""Host ownership and manager-outcome regressions for the browser voice surface."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.service import AppService, AppError
from test_service import Runtime


async def test_voice_waiter_ignores_child_completion_and_failure(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    sid=service._session()['id']
    try:
        waiter=asyncio.create_task(service.wait_for_response(sid,'request',timeout=2))
        for event in ['generation.finished','generation.failed']:
            await service.on_runtime_event('runtime.generation',{'sessionId':'child','rootSessionId':sid,'event':event,
                'generation_id':'child-'+event,'input_ids':['request'],'text':'Private worker result'})
        await asyncio.sleep(0)
        assert not waiter.done()
        await service.on_runtime_event('runtime.generation',{'sessionId':sid,'rootSessionId':sid,'event':'generation.finished',
            'generation_id':'manager','input_ids':['request'],'text':'Manager verified answer','active_job_ids':['other-worker'],
            'disposition':'manager_turn_finished'})
        result=await waiter
        assert result['text']=='Manager verified answer'
        assert result['active_job_ids']==['other-worker']
    finally:
        await service.close()


async def test_second_browser_start_and_stale_controls_preserve_active_call(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    call=SimpleNamespace(id='active',attempt_id='attempt-a',closed=False)
    manager=SimpleNamespace(call=call,api_key='synthetic',end=AsyncMock(),close=AsyncMock())
    service.voice_service=manager
    service.state['voice'].update(status='connected',id='active',muted=False)
    try:
        with pytest.raises(AppError,match='already active'):
            await service.dispatch('call.start',{'attemptId':'attempt-b'})
        assert service.state['voice']['status']=='connected'
        for action,args in [('call.end',{}),('call.mute',{'muted':True})]:
            with pytest.raises(AppError,match='no longer active'):
                await service.dispatch(action,{**args,'callId':'old','attemptId':'attempt-old'})
        assert service.state['voice']['muted'] is False
        manager.end.assert_not_called()
        receipt=await service.dispatch('call.mute',{'callId':'active','attemptId':'attempt-a','controlId':'mute-a','muted':True})
        effect=next(e for e in receipt['effects'] if e['type']=='call.mute')
        assert effect['callId']=='active' and effect['controlId']=='mute-a'
        assert service.state['voice']['muted'] is True
        manager.call=SimpleNamespace(id='replacement',attempt_id='attempt-c',closed=False)
        await service._end_call(call)
        manager.end.assert_not_called()
        assert not service.runtime.stopped
    finally:
        await service.close()


async def test_microphone_setup_does_not_claim_shared_call_state(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    service.voice_service=SimpleNamespace(call=None,api_key='synthetic',close=AsyncMock())
    previous=dict(service.state['voice'])
    try:
        receipt=await service.dispatch('call.start',{'attemptId':'pending'})
        assert service.state['voice']['status']==previous['status']
        assert receipt['effects'][0]['attemptId']=='pending'
    finally:
        await service.close()


async def test_pending_setup_controls_do_not_target_previous_closed_call(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    manager=SimpleNamespace(call=SimpleNamespace(id='old',attempt_id='old-attempt',closed=True),api_key='synthetic',end=AsyncMock(),close=AsyncMock())
    service.voice_service=manager
    try:
        for action,args in [('call.mute',{'muted':True}),('call.end',{})]:
            receipt=await service.dispatch(action,{**args,'attemptId':'new-local-setup','controlId':action})
            assert receipt['effects'][0]['attemptId']=='new-local-setup'
        await asyncio.gather(*service.tasks)
        manager.end.assert_not_called()
        assert not service.runtime.stopped
    finally:
        await service.close()


async def test_retired_call_status_cannot_overwrite_replacement(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.voice_service=SimpleNamespace(call=SimpleNamespace(id='new',attempt_id='new-attempt',session_id=None,closed=False,closing=False),close=AsyncMock())
    service.state['voice'].update(id='new',attemptId='new-attempt',status='connected')
    try:
        await service.set_voice_status({'id':'old','attemptId':'old-attempt','status':'error','error':'Late close failed'})
        assert service.state['voice']['id']=='new' and service.state['voice']['status']=='connected'
        await service.set_voice_status({'id':'new','attemptId':'new-attempt','status':'ended'})
        assert service.state['voice']['status']=='ended'
    finally:
        await service.close()
