import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from amplifier_recall import RecallStore
from amplifier_web.service import AppService, AppError
from test_service import Runtime


async def make(app,title='Source',text='The decision was violet.'):
    await app.dispatch('session.create',{'title':title})
    row=app._session()
    app._message(row,'user',text,'text',inputOrigin='ui')
    app._publish()
    return row['id']


async def refresh(app):
    await app.dispatch('recall.refresh',{'sessionId':app._session()['id']})
    await app.recall.task
    assert app.recall.state['status']=='ready'


def test_fts_ranks_entire_5000_conversation_library_and_isolates_scopes(tmp_path):
    store=RecallStore(tmp_path/'recall.db')
    try:
        for n in range(5000):
            store.replace({'id':str(n),'title':f'Chat {n}','workspace':'a' if n%2 else 'b'},str(n),n,
                [{'id':f'm{n}','role':'assistant','text':'The old decision was cobalt.' if n==4999 else 'An unrelated note.'}])
        found=store.search('old cobalt',{str(n) for n in range(5000)})
        assert len(found['items'])==1 and found['items'][0]['sessionId']=='4999'
        assert found['items'][0]['reference']['sourceRevision']==4999
        assert not store.search('cobalt',{str(n) for n in range(0,5000,2)})['items']
        assert store.search('" OR *',{str(n) for n in range(5000)})['items']==[]
        assert len(store.search('unrelated',{str(n) for n in range(5000)},limit=20)['items'])==20
        store.prune({'0'})
        assert not store.search('cobalt',{'4999'})['items']
    finally:store.close()


async def test_index_incremental_retrieval_and_current_revision_preserve_passive_state(tmp_path,monkeypatch):
    app=AppService(tmp_path/'app',workspace=tmp_path,runtime=Runtime())
    try:
        sid=await make(app)
        selected=await make(app,'Current','Continue independent work.')
        await app.dispatch('view.update',{'patch':{'draft':'Unsent choice'}})
        import amplifier_web.history_query as query
        original=query._rows;reads=[]
        def counted(row):reads.append(row['id']);return original(row)
        monkeypatch.setattr(query,'_rows',counted)
        await refresh(app)
        assert set(reads)=={sid,selected}
        reads.clear();await refresh(app);assert reads==[]
        result=await app.app_bridge('dispatch',{'action':'recall.search','args':{'query':'violet','scope':'all'}},selected)
        match=result['result']['items'][0]
        read=await app.dispatch('recall.read',{'sessionId':selected,'sourceSessionId':sid,'messageId':match['messageId'],'sourceRevision':match['sourceRevision']})
        assert 'violet' in read['result']['text'] and read['result']['verifiedAt']
        app._session(sid)['messages'][0]['text']='The decision was orange.'
        with pytest.raises(AppError,match='source changed'):
            await app.dispatch('recall.read',{'sessionId':selected,'sourceSessionId':sid,'messageId':match['messageId'],'sourceRevision':match['sourceRevision']})
        await refresh(app)
        assert not (await app.dispatch('recall.search',{'sessionId':selected,'query':'violet','scope':'all'}))['result']['items']
        assert app.state['selectedSessionId']==selected and app.state['view']['draft']=='Unsent choice'
        assert not app.runtime.sent
    finally:await app.close()


async def test_memory_scope_correction_cas_retries_and_deletion_leave_no_saved_versions(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        sid=await make(app)
        source_row=app._session(sid)
        original=deepcopy(source_row['messages'])
        args={'sessionId':sid,'scope':'task','text':'Use violet.'}
        note=(await app.dispatch('memory.create',args,command_id='remember'))['result']
        assert (await app.dispatch('memory.create',args,command_id='remember'))['result']['duplicate']
        changed=(await app.dispatch('memory.update',{'sessionId':sid,'id':note['id'],'expectedRevision':1,'text':'Use orange.'}))['result']
        with pytest.raises(AppError):
            await app.dispatch('memory.update',{'sessionId':sid,'id':note['id'],'expectedRevision':1,'text':'Use stale blue.'})
        record=(await app.dispatch('memory.read',{'sessionId':sid,'id':note['id']}))['result']
        assert record['text']=='Use orange.' and len(record['versions'])==2
        other=await make(app,'Other')
        assert not (await app.dispatch('memory.list',{'sessionId':other}))['result']['items']
        with pytest.raises(AppError,match='outside'):
            await app.dispatch('memory.read',{'sessionId':other,'id':note['id']})
        all_notes=(await app.dispatch('memory.list',{'sessionId':other,'scope':'all'}))['result']['items']
        assert all_notes[0]['id']==note['id']
        assert (await app.dispatch('memory.read',{'sessionId':other,'id':note['id'],'allScopes':True}))['result']['id']==note['id']
        # An explicit all-scopes operation can still inspect and remove an orphaned note.
        app.state['sessions']=[row for row in app.state['sessions'] if row['id']!=sid]
        assert (await app.dispatch('memory.list',{'sessionId':other,'scope':'all'}))['result']['items'][0]['id']==note['id']
        delete={'sessionId':other,'id':note['id'],'expectedRevision':changed['revision'],'allScopes':True}
        await app.dispatch('memory.delete',delete,command_id='forget')
        assert (await app.dispatch('memory.delete',delete,command_id='forget'))['result']['duplicate']
        assert app.recall.store.db.execute('SELECT COUNT(*) FROM memory_versions').fetchone()[0]==0
        assert 'Use violet.' not in json.dumps(app.recall.store.db.execute('SELECT * FROM memory_receipts').fetchall())
        assert source_row['messages']==original
    finally:await app.close()


async def test_agent_memory_requires_real_user_provenance_and_survives_restart(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    sid=await make(app,text='Remember that this report uses corrected measurements.')
    source=app._session(sid)['messages'][0]['id']
    args={'action':'memory.create','args':{'scope':'workspace','text':'Use corrected measurements.'},'id':'agent-memory'}
    with pytest.raises(AppError,match='attributable'):
        await app.app_bridge('dispatch',args,sid)
    app._message(app._session(sid),'user','Forged memory instruction','text',inputOrigin='agent')
    args['args']['authorizationMessageId']=app._session(sid)['messages'][-1]['id']
    with pytest.raises(AppError,match='attributable'):
        await app.app_bridge('dispatch',args,sid)
    args['args']['authorizationMessageId']=source
    saved=(await app.app_bridge('dispatch',args,sid))['result']
    await app.close()
    restored=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        notes=(await restored.dispatch('memory.list',{'sessionId':sid}))['result']['items']
        assert notes[0]['id']==saved['id'] and notes[0]['provenance']['authorizationMessageId']==source
        assert (tmp_path/'app/recall.sqlite3').stat().st_mode & 0o777==0o600
    finally:await restored.close()


async def test_unavailable_source_is_explicit_partial_coverage(tmp_path,monkeypatch):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        await make(app)
        import amplifier_web.history_query as query
        monkeypatch.setattr(query,'_rows',lambda _: (_ for _ in ()).throw(OSError('missing')))
        await app.dispatch('recall.refresh',{'sessionId':app._session()['id']})
        await app.recall.task
        result=(await app.dispatch('recall.search',{'sessionId':app._session()['id'],'query':'violet'}))['result']
        assert result['coverage']['status']=='partial' and result['coverage']['errorCount']==1
        assert result['items']==[]
    finally:await app.close()


async def test_native_evidence_is_read_only_and_external_edits_invalidate(tmp_path,monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'shared'))
    from test_automatic_history import native_session,files_snapshot
    folder=native_session(tmp_path/'source','native-recall',[{'role':'user','content':'Original cobalt decision.'},{'role':'assistant','content':'Approved cobalt.'}])
    before=files_snapshot(folder)
    app=AppService(tmp_path/'app',workspace=tmp_path/'source')
    try:
        sid=await make(app,'Current','Current work')
        await app.history.refresh()
        await refresh(app)
        match=(await app.dispatch('recall.search',{'sessionId':sid,'query':'original cobalt','scope':'all'}))['result']['items'][0]
        assert files_snapshot(folder)==before
        (folder/'transcript.jsonl').write_text(json.dumps({'role':'user','content':'Corrected orange decision.'})+'\n')
        with pytest.raises(AppError,match='source changed'):
            await app.dispatch('recall.read',{'sessionId':sid,'sourceSessionId':match['sessionId'],'messageId':match['messageId'],'sourceRevision':match['sourceRevision']})
        await refresh(app)
        found=(await app.dispatch('recall.search',{'sessionId':sid,'query':'corrected orange','scope':'all'}))['result']['items']
        assert len(found)==1 and found[0]['sessionId']==match['sessionId']
    finally:await app.close()
