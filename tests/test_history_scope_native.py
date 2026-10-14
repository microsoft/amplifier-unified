"""Actual Core/Foundation app-control mounts use the shared history policy."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')


@pytest.mark.parametrize('mode',['finite','resumed','persistent','nested','recipe'])
async def test_native_root_and_child_history_scope(tmp_path,monkeypatch,mode):
    from amplifier_foundation.bundle import Bundle,BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children,_PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.service import AppService

    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native'))
    spec=importlib.util.spec_from_file_location('history_scope_child_fixture',Path(__file__).parent/'fixtures/standalone_children_probe.py')
    fixture=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,fixture)
    spec.loader.exec_module(fixture)
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},'agents':{'worker':{'instruction':'Read history'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent for name in ('loop-live','context-simple')}
    prepared=fixture.ProbePrepared(plan,BundleModuleResolver(paths),Bundle(name='fixture',session=plan['session'],agents=plan['agents']))
    approvals=Approvals(None,AsyncMock(return_value='deny'))
    parent=await prepared.create_session(session_id='root',approval_system=approvals)
    app=AppService(tmp_path/'app',workspace=tmp_path)
    root=app._new_session({'title':'Root'})
    (tmp_path/'other').mkdir()
    other=app._new_session({'title':'Other','workspace':str(tmp_path/'other')})
    other['messages']=[{'id':'retained','role':'assistant','text':'Other own-history'}]
    app.state['sessions']=[root,other]
    app.state['selectedSessionId']=root['id']
    app._publish()
    worker=Worker()
    worker.session=parent
    worker.runtime=SimpleNamespace(generation={'id':'generation'})
    async def bridge(operation,args):
        return await app.app_bridge(operation,args,root['id'])
    worker.bridge=bridge
    await install_app_access(parent.coordinator,worker.app_access_bridge(parent.coordinator))
    registry=await install_children(parent,prepared,Runtime('root'),SessionStore(tmp_path/'children'),approvals)
    seen=[]
    async def inspect(control):
        denied=await control.execute({'operation':'history','args':{'action':'read','session_id':other['id']}})
        assert not denied.success and 'scope' in str(denied.error)
        allowed=await control.execute({'operation':'history','args':{'action':'read','session_id':other['id'],'scope':'all'}})
        assert allowed.success,allowed.error
        assert allowed.output['messages'][0]['text']=='Other own-history'
        exact={'action':'coordination.read','args':{'sessionId':other['id'],'messageId':'retained'}}
        denied=await control.execute({'operation':'dispatch','args':exact})
        assert not denied.success and 'scope' in str(denied.error)
        allowed=await control.execute({'operation':'dispatch','args':{**exact,'args':{**exact['args'],'scope':'all'}}})
        assert allowed.success,allowed.error
        assert allowed.output['result']['message']['text']=='Other own-history'
    async def execute(self,prompt,context,providers,tools,hooks,coordinator):
        await inspect(tools['app_control'])
        seen.append(coordinator.session_id)
        if mode=='nested' and coordinator.session_id=='child':
            await registry.spawn('worker','nested',registry.sessions['child'],sub_session_id='grandchild')
        return 'done'
    monkeypatch.setattr(fixture.FiniteOrchestrator,'execute',execute)
    token=_PERSISTENT.set(mode=='persistent')
    try:
        await inspect(parent.coordinator.get('tools')['app_control'])
        await registry.spawn('worker','read',parent,sub_session_id='child',**({'use_subprocess':False} if mode=='recipe' else {}))
        if mode=='resumed':
            await registry.resume('child','read again',parent)
        assert len(seen)==(2 if mode in {'resumed','nested'} else 1)
        assert app.state['selectedSessionId']==root['id']
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()
        await app.close()
