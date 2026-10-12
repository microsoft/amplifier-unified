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
        path=owner.default_root/'project';path.mkdir(parents=True);(path/'data').write_bytes(b'four')
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


def test_content_categories_exclude_nested_boundaries_and_count_links_once(tmp_path):
    app=tmp_path/'app';app.mkdir();workspace=app/'work';workspace.mkdir();runtime=app/'runtime';runtime.mkdir();cache=runtime/'cache';cache.mkdir()
    (app/'settings').write_bytes(b'a'*3);(workspace/'work').write_bytes(b'b'*7);(runtime/'code').write_bytes(b'c'*11);(cache/'cached').write_bytes(b'd'*13)
    os.link(workspace/'work',workspace/'same');(app/'linked').symlink_to(workspace)
    roots=[root(app,category='other'),root(workspace,'w','workspace',workspaceId='w',category='workspaces'),root(runtime,'runtime',category='runtime'),root(cache,'cache',category='cache'),root(app/'missing','backups',category='backups',optional=True)]
    result=usage.inspect(roots,{})
    assert result['view']=='categories' and result['bytes']==34
    rows={r['id']:r for r in result['rows']}
    assert rows['other']['bytes']==3 and rows['runtime']['bytes']==11 and rows['cache']['bytes']==13
    assert not rows['backups']['incomplete'] and result['incomplete']
    work=usage.inspect(roots,{'scope':'category','categoryId':'workspaces'})
    assert work['bytes']==7 and work['rows'][0]['workspaceId']=='w'
    folder=usage.inspect(roots,{'scope':'app','rootId':'runtime','categoryId':'runtime'})
    assert folder['bytes']==11 and next(r for r in folder['rows'] if r['id']=='cache')['separateCategory']
    with pytest.raises(ValueError,match='own storage category'):
        usage.inspect(roots,{'scope':'app','rootId':'runtime','path':'cache'})


def test_content_accounting_excludes_cli_configuration_but_counts_shared_history(tmp_path):
    cli=tmp_path/'cli';projects=cli/'projects';projects.mkdir(parents=True)
    (tmp_path/'settings').write_bytes(b'a'*2);(cli/'keys.env').write_bytes(b'secret');(projects/'history').write_bytes(b'h'*5)
    roots=[root(tmp_path,category='other'),root(cli,'cli',category=None),root(projects,'history','history',category='sessions')]
    result=usage.inspect(roots,{})
    assert result['bytes']==7 and not result['incomplete']
    assert usage.inspect(roots,{'scope':'category','categoryId':'sessions'})['bytes']==5
    with pytest.raises(ValueError,match='excluded'):
        usage.inspect(roots,{'scope':'app','rootId':'cli'})


async def test_owner_content_root_contract_groups_registered_workspaces_and_stays_passive(tmp_path):
    catalog=CatalogFixture();cfg=config(tmp_path);app=tmp_path/'app';app.mkdir();(app/'settings').write_bytes(b'abc')
    async def callback(method,args):
        if method=='storageRoots':return {'roots':[root(app)],'contentVersion':1,'contentRoots':[root(app,category='other')]}
        return await catalog(method,args)
    owner=Owner(cfg,callback)
    try:
        await owner.request('initialize',{'storageRootsVersion':1})
        path=owner.default_root/'project';path.mkdir(parents=True);(path/'data').write_bytes(b'four')
        row=(await action(owner,'add',{'path':str(path)},'register'))['workspace']
        async def inspect(args):return await owner.request('action',{'operation':'storage.inspect','args':args,'clientId':'a'})
        assert (await inspect({}))['bytes']==7
        category=await inspect({'scope':'category','categoryId':'workspaces'})
        assert category['rows'][0]['workspaceId']==row['id'] and category['bytes']==4
        await owner.request('quiescence/acquire',FENCE)
        assert (await inspect({'scope':'category','categoryId':'other'}))['bytes']==3
        assert not owner.setup_tasks
    finally:await owner.close()


def test_category_measurement_skips_unrelated_roots_and_preserves_global_budget(tmp_path,monkeypatch):
    runtime=tmp_path/'runtime';runtime.mkdir();saved=tmp_path/'saved';saved.mkdir()
    for i in range(12):(runtime/str(i)).write_bytes(b'x'*10)
    (saved/'conversation').write_bytes(b'abc')
    roots=[root(runtime,'runtime',category='runtime'),root(saved,'history','history',category='sessions')]
    monkeypatch.setattr(usage,'LIMIT',8)
    observed=[];original=usage._usage
    def measure(path,*args,**kwargs):
        observed.append(str(path));return original(path,*args,**kwargs)
    monkeypatch.setattr(usage,'_usage',measure)
    category=usage.inspect(roots,{'scope':'category','categoryId':'sessions'})
    assert category['bytes']==3 and not category['incomplete']
    assert observed==[str(saved)]
    result=usage.inspect(roots,{})
    rows={row['id']:row for row in result['rows']}
    assert rows['sessions']['bytes']==3 and not rows['sessions']['incomplete']
    assert rows['runtime']['incomplete'] and result['scanBudget']==8
    folder=usage.inspect(roots,{'scope':'app','rootId':'runtime'})
    assert len(folder['rows'])==12 and folder['incomplete']
    assert any('Scan limit' in row.get('measurementNote','') for row in folder['rows'])
    assert folder['label']=='runtime'


def test_shared_history_review_targets_are_metadata_only_and_depth_bound(tmp_path):
    sessions=tmp_path/'project-one'/'sessions';sessions.mkdir(parents=True)
    saved=sessions/'native-one';saved.mkdir();(saved/'transcript.json').write_text('private')
    (sessions/'.stable-lock').write_text('lock')
    (tmp_path/'linked').symlink_to(tmp_path/'project-one')
    roots=[root(tmp_path,'history','history',category='sessions')]
    args={'scope':'history','rootId':'history','categoryId':'sessions'}
    projects=usage.inspect(roots,args)['rows']
    assert next(r for r in projects if r['id']=='project-one')['historySelection']=={'project':'project-one'}
    assert 'historySelection' not in next(r for r in projects if r['id']=='linked')
    rows=usage.inspect(roots,{**args,'path':'project-one/sessions'})['rows']
    assert next(r for r in rows if r['id']=='native-one')['historySelection']=={'project':'project-one','nativeSessionId':'native-one'}
    assert 'historySelection' not in next(r for r in rows if r['id']=='.stable-lock')
    assert all('historySelection' not in r for r in usage.inspect(roots,{**args,'path':'project-one/sessions/native-one'})['rows'])
    assert 'private' not in str(projects)+str(rows)


async def test_only_registered_descendants_and_managed_files_count_as_workspace_storage(tmp_path, monkeypatch):
    catalog=CatalogFixture();cfg=config(tmp_path)
    cfg['allowedRoots']=[str(tmp_path)]
    app=tmp_path/'application';app.mkdir()
    native=tmp_path/'native';native.mkdir();history=native/'projects';history.mkdir();(history/'saved').write_bytes(b'h'*11)
    managed=app/'workspaces';managed.mkdir();(managed/'leftover').write_bytes(b'm'*5)
    async def callback(method,args):
        if method=='storageRoots':return {'roots':[root(app),root(native,'native','history')], 'contentVersion':1,
            'contentRoots':[root(app,category='other'),root(native,'native','history',category=None),root(history,'history','history',category='sessions'),root(managed,'managed',category='workspaces')]}
        return await catalog(method,args)
    owner=Owner(cfg,callback)
    try:
        await owner.request('initialize',{'storageRootsVersion':1})
        selected=owner.default_root/'registered';selected.mkdir(parents=True);(selected/'file').write_bytes(b'p'*17)
        unregistered=owner.default_root/'unregistered';unregistered.mkdir();(unregistered/'private').write_bytes(b'x'*10000)
        outside=tmp_path/'outside';outside.mkdir();(outside/'private').write_bytes(b'x'*10000)
        (selected/'link').symlink_to(outside)
        fake_home=tmp_path/'person';fake_home.mkdir();(fake_home/'private').write_bytes(b'x'*10000)
        monkeypatch.setattr(Path,'home',classmethod(lambda cls:fake_home))
        for n,path in enumerate([selected,outside,owner.default_root,app,native,fake_home]):
            await action(owner,'add',{'path':str(path)},'register-'+str(n))
        catalog.rows['remembered']={'id':'remembered','path':str(unregistered),'name':'Remembered cwd','registered':False,'hidden':False,'available':True}
        async def inspect(args):return await owner.request('action',{'operation':'storage.inspect','args':args,'clientId':'a'})
        workspace=await inspect({'scope':'category','categoryId':'workspaces'})
        assert workspace['bytes']==22
        assert {r['path'] for r in workspace['rows']}=={str(selected),str(managed)}
        assert (await inspect({'scope':'category','categoryId':'sessions'}))['bytes']==11
        assert 'folders outside it are not scanned' in workspace['workspaceScope']['description']
        assert workspace['workspaceScope']['root']==str(owner.default_root)
        from amplifier_unified_workspaces.owner import identity
        with pytest.raises(ValueError,match='outside the workspace storage scope'):
            await inspect({'scope':'workspace','workspaceId':identity(outside)})
        assert not owner.setup_tasks
    finally:await owner.close()


async def test_changed_workspace_default_expires_poll_and_cannot_reuse_old_measurements(tmp_path):
    catalog=CatalogFixture();cfg=config(tmp_path);owner=Owner(cfg,catalog)
    try:
        old=owner.default_root/'one';old.mkdir(parents=True);(old/'file').write_bytes(b'x'*19)
        await action(owner,'add',{'path':str(old)},'old')
        async def inspect(args):return await owner.request('action',{'operation':'storage.inspect','args':args,'clientId':'a'})
        first=await inspect({'progressive':True})
        original=owner.storage_scans.entries[first['scanId']];await original.task
        assert original.result['bytes']==19
        replacement=Path(owner.roots[0])/'different';replacement.mkdir()
        await action(owner,'defaults.set',{'defaultRoot':str(replacement),'expectedConfigRevision':owner.config_revision},'move-default')
        with pytest.raises(ValueError,match='expired'):await inspect({'progressive':True,'scanId':first['scanId']})
        assert original.stopped.is_set()
        second=await inspect({'progressive':True,'fromScanId':first['scanId']})
        changed=owner.storage_scans.entries[second['scanId']];await changed.task
        assert changed.cache is not original.cache and changed.result['bytes']==0
    finally:await owner.close()
