import os
from pathlib import Path
import pytest
from amplifier_unified_workspaces import storage_usage as usage
from amplifier_unified_workspaces.owner import Owner
from test_owner import CatalogFixture, config, action, FENCE


def root(path, id='app', scope='app', **more):
    return {'id':id, 'label':id, 'scope':scope, 'path':str(path), **more}


def test_accounting_hardlinks_nested_roots_hidden_files_and_drill(tmp_path):
    app=tmp_path/'app';app.mkdir();workspace=app/'workspace';workspace.mkdir()
    (app/'.settings').write_bytes(b'a'*10)
    (workspace/'one').write_bytes(b'b'*30)
    os.link(workspace/'one',workspace/'two')
    history=tmp_path/'history';history.mkdir();(history/'transcript').write_bytes(b'c'*20)
    roots=[root(workspace,'w','workspace',workspaceId='w'),root(app),root(history,'history','history')]
    result=usage.inspect(roots,{})
    assert result['bytes']==60 and not result['incomplete']
    assert next(r for r in result['rows'] if r['id']=='w')['includedIn']=='app'
    drill=usage.inspect(roots,{'scope':'workspace','workspaceId':'w'})
    assert drill['bytes']==30 and len(drill['rows'])==2 and drill['parent'] is None
    assert usage.inspect(roots,{'scope':'app','path':'workspace'})['parent']=='.'


def test_links_missing_roots_and_scope_escape_never_read_targets(tmp_path):
    app=tmp_path/'app';app.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (outside/'private').write_bytes(b'x'*10000);(app/'link').symlink_to(outside)
    result=usage.inspect([root(app),root(tmp_path/'missing','history','history')],{})
    assert result['bytes']==0 and result['incomplete']
    for path in ('../outside',str(outside),'link'):
        with pytest.raises(ValueError):usage.inspect([root(app)],{'scope':'app','path':path})
    parent=tmp_path/'parent-link';parent.symlink_to(app)
    assert usage.inspect([root(parent/'link')],{})['incomplete']
    with pytest.raises(ValueError):usage.inspect([root(app)],{'scope':'workspace','workspaceId':'foreign'})
    with pytest.raises(ValueError):usage.inspect([root(app)],{'path':'foo'})


def test_budgets_are_explicit_lower_bounds(tmp_path,monkeypatch):
    for i in range(8):(tmp_path/str(i)).write_bytes(b'x'*10)
    monkeypatch.setattr(usage,'ROW_LIMIT',2)
    result=usage.inspect([root(tmp_path)],{'scope':'app'})
    assert len(result['rows'])==2 and result['bytes']==20 and result['incomplete']
    monkeypatch.setattr(usage,'LIMIT',3)
    result=usage.inspect([root(tmp_path)],{})
    assert result['bytes']<=20 and result['incomplete']


def test_directory_replacement_during_open_does_not_follow_link(tmp_path,monkeypatch):
    app=tmp_path/'app';app.mkdir();folder=app/'child';folder.mkdir()
    outside=tmp_path/'outside';outside.mkdir();(outside/'private').write_bytes(b'z'*500)
    original=usage.os.open
    def substitute(path,flags,*args,**kwargs):
        if path=='child' and kwargs.get('dir_fd') is not None:
            folder.rename(app/'kept');folder.symlink_to(outside)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(usage.os,'open',substitute)
    result=usage.inspect([root(app)],{})
    assert result['bytes']==0 and result['incomplete']
    assert (outside/'private').read_bytes()==b'z'*500


async def test_owner_uses_native_roots_and_registered_archived_workspaces_without_starting_work(tmp_path):
    catalog=CatalogFixture();cfg=config(tmp_path);app=tmp_path/'app';app.mkdir();(app/'one').write_bytes(b'abc')
    seen=[]
    async def callback(method,args):
        seen.append(method)
        if method=='storageRoots':return {'roots':[root(app)]}
        return await catalog(method,args)
    owner=Owner(cfg,callback)
    try:
        await owner.request('initialize',{'storageRootsVersion':1})
        path=Path(owner.roots[0])/'project';path.mkdir();(path/'data').write_bytes(b'four')
        row=(await action(owner,'add',{'path':str(path)},'register'))['workspace']
        async def inspect(args):return await owner.request('action',{'operation':'storage.inspect','args':args,'clientId':'a'})
        result=await inspect({})
        assert result['bytes']==7 and not result['incomplete']
        assert (await inspect({'scope':'workspace','workspaceId':row['id']}))['bytes']==4
        before=owner.unresolved()
        await owner.request('quiescence/acquire',FENCE)
        assert (await inspect({'scope':'app'}))['bytes']==3
        assert owner.unresolved()==before
        with pytest.raises(ValueError):await inspect({'scope':'workspace','workspaceId':'foreign'})
        assert 'storageRoots' in seen and not owner.setup_tasks
    finally:await owner.close()
