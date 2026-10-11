import copy
import json

import pytest

from amplifier_web import execution
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.image_results import completed_receipt
from amplifier_web.runtime import normalize_event
from amplifier_web.service import AppService
from test_generated_images import receipt


async def emit_image(app, row, *, child=False, before_complete=None):
    session=app._session(); sid=session['id']; events=[]
    session['messages']=[{'id':'origin','role':'user','text':'Draw it','inputId':'turn'},
                         {'id':'later','role':'user','text':'A later request'}]
    session['status']='working'; execution.ensure_turn(session,'turn')
    observer=ExecutionEvents(sid,events.append)
    observer.lifecycle({'type':'input.delivered','input_id':'turn'})
    owner=sid
    if child:
        observer.hook(sid,'tool:pre',{'tool_name':'delegate','tool_call_id':'delegate','tool_input':{}})
        observer.lifecycle({'type':'child.updated','sessionId':'child','parentSessionId':sid,
                            'callId':'delegate','status':'running','agent':'image-worker'})
        owner='child'
    observer.hook(owner,'tool:pre',{'tool_name':'image_generate','tool_call_id':row['requestId'],
        'tool_input':{'action':row['operation'],'request_id':row['requestId']}})
    observer.hook(owner,'tool:post',{'tool_call_id':row['requestId'],
        'result':{'success':True,'output':{**row,'receiptPath':row['requestId']+'.json'}}})
    for event in events:
        if event['type']=='image.generated' and before_complete:
            before_complete()
        kind,payload=normalize_event(event,sid)
        await app.on_runtime_event(kind,payload)
    return events[-1]


@pytest.mark.parametrize('child',[False,True])
@pytest.mark.parametrize('accompanying_text',[None,'A blue square on white.'])
async def test_native_result_automatically_publishes_exact_image_and_origin(tmp_path,child,accompanying_text):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        app.state['view']['draft']='Keep my draft'
        before=copy.deepcopy(app.state['canvas'])
        row,image=receipt(tmp_path,'generated')
        if accompanying_text is not None:
            row['text']=accompanying_text
            (tmp_path/'generated.json').write_text(json.dumps(row))
        event=await emit_image(app,row,child=child)
        sid=app._session()['id']
        output=app.outputs.store.list(sid)['items'][0]
        assert app.outputs.content(output)==image
        assert output['imageGeneration'].get('text')==accompanying_text
        canvas=app.state['canvasArtifacts'][0]
        assert canvas['messageId']=='origin' and canvas['imageRequestId']=='generated'
        assert app.state['canvas']==before and app.state['view']['draft']=='Keep my draft'
        # Duplicate completion and an explicit model attachment share one image.
        await app.on_runtime_event(*normalize_event(event,sid))
        explicit=await app.dispatch('outputs.attachImage',{'sessionId':sid,'messageId':'origin',
            'title':'Model title','receiptPath':'generated.json'})
        assert explicit['result']['id']==output['id']
        assert len(app.outputs.store.list(sid)['items'])==len(app.state['canvasArtifacts'])==1
        assert not app._session().get('imageResultErrors')
    finally:
        await app.close()
    restored=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        assert restored.outputs.content(restored.outputs.record(sid,output['id']))==image
        assert restored.state['canvasArtifacts'][0]['messageId']=='origin'
    finally:
        await restored.close()


@pytest.mark.parametrize('corrupt',['artifact','identity','origin'])
async def test_changed_receipt_or_origin_never_publishes_wrong_result(tmp_path,corrupt):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        row,_=receipt(tmp_path,'generated')
        def change():
            if corrupt=='artifact': (tmp_path/'generated.png').write_bytes(b'changed')
            elif corrupt=='identity':
                other={**row,'requestId':'other'}
                (tmp_path/'generated.json').write_text(json.dumps(other))
            else: app._session()['messages']=[{'id':'later','role':'user','text':'New turn'}]
        await emit_image(app,row,before_complete=change)
        assert not app.outputs.store.list(app._session()['id'])['items']
        assert not app.state.get('canvasArtifacts')
        assert app._session()['status']=='working'  # Display errors do not fail a turn.
        if corrupt!='origin': assert app._session()['imageResultErrors']
    finally:
        await app.close()


@pytest.mark.parametrize('saved_parent',[False,True])
async def test_automatic_edit_keeps_original_snapshot_and_lineage(tmp_path,saved_parent):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await app.dispatch('session.create',{})
        original,original_bytes=receipt(tmp_path,'original')
        if saved_parent: await emit_image(app,original)
        edited,image=receipt(tmp_path,'edited',inputs=[{'path':'original.png','sha256':original['artifact']['sha256'],'role':'target'}])
        await emit_image(app,edited)
        outputs=app.outputs.store.list(app._session()['id'])['items']
        edit=next(r for r in outputs if r.get('imageGeneration',{}).get('requestId')=='edited')
        parent=app.outputs.record(app._session()['id'],edit['parentId'])
        assert app.outputs.content(parent)==original_bytes
        assert app.outputs.content(edit)==image
        assert len(outputs)==2 and edit['version']==parent['version']+1
    finally:
        await app.close()


def test_private_delivery_requires_completed_native_receipt(tmp_path):
    row,_=receipt(tmp_path,'generated');row['receiptPath']='generated.json'
    assert completed_receipt({'success':False,'output':row}) is None
    assert completed_receipt({**row,'status':'unknown'}) is None
    assert completed_receipt({**row,'requestHash':'bad'}) is None
    assert completed_receipt({**row,'schema':'other'}) is None
    assert completed_receipt(row)['receiptPath']=='generated.json'
