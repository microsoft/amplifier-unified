"""Workspace predicates prune native candidates without bypassing owner overlays."""
from copy import deepcopy
import pytest
from amplifier_web.native_catalog import NativeCatalog
from test_shell_modules import service


@pytest.mark.parametrize('section', [None, 'shortcuts', 'search', 'pinned'])
async def test_workspace_candidates_equal_complete_query_with_aliases_and_overlays(service, tmp_path, monkeypatch, section):
    one, two = service.state['workspaces'][:2]
    # Last path owner controls fallback, while a registered ID wins over path.
    alias = {**one, 'id':'same-path', 'name':'Same path alias'}
    service.state['workspaces'].append(alias)
    template = {'sessionKind':'root','nativeProject':'p','title':'Native root',
                'workspaceId':'one','workspace':one['path'],'recentActivityAt':1}
    rows = [{**template, 'id':identity, 'nativeIdentity':identity, **changes} for identity,changes in [
        ('by-id',{}), ('known-id-other-path',{'workspace':two['path']}),
        ('unknown-id',{'workspaceId':'missing'}), ('null-id',{'workspaceId':None}),
        ('other-id-selected-path',{'workspaceId':'two'}),
        ('shadow',{}), ('duplicate',{}), ('hidden',{})]]
    catalog=NativeCatalog(tmp_path/'native.sqlite3',tmp_path/'native')
    catalog.save({'p':{'workspace':one,'sessions':rows},
                  'q':{'workspace':two,'sessions':[{**template,'id':'other-duplicate','nativeIdentity':'duplicate','nativeProject':'q'}]}}, {}, {}, ())
    # Project metadata is authoritative for native placement. Set mixed facts in
    # the synthetic scalar fixture to exercise the exact registry resolution.
    with catalog.connect() as db:
        for row in rows:
            db.execute('UPDATE native_rows SET workspace_id=?,workspace_path=? WHERE namespace=? AND project=? AND identity=?',
                       (row['workspaceId'],row['workspace'],catalog.namespace,'p',row['nativeIdentity']))
    local=service.state['sessions'][0]
    local.update(nativeProject='p',nativeIdentity='shadow',workspaceId='two',workspace=two['path'])
    service.state['pinnedSessionIds']=['by-id','unknown-id']
    service._save_changes(sessions={'alpha','beta'},globals={'workspaces','pinnedSessionIds'})
    service.directory.bind(catalog)
    effective=service.directory.effective
    for selected in ('one','same-path','two','absent'):
        state={**service.state,'selectedWorkspaceId':selected,'view':{**service.state['view'],'navChatScope':'workspace'}}
        actual=service.directory.chats(state,section=section)
        def complete(*args,**kwargs):
            kwargs.pop('native_root_workspace',None)
            return effective(*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(service.directory,'effective',complete)
            expected=service.directory.chats(state,section=section)
        assert actual==expected
        assert 'shadow' not in [row['id'] for row in actual['items']]
    service.state['hiddenNativeWorkspaces']=[one['id']]
    state={**service.state,'selectedWorkspaceId':'one','view':{**service.state['view'],'navChatScope':'workspace'}}
    actual=service.directory.chats(state,section=section)
    with monkeypatch.context() as patch:
        patch.setattr(service.directory,'effective',complete)
        assert actual==service.directory.chats(state,section=section)


@pytest.mark.parametrize('unrelated', [270, 2700, 27000])
async def test_workspace_candidate_work_does_not_scale_with_other_workspaces(service,tmp_path,monkeypatch,unrelated):
    from test_native_directory import seed
    from test_history_directory_query_budget import measure_sql
    catalog=NativeCatalog(tmp_path/'native.sqlite3',tmp_path/'native')
    _,wanted=seed(catalog,tmp_path/'wanted',25)
    _,other=seed(catalog,tmp_path/'other',unrelated)
    service.state['workspaces'].extend([wanted,other])
    service._save_changes(sessions={'alpha','beta'},globals={'workspaces'})
    service.directory.bind(catalog)
    state={**service.state,'selectedWorkspaceId':wanted['id'],'view':{**service.state['view'],'navChatScope':'workspace'}}
    actual,vm,ms=measure_sql(service,lambda:service.directory.chats(state))
    effective=service.directory.effective
    def complete(*args,**kwargs):
        kwargs.pop('native_root_workspace',None)
        return effective(*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(service.directory,'effective',complete)
        expected,full_vm,full_ms=measure_sql(service,lambda:service.directory.chats(state))
    assert actual==expected and actual['total']==25
    assert vm<60000
    print({'unrelatedRoots':unrelated,'selectedRoots':25,'candidateVm':vm,'fullVm':full_vm,'candidateMs':ms,'fullMs':full_ms})
