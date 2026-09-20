import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web.bundle_selection import BundleTransaction, defaults, switch
from amplifier_web.host.storage import SessionStore
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppService, AppError
from amplifier_web.management import Management
from amplifier_web.session_store import fork_session


async def test_scoped_defaults_persist_and_do_not_retarget_existing_chats(tmp_path):
    home=tmp_path/'app'; workspace=tmp_path/'work'; workspace.mkdir()
    settings=SettingsStore(home)
    settings.update(workspace,'global',lambda s:s.update(bundle={'active':'anchors'},unrelated={'keep':True}))
    app=AppService(home,workspace=workspace)
    try:
        await app.dispatch('session.create',{})
        original=app._session()['id']
        await app.app_bridge('dispatch',{'action':'bundle.default','args':{'scope':'app','bundle':'work'}},original)
        assert app._new_session({})['bundle']=='work'
        assert settings.read(workspace)['bundle']['active']=='anchors'
        assert app._session(original)['bundle']=='anchors'
        await app.dispatch('bundle.default',{'scope':'workspace','bundle':'anchors-amp-dev'})
        assert app._new_session({})['bundle']=='anchors-amp-dev'
        assert defaults(home,workspace,'work')['source']=='workspace'
        await app.dispatch('bundle.default',{'scope':'workspace','bundle':None})
        assert app._new_session({})['bundle']=='work'
        await app.dispatch('bundle.default',{'scope':'shared','bundle':'anchors-work'})
        assert app._new_session({})['bundle']=='work'
        assert settings.read(workspace)['unrelated']=={'keep':True}
    finally: await app.close()
    app=AppService(home,workspace=workspace)
    try:
        assert app._new_session({})['bundle']=='work'
        await app.dispatch('bundle.default',{'scope':'app','bundle':None})
        assert app._new_session({})['bundle']=='anchors-work'
    finally: await app.close()


def seed(home,workspace):
    store=SessionStore.for_app(home,workspace)
    messages=[{'role':'system','content':'Old instructions'}, {'role':'user','content':'Keep this history'},
              {'role':'assistant','content':'Done','tool_calls':[{'id':'call','name':'bash','arguments':{}}]},
              {'role':'tool','tool_call_id':'call','name':'bash','content':'actual result'}]
    store.save('source',messages,{'bundle_name':'anchors'},preserve_system=True)
    local=home/'sessions/source';local.mkdir(parents=True)
    (local/'configuration.json').write_text('{"tools":[{"module":"old-tool"}]}')
    (local/'control-state.json').write_text(json.dumps({'selection':{'instance':'test','model':'chosen','effort':'high'},
                                                      'goal':None,'mode':'old','budget':{'contextTokens':2}}))
    return store,messages


def test_transition_recovers_all_original_files_after_interruption(tmp_path):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    transaction=BundleTransaction(home,tmp_path,'source')
    before={key:path.read_bytes() if path.exists() else None for key,path in transaction.paths.items()}
    transaction.begin();transaction.select('work')
    assert store.load('source')[1]['bundle_name']=='work'
    assert store.load('source')[0]==messages[1:]
    assert not (transaction.local/'configuration.json').exists()
    assert json.loads((transaction.local/'control-state.json').read_text())=={'selection':{'instance':'test','model':'chosen','effort':'high'},'goal':None}
    assert BundleTransaction(home,tmp_path,'source').restore()
    assert {key:path.read_bytes() if path.exists() else None for key,path in transaction.paths.items()}==before
    assert not transaction.path.exists()


def test_fork_with_bundle_keeps_tools_voice_and_original_but_replaces_instructions(tmp_path):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    before=(store.directory('source')/'transcript.jsonl').read_bytes()
    source={'id':'source','status':'idle','workspace':str(tmp_path),'bundle':'anchors',
            'messages':[{'role':'user','text':'Keep this history','nativeIndex':1},
                        {'role':'assistant','text':'Spoken response','voiceId':'voice','via':'call'}]}
    result=fork_session(home,source,'fork',bundle='work')
    saved,meta=store.load('fork')
    assert saved[:3]==messages[1:]
    assert 'Spoken response' in saved[-1]['content']
    assert meta['bundle_name']=='work' and not meta['preserve_system']
    assert result['messages'][0]['nativeIndex']==0
    assert not (home/'sessions/fork/configuration.json').exists()
    assert (store.directory('source')/'transcript.jsonl').read_bytes()==before


@pytest.mark.parametrize('fail',[False,True])
async def test_worker_switch_commits_only_after_mount_and_rolls_back_failure(tmp_path,monkeypatch,fail):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    result={'bundle':'work','fingerprint':'same','selection':None,'modelCompatible':True}
    async def inspect(*args): return result.copy()
    monkeypatch.setattr('amplifier_web.bundle_selection.preview',inspect)
    class Controls:
        def require_idle(self): pass
        async def checkpoint(self): pass
        async def close(self): pass
        def configuration(self): return {'plan':{}}
        async def perform(self,*args): return {'providers':[]}
    class Session:
        async def cleanup(self): pass
    class Worker:
        def __init__(self):
            self.home=home;self.workspace=tmp_path;self.runtime=SimpleNamespace(session_id='source')
            self.start_config={'id':'source','bundle':'anchors'}
            self.bundle_preview={**result,'previewId':'reviewed'}
            self.controls=Controls();self.session=Session();self.execution=None;self.remounting=False
            self.started=[]
        async def start(self,config,**kw):
            self.started.append(config['bundle'])
            if fail and config['bundle']=='work': raise ValueError('module did not mount')
            self.controls=Controls();self.session=Session()
    worker=Worker()
    if fail:
        with pytest.raises(ValueError,match='did not mount'): await switch(worker,{'bundle':'work','previewId':'reviewed'})
        assert store.load('source')[0]==messages
        assert store.load('source')[1]['bundle_name']=='anchors'
        assert worker.started==['work','anchors']
    else:
        assert (await switch(worker,{'bundle':'work','previewId':'reviewed'}))['historyPreserved']
        assert store.load('source')[0]==messages[1:]
        assert store.load('source')[1]['bundle_name']=='work'
    assert not worker.remounting
    assert not (home/'sessions/source/bundle-transition.json').exists()


class Runtime:
    def __init__(self): self.calls=[];self.preview=None;self.fail=False
    async def start(self,session,emit): await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
    async def stop(self,*args): pass
    async def close(self): pass
    async def control(self,sid,operation,args):
        self.calls.append((sid,operation,args))
        if operation=='bundle.preview':
            self.preview={'previewId':'token','bundle':args['bundle'],'fingerprint':'same','selection':None,'modelCompatible':True,'changes':{}}
            return self.preview
        if operation=='bundle.switch':
            if self.fail: raise ValueError('Load failed; original restored')
            return {'bundle':args['bundle'],'configuration':{'plan':{}},'providers':{'providers':[]}}
        if operation=='history.snapshot': return {'messages':[{'role':'user','content':'keep'}]}
        if operation=='configuration.inspect':return {'plan':{}}
        return {'providers':[]}

async def settled(app):
    for _ in range(200):
        if not app.tasks: return
        await asyncio.sleep(.005)
    raise AssertionError('operation did not settle')

async def test_agent_preview_switch_failure_and_duplicate_actions(tmp_path):
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{'bundle':'anchors'})
        sid=app._session()['id'];app._session()['messages']=[{'role':'user','text':'keep'}]
        await app.app_bridge('dispatch',{'action':'bundle.preview','args':{'sessionId':sid,'bundle':'work'}},sid)
        await settled(app)
        assert app._session()['bundlePreview']['bundle']=='work'
        runtime.fail=True
        args={'sessionId':sid,'bundle':'work','previewId':'token'}
        await app.app_bridge('dispatch',{'action':'bundle.switch','args':args},sid)
        await settled(app)
        assert app._session()['bundle']=='anchors'
        assert app._session()['bundleChange']['phase']=='error'
        assert not app._session()['configurationBusy']
        runtime.fail=False
        await app.dispatch('bundle.switch',args,command_id='switch-once');await settled(app)
        await app.dispatch('bundle.switch',args,command_id='switch-once');await settled(app)
        assert app._session()['bundle']=='work'
        assert len([row for row in runtime.calls if row[1]=='bundle.switch'])==2
        assert app._session()['messages']==[{'role':'user','text':'keep'}]
        app._session()['status']='working'
        await app.dispatch('bundle.preview',{'sessionId':sid,'bundle':'anchors'})
        await settled(app)
        assert app.state['actionStatus']['bundle.preview']['phase']=='error'
    finally: await app.close()

async def test_agent_fork_is_independent_and_duplicate_does_not_repeat(tmp_path):
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{'bundle':'anchors'})
        source=app._session();sid=source['id']
        source['messages']=[{'id':'user','role':'user','text':'keep'}]
        await app.app_bridge('dispatch',{'action':'bundle.preview','args':{'sessionId':sid,'bundle':'work'}},sid)
        await settled(app)
        payload={'action':'bundle.fork','id':'fork-once','args':{'sessionId':sid,'bundle':'work','previewId':'token'}}
        await app.app_bridge('dispatch',payload,sid);await settled(app)
        child=app._session();assert child['id']!=sid and child['bundle']=='work'
        assert child['messages']==[{'id':'user','role':'user','text':'keep','nativeIndex':0}]
        assert source['bundle']=='anchors' and source['messages']==[{'id':'user','role':'user','text':'keep'}]
        await app.app_bridge('dispatch',payload,sid);await settled(app)
        assert len(app.state['sessions'])==2
        assert child['forkTranscript']['jobsReplayed'] is False
    finally: await app.close()


async def test_generic_runtime_action_cannot_bypass_root_switch_policy(tmp_path):
    runtime=Runtime();app=AppService(tmp_path,runtime=runtime,workspace=tmp_path);app.management=Management(app)
    try:
        await app.dispatch('session.create',{})
        sid=app._session()['id']
        await app.dispatch('runtime.control',{'sessionId':sid,'operation':'bundle.switch','args':{'bundle':'work'}})
        await settled(app)
        assert app.state['actionStatus']['runtime.control']['phase']=='error'
        assert not runtime.calls
    finally: await app.close()


async def test_unavailable_model_requires_explicit_reset_before_any_mutation(tmp_path,monkeypatch):
    home=tmp_path/'app';store,messages=seed(home,tmp_path)
    inspected={'bundle':'work','fingerprint':'same','selection':{'instance':'missing','model':'chosen'},'modelCompatible':False}
    async def preview(*args):return inspected.copy()
    monkeypatch.setattr('amplifier_web.bundle_selection.preview',preview)
    worker=SimpleNamespace(controls=SimpleNamespace(require_idle=lambda:None),workspace=tmp_path,
                           bundle_preview={**inspected,'previewId':'token'})
    with pytest.raises(ValueError,match='unavailable'):
        await switch(worker,{'bundle':'work','previewId':'token'})
    assert store.load('source')[0]==messages
    assert not (home/'sessions/source/bundle-transition.json').exists()
