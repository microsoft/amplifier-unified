"""Synthetic native observation only: never reads this machine's desktop."""
import asyncio
import base64
import copy
import time

import pytest

from amplifier_web.host_identity import local_host_identity
from amplifier_web.service import AppError
from test_voice_visual import visual, PNG, begin, complete


class Native:
    def __init__(self):
        self.calls=[]
        self.entered=asyncio.Event()
        self.release=None
        self.cancelled=False
        self.available=True

    async def run(self, operation):
        self.calls.append(operation)
        if operation=='status': return {'available':self.available,'status':'ready' if self.available else 'permission_required','permission':'granted' if self.available else 'required'}
        self.entered.set()
        if self.release:
            try: await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled=True
                raise
        return {'image':PNG,'capturedAt':time.time(),'window':{'id':'42','title':'Synthetic window','application':'Fixture app','bounds':[10,20,11,21]}}


async def native_grant(app,target):
    with app.clients.bind('browser-one'):
        return await app.voice_visual.grant_source({**target,'source':{'kind':'native-foreground','hostId':local_host_identity()['id'],'hostInstanceId':app.instance_id}})


async def test_native_grant_status_never_captures_and_agent_cannot_grant(visual):
    app,target=visual;native=app.voice_visual.native=Native()
    assert native.calls==[]
    with pytest.raises(AppError,match='browser owning'):
        await native_grant_without_owner(app,target)
    with app.clients.bind('browser-one'):
        status=await app.voice_visual.native_status(**{'sid':target['sessionId'],'call_id':target['callId']})
    assert status['host']==local_host_identity() and status['hostInstanceId']==app.instance_id
    await native_grant(app,target)
    assert native.calls==['status','status'] and app.voice_visual.pending is None
    assert app.voice_visual.status(target['sessionId'],target['callId'])['nativeForeground']

async def native_grant_without_owner(app,target):
    return await app.voice_visual.grant_source({**target,'source':{'kind':'native-foreground'}})


@pytest.mark.parametrize('field,value',[('hostId','other-host'),('hostInstanceId','old-instance')])
async def test_explicit_host_binding_rejects_wrong_device_or_instance(visual,field,value):
    app,target=visual;native=app.voice_visual.native=Native()
    source={'kind':'native-foreground','hostId':local_host_identity()['id'],'hostInstanceId':app.instance_id,field:value}
    with app.clients.bind('browser-one'),pytest.raises((AppError,ValueError)):
        await app.voice_visual.grant_source({**target,'source':source})
    assert native.calls==[] and app.voice_visual.grant is None


async def test_permission_required_no_grant_no_capture(visual):
    app,target=visual;native=app.voice_visual.native=Native();native.available=False
    with pytest.raises(AppError,match='unavailable'): await native_grant(app,target)
    assert app.voice_visual.grant is None and native.calls==['status']


async def test_native_capture_existing_action_once_with_authoritative_metadata(visual):
    app,target=visual;native=app.voice_visual.native=Native()
    await native_grant(app,target)
    app.state['view']['draft']='Keep draft';selected=app.state['selectedSessionId']
    receipt=await app.dispatch('voice.visual.capture',target,command_id='native-one',origin='agent')
    row=receipt['result']
    assert row['nativeForeground'] and row['source']['host']==local_host_identity()
    assert row['observation']['window']['application']=='Fixture app'
    assert row['observation']['captureScope']=='visible foreground window region'
    assert app.voice_visual.read(selected,row['id'])['_image']==PNG
    assert app.clients.records['browser-one'].get('deviceCommands',[])==[]
    assert await app.dispatch('voice.visual.capture',target,command_id='native-one')==receipt
    assert native.calls==['status','capture']
    assert app.state['selectedSessionId']==selected and app.state['view']['draft']=='Keep draft'


async def test_browser_cannot_forge_native_pixels(visual):
    app,target=visual;native=app.voice_visual.native=Native();native.release=asyncio.Event()
    await native_grant(app,target);task=await begin(app,target);await native.entered.wait()
    with pytest.raises(AppError,match='selected source'): await complete(app,target)
    app.voice_visual.revoke()
    with pytest.raises(AppError):await task
    assert native.cancelled and not app.voice_visual.receipts


@pytest.mark.parametrize('ending',['end','revoke','cancel','restart'])
async def test_cancelled_or_stale_native_observation_never_delivers(visual,ending):
    app,target=visual;native=app.voice_visual.native=Native();native.release=asyncio.Event()
    await native_grant(app,target);task=await begin(app,target);await native.entered.wait()
    if ending=='end':await app.set_voice_status({'status':'ending'})
    elif ending=='revoke':app.voice_visual.revoke()
    elif ending=='cancel':task.cancel()
    else:app.instance_id='restarted';native.release.set()
    with pytest.raises((AppError,asyncio.CancelledError)):await task
    assert not app.voice_visual.receipts and not app._session(target['sessionId'])['messages']
    assert app.voice_visual.pending is None
    if ending!='restart':assert native.cancelled


async def test_call_ends_while_native_status_is_pending_cannot_grant(visual):
    app,target=visual;entered=asyncio.Event();release=asyncio.Event()
    class Delayed(Native):
        async def run(self,op):entered.set();await release.wait();return {'available':True,'status':'ready'}
    app.voice_visual.native=Delayed()
    task=asyncio.create_task(native_grant(app,target));await entered.wait()
    await app.set_voice_status({'status':'ending'});release.set()
    with pytest.raises(AppError,match='connected voice call'):await task
    assert app.voice_visual.grant is None
