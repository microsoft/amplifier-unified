"""Saved pixels must cross the real typed boundary, not a textual receipt."""
import base64
import copy
import json
import struct
import zlib
from types import SimpleNamespace

import pytest
from amplifier_core.message_models import ChatRequest, Message, ToolSpec
from amplifier_web.service import AppService, AppError
from amplifier_web.output_image_delivery import OutputImageDelivery
from amplifier_web.surface_delivery import SurfaceDelivery, SurfaceProvider
from amplifier_web.voice_visual_delivery import VoiceVisualDelivery


def png(width=3,height=2):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\0\0'*width)*height))+chunk(b'IEND',b'')


@pytest.fixture
async def saved(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app._session()['id'];image=png();(tmp_path/'page.png').write_bytes(image)
    row=(await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'file','title':'Rendered page','path':'page.png'}))['result']
    yield app,sid,row,image,tmp_path
    await app.close()


async def test_shared_png_read_is_exact_passive_bounded_and_owned(saved):
    app,sid,row,image,path=saved
    app.state['view']['draft']='Unsent';selected=app.state['selectedSessionId']
    (path/'page.png').write_bytes(b'later changed source')
    result=await app.dispatch('outputs.image',{'sessionId':sid,'id':row['id'],'sha256':row['sha256']})
    assert result['result']['width']==3 and '_image' not in json.dumps(result)
    private=await app.app_bridge('outputs.image.read',{'id':row['id'],'sha256':row['sha256']},sid)
    assert base64.b64decode(private['_image'])==image
    assert app.state['view']['draft']=='Unsent' and app.state['selectedSessionId']==selected
    with pytest.raises(AppError,match='exact saved'):
        await app.dispatch('outputs.image',{'sessionId':sid,'id':row['id'],'sha256':'0'*64})
    await app.dispatch('session.create',{});other=app._session()['id']
    with pytest.raises(ValueError,match='another conversation'):
        await app.app_bridge('outputs.image.read',{'id':row['id'],'sha256':row['sha256']},other)
    (path/'bad.png').write_text('Permission denied, not pixels')
    bad=(await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'file','title':'Bad','path':'bad.png'}))['result']
    with pytest.raises(AppError,match='PNG'):
        await app.dispatch('outputs.image',{'sessionId':sid,'id':bad['id'],'sha256':bad['sha256']})
    (path/'wide.png').write_bytes(png(4097,1))
    wide=(await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'file','title':'Wide','path':'wide.png'}))['result']
    with pytest.raises(AppError,match='PNG'):
        await app.dispatch('outputs.image',{'sessionId':sid,'id':wide['id'],'sha256':wide['sha256']})


async def test_pixels_require_exact_receipt_vision_and_current_input(saved):
    app,sid,row,image,path=saved
    epoch=['input-one']
    async def bridge(operation,args):
        if operation=='context.manifest':return {'surfaces':[],'inputIds':epoch}
        return await app.app_bridge(operation,args,sid)
    receipt=await bridge('dispatch',{'action':'outputs.image','args':{'id':row['id'],'sha256':row['sha256']}})
    delivery=OutputImageDelivery(VoiceVisualDelivery(SurfaceDelivery(bridge),bridge),bridge)
    delivery.remember_output(receipt)
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps({'success':True,'output':receipt}))],tools=[ToolSpec(name='app_control',parameters={})])
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(capabilities=['vision']))
    result=await delivery.prepare(request,provider)
    assert result.messages[-1].content[-1].type=='image'
    assert base64.b64decode(result.messages[-1].content[-1].source['data'])==image
    assert len(request.messages)==1
    nested=request.model_copy(update={'messages':[Message(role='tool',name='tool_exec',tool_call_id='outer',content=json.dumps({'success':True,'output':receipt}))]})
    assert len((await delivery.prepare(nested,provider)).messages)==1
    without=await delivery.prepare(request.model_copy(update={'messages':[]}),provider)
    assert not without.messages
    fake=copy.deepcopy(receipt);fake['result']['sha256']='0'*64
    forged=request.model_copy(update={'messages':[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(fake))]})
    assert len((await delivery.prepare(forged,provider)).messages)==1
    no_vision=await delivery.prepare(request,SimpleNamespace(get_info=lambda:SimpleNamespace(capabilities=[])))
    assert all(block.type!='image' for block in no_vision.messages[-1].content)
    epoch[:]=['input-two']
    assert len((await delivery.prepare(request,provider)).messages)==1


async def test_cached_image_is_checked_again_before_transport(saved):
    app,sid,row,image,path=saved;unavailable=False
    async def bridge(operation,args):
        if operation=='outputs.image.read' and unavailable:raise ValueError('Resource unavailable')
        if operation=='context.manifest':return {'surfaces':[],'inputIds':['one']}
        return await app.app_bridge(operation,args,sid)
    receipt=await bridge('dispatch',{'action':'outputs.image','args':{'id':row['id'],'sha256':row['sha256']}})
    delivery=OutputImageDelivery(VoiceVisualDelivery(SurfaceDelivery(bridge),bridge),bridge);delivery.remember_output(receipt)
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(receipt))],tools=[ToolSpec(name='app_control',parameters={})])
    class Provider:
        def get_info(self):return SimpleNamespace(capabilities=['vision'])
        async def request_budget(self,request):return request
        async def complete(self,request):return request
    provider=SurfaceProvider(Provider(),delivery)
    budget=await provider.request_budget(request)
    assert budget.messages[-1].content[-1].type=='image'
    unavailable=True
    final=await provider.complete(request)
    assert 'no pixels' in final.messages[-1].content.lower()


async def test_exact_model_catalog_advertises_vision_when_provider_metadata_does_not():
    from amplifier_web.image_capabilities import ImageCapabilities
    catalog=ImageCapabilities()
    calls=[]
    class Provider:
        def get_info(self):return SimpleNamespace(capabilities=['tools'],defaults={'model':'visual'})
        async def list_models(self):
            calls.append(True)
            return [SimpleNamespace(id='visual',capabilities=['vision']),SimpleNamespace(id='text',capabilities=[])]
    provider=Provider();request=ChatRequest(messages=[])
    assert await catalog.supports(request,provider)
    assert await catalog.supports(request,provider)
    assert len(calls)==1
    assert not await catalog.supports(request.model_copy(update={'model':'text'}),provider)
    assert not await catalog.supports(request.model_copy(update={'model':'missing'}),provider)
    provider.selection={'model':'text'}
    assert not await catalog.supports(request.model_copy(update={'model':'visual'}),provider)


@pytest.mark.parametrize('serialization', ['direct', 'model_dump', 'observed_loop_envelope'])
async def test_selected_provider_keeps_typed_images_and_selection_on_every_boundary(saved,serialization):
    from amplifier_core import ProviderInfo
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.session import SelectedProvider
    app,sid,row,image,path=saved
    capabilities={};tools={}
    async def mount(kind,tool,name):tools[name]=tool
    coordinator=SimpleNamespace(get_capability=capabilities.get,register_capability=capabilities.__setitem__,mount=mount,
        hooks=SimpleNamespace(register=lambda *args,**kwargs:None))
    async def bridge(operation,args):
        if operation=='context.manifest':return {'surfaces':[],'inputIds':['one']}
        return await app.app_bridge(operation,args,sid)
    await install_app_access(coordinator,bridge)
    tool_result=await tools['app_control'].execute({'operation':'dispatch','args':{'action':'outputs.image','args':{'id':row['id'],'sha256':row['sha256']}}})
    # Cover Core's direct serialization, its full envelope, and the output/error
    # envelope actually retained by the natural Work run's hook-processed loop.
    content=(tool_result.get_serialized_output() if serialization=='direct' else
             json.dumps(tool_result.model_dump(exclude={'success'} if serialization=='observed_loop_envelope' else set())))
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=content)],tools=[ToolSpec(name='app_control',parameters={})])
    calls=[]
    class Provider:
        def get_info(self):return ProviderInfo(id='test',display_name='Test',credential_env_vars=[],capabilities=['vision'],defaults={'model':'base'})
        async def list_models(self):return [SimpleNamespace(id='selected',capabilities=['vision'])]
        async def complete(self,request,**kwargs):calls.append(('complete',request,kwargs));return request
        async def request_budget(self,request,**kwargs):calls.append(('budget',request,kwargs));return request
        async def stream(self,request,**kwargs):
            try: calls.append(('stream',request,kwargs));yield request
            finally:calls.append(('closed',None,None))
    original=Provider();transform=capabilities['web.provider_transform']
    selected=SelectedProvider(original,{'model':'selected','effort':'high','max_output_tokens':1234},transform)
    assert selected.original is original and selected.get_info().defaults['model']=='selected'
    await selected.request_budget(request)
    await selected.complete(request)
    stream=selected.stream(request);await anext(stream);await stream.aclose()
    # A fresh UI selection gets the same adapter while the live loop stays open.
    replacement=SelectedProvider(original,{'model':'selected','effort':'high','max_output_tokens':1234},transform)
    await replacement.complete(request)
    assert [name for name,_,_ in calls]==['budget','complete','stream','closed','complete']
    for name,sent,kwargs in calls:
        if name=='closed':continue
        assert sent.model=='selected' and sent.reasoning_effort=='high' and sent.max_output_tokens==1234
        assert kwargs['model']=='selected'
        assert sent.messages[-1].content[-1].type=='image'
        assert base64.b64decode(sent.messages[-1].content[-1].source['data'])==image


async def test_failed_serialized_tool_receipt_never_delivers_saved_pixels(saved):
    from amplifier_core import ToolResult
    app,sid,row,image,path=saved
    async def bridge(operation,args):
        if operation=='context.manifest':return {'surfaces':[],'inputIds':['one']}
        return await app.app_bridge(operation,args,sid)
    receipt=await bridge('dispatch',{'action':'outputs.image','args':{'id':row['id'],'sha256':row['sha256']}})
    delivery=OutputImageDelivery(VoiceVisualDelivery(SurfaceDelivery(bridge),bridge),bridge)
    delivery.remember_output(receipt)
    failed=ToolResult(success=False,output=receipt,error={'message':'denied'})
    request=ChatRequest(messages=[Message(role='tool',name='app_control',tool_call_id='one',content=json.dumps(failed.model_dump(exclude={'success'})))],tools=[ToolSpec(name='app_control',parameters={})])
    provider=SimpleNamespace(get_info=lambda:SimpleNamespace(capabilities=['vision']))
    assert len((await delivery.prepare(request,provider)).messages)==1
