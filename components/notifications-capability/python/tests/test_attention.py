import json
import pytest
from amplifier_unified_notifications.owner import Owner, ATTENTION_PREFIX

def config(tmp_path):return {'stateDirectory':str(tmp_path/'state')}
def fact(char='a'):return {'id':'settings:'+'1'*64,'fingerprint':char*64}
async def call(owner,operation,args,command='review'):
    return await owner.request('action',{'operation':'attention.'+operation,'args':args,'commandId':command})

async def test_review_is_shared_exact_durable_and_never_sends(tmp_path):
    async def forbidden(*args):pytest.fail('Review must not deliver or retry')
    owner=Owner(config(tmp_path),transport=forbidden)
    before=owner.public()
    assert (await owner.request('initialize',{}))['attention']=={'version':1}
    assert (await call(owner,'inspect',{'items':[fact()]}))=={'reviewed':[]}
    receipt=await call(owner,'read',{'requested':[fact()],'current':[fact()]})
    assert receipt['reviewed']==[fact()['id']]
    assert owner.public()==before and not owner.tasks
    await owner.close()
    owner=Owner(config(tmp_path),transport=forbidden)
    try:
        assert (await call(owner,'inspect',{'items':[fact()]}))['reviewed']==[fact()['id']]
        assert (await call(owner,'inspect',{'items':[fact('b')]}))['reviewed']==[]
        # Recover the original acknowledgement, even after the fact changed.
        same=await call(owner,'read',{'requested':[fact()],'current':[]})
        assert same['reviewed']==receipt['reviewed'] and same['replayed'] is False
        stale=await call(owner,'read',{'requested':[fact('b')],'current':[]},'stale')
        assert stale['reviewed']==[]
        with pytest.raises(ValueError,match='identity changed'):
            await call(owner,'read',{'requested':[fact('b')],'current':[fact('b')]})
        assert owner.public()==before
    finally:await owner.close()

async def test_refused_invalid_held_and_reserved_never_acknowledge(tmp_path):
    owner=Owner(config(tmp_path))
    try:
        for args in [{'requested':[fact()],'current':[fact('b')]},{'requested':[fact()]*51,'current':[]},{'requested':[fact(),fact()],'current':[]}]:
            with pytest.raises(ValueError):await call(owner,'read',args)
        with pytest.raises(ValueError,match='Reserved'):
            owner.save({'expectedRevision':0,'patch':{'preview':True}},ATTENTION_PREFIX+'a'*64)
        with pytest.raises(ValueError,match='Reserved'):
            await call(owner,'read',{'requested':[fact()],'current':[fact()]},ATTENTION_PREFIX+'a'*64)
        fence={'fenceId':'one','commandId':'update','purpose':'distribution-update','instanceId':'before','dataScope':'owned'}
        await owner.request('quiescence/acquire',fence)
        assert (await call(owner,'read',{'requested':[fact()],'current':[fact()]}))['executed'] is False
        assert (await call(owner,'inspect',{'items':[fact()]}))['reviewed']==[]
        assert owner.public()['revision']==0
    finally:await owner.close()

async def test_inspection_uses_bounded_key_lookups_not_command_history(tmp_path):
    owner=Owner(config(tmp_path))
    try:
        with owner.db:
            owner.db.executemany('INSERT INTO commands VALUES(?,?,?)',[(str(i),'fixture',json.dumps({'status':'completed'})) for i in range(1000)])
        sql=[];owner.db.set_trace_callback(sql.append)
        assert (await call(owner,'inspect',{'items':[fact()]}))['reviewed']==[]
        assert len(sql)==1 and 'WHERE id=' in sql[0]
    finally:await owner.close()
