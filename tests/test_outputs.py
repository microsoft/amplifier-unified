import copy
import hashlib
import subprocess

import pytest

from amplifier_web.service import AppService, AppError
from test_service import Runtime


@pytest.fixture
async def app(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    service=AppService(tmp_path/'app',workspace=workspace,runtime=Runtime())
    await service.dispatch('session.create',{'title':'Output source'})
    service._message(service._session(),'assistant','A report and supporting dataset.','text')
    await service.dispatch('view.update',{'patch':{'draft':'Unsent draft'}})
    yield service
    await service.close()


async def test_exact_file_versions_lineage_receipts_unlink_and_resource_retention(app):
    sid=app._session()['id'];path=app._session()['workspace']
    from pathlib import Path
    file=Path(path)/'report.txt';file.write_text('Original report')
    original=copy.deepcopy(app._session()['messages'])
    args={'sessionId':sid,'kind':'file','title':'Report','path':'report.txt','messageId':original[0]['id']}
    first=(await app.dispatch('outputs.attach',args,command_id='attach-one'))['result']
    file.write_text('Corrected report')
    duplicate=(await app.dispatch('outputs.attach',args,command_id='attach-one'))['result']
    assert duplicate['id']==first['id'] and duplicate['duplicate']
    second=(await app.dispatch('outputs.attach',{**args,'parentId':first['id'],'expectedSha256':hashlib.sha256(b'Corrected report').hexdigest()}))['result']
    assert second['version']==2 and second['parentId']==first['id']
    await app.dispatch('outputs.unlink',{'sessionId':sid,'id':first['id'],'expectedRevision':1})
    assert len((await app.dispatch('outputs.list',{'sessionId':sid}))['result']['items'])==1
    from amplifier_web.resource_files import collect
    assert first['body']['$resource'] not in collect(app.db,app.state)
    read=(await app.dispatch('outputs.read',{'sessionId':sid,'id':first['id']}))['result']
    assert read['text']=='Original report' and not read['linked']
    assert file.read_text()=='Corrected report' and app._session()['messages']==original
    assert app.state['view']['draft']=='Unsent draft' and app.state['selectedSessionId']==sid and not app.runtime.sent
    with pytest.raises(AppError,match='changed'):
        await app.dispatch('outputs.relink',{'sessionId':sid,'id':first['id'],'expectedRevision':1})


async def test_scope_and_file_boundaries_are_shared_with_agent(app,tmp_path):
    sid=app._session()['id'];outside=tmp_path/'private.txt';outside.write_text('private')
    from pathlib import Path
    (Path(app._session()['workspace'])/'escape').symlink_to(outside)
    for path in [str(outside),'../private.txt','escape']:
        with pytest.raises(AppError,match='inside'):
            await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'file','title':'Escape','path':path})
    writing=(await app.app_bridge('dispatch',{'action':'outputs.write','args':{'title':'Memo','content':'Reusable memo','variant':'document'}},sid))['result']
    await app.dispatch('session.create',{'title':'Other'})
    other=app._session()['id']
    with pytest.raises(AppError,match='calling'):
        await app.app_bridge('dispatch',{'action':'outputs.read','args':{'sessionId':sid,'id':writing['id']}},other)
    with pytest.raises(AppError,match='another'):
        await app.dispatch('outputs.read',{'sessionId':other,'id':writing['id']})
    with pytest.raises(AppError,match='another|originating'):
        await app.dispatch('outputs.write',{'sessionId':other,'title':'Bad lineage','content':'Memo','variant':'standard','parentId':writing['id']})


async def test_external_references_truthful_versions_and_local_comments(app):
    sid=app._session()['id']
    for url in ['javascript:alert(1)','https://user:secret@example.test/x','file:///tmp/a']:
        with pytest.raises(AppError):
            await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'pull_request','title':'PR','url':url})
    row=(await app.dispatch('outputs.attach',{'sessionId':sid,'kind':'pull_request','title':'PR','url':'https://example.test/pull/7'}))['result']
    assert row['versionEvidence']=='unversioned_reference' and 'body' not in row
    result=(await app.dispatch('outputs.comment',{'sessionId':sid,'id':row['id'],'body':'Review the failure branch.'},command_id='comment-one'))['result']
    assert result['externalPosted'] is False
    assert (await app.dispatch('outputs.comment',{'sessionId':sid,'id':row['id'],'body':'Review the failure branch.'},command_id='comment-one'))['result']['duplicate']
    assert len((await app.dispatch('outputs.read',{'sessionId':sid,'id':row['id']}))['result']['comments'])==1


async def test_actual_git_diff_exact_lines_and_no_source_mutations(app):
    from pathlib import Path
    root=Path(app._session()['workspace']);sid=app._session()['id']
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.test')
    file=root/'design.txt';file.write_text('before\n');git('add','design.txt');git('commit','-qm','Initial')
    head=git('rev-parse','HEAD');file.write_text('after\n');(root/'untracked.txt').write_text('Not in review')
    record=(await app.dispatch('outputs.review',{'sessionId':sid,'mode':'unstaged'}))['result']
    read=(await app.dispatch('outputs.read',{'sessionId':sid,'id':record['id']}))['result']
    assert '-before' in read['text'] and '+after' in read['text'] and 'untracked' not in read['text']
    comment={'sessionId':sid,'id':record['id'],'body':'Use a clearer name.','path':'design.txt','line':1,'side':'right'}
    assert (await app.dispatch('outputs.comment',comment))['result']['sha256']==record['sha256']
    with pytest.raises(AppError,match='exact saved diff'):
        await app.dispatch('outputs.comment',{**comment,'line':999})
    with pytest.raises(AppError):await app.dispatch('outputs.review',{'sessionId':sid,'mode':'branch','base':'--help'})
    assert file.read_text()=='after\n' and git('rev-parse','HEAD')==head
    file.write_text('later\n')
    assert '+after' in (await app.dispatch('outputs.read',{'sessionId':sid,'id':record['id']}))['result']['text']


async def test_writing_versions_survive_restart_and_corrupt_content_fails(tmp_path):
    app=AppService(tmp_path/'app',workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app._session()['id']
    args={'sessionId':sid,'title':'Email','content':'First draft','variant':'email','subject':'Review'}
    first=(await app.dispatch('outputs.write',args))['result']
    second=(await app.dispatch('outputs.write',{**args,'content':'Revised draft','parentId':first['id'],'evidenceIds':[first['id']]}))['result']
    await app.close()
    restored=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        assert restored.outputs.content(first)==b'First draft' and restored.outputs.content(second)==b'Revised draft'
        path=tmp_path/'app/artifacts'/(first['body']['$resource']+'.json')
        path.write_text('{"encoding":"utf-8","data":"changed"}')
        with pytest.raises(AppError,match='hash'):
            await restored.dispatch('outputs.read',{'sessionId':sid,'id':first['id']})
    finally:await restored.close()


async def test_download_requires_auth_and_serves_exact_inert_bytes(authenticated_client,tmp_path):
    from amplifier_web.server import create_app
    app=await create_app(tmp_path/'app',workspace=tmp_path,runtime=Runtime(),voice=False,preload_providers=False,background_updates=False)
    service=app['service'];await service.dispatch('session.create',{})
    record=(await service.dispatch('outputs.write',{'sessionId':service._session()['id'],'title':'Inert','content':'<script>alert(1)</script>','variant':'document'}))['result']
    client=await authenticated_client(app)
    response=await client.get('/api/outputs/'+record['id']+'/content')
    assert response.status==200 and await response.read()==b'<script>alert(1)</script>'
    assert response.headers['Content-Disposition'].startswith('attachment;') and "sandbox" in response.headers['Content-Security-Policy']
    client.session.cookie_jar.clear()
    response=await client.get('/api/outputs/'+record['id']+'/content',headers={'Authorization':''},allow_redirects=False)
    assert response.status in {401,403}


async def test_fork_carries_only_outputs_anchored_to_retained_messages(app):
    sid=app._session()['id'];message=app._session()['messages'][0]['id']
    args={'sessionId':sid,'title':'Anchored','content':'Saved content','variant':'document'}
    anchored=(await app.dispatch('outputs.write',{**args,'messageId':message}))['result']
    await app.dispatch('outputs.write',{**args,'title':'Unanchored'})
    original_count=len(app.outputs.store.list(sid)['items'])
    target={'id':'forked-session','messages':[{'id':message}]}
    app.outputs.fork(sid,target)
    values=app.outputs.store.list(target['id'])['items']
    assert len(values)==1 and values[0]['forkSource']['outputId']==anchored['id']
    assert values[0]['body']==anchored['body'] and len(app.outputs.store.list(sid)['items'])==original_count
