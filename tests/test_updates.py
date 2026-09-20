import json
from pathlib import Path
import subprocess
import time
import pytest
from amplifier_web.service import AppService,AppError
from amplifier_web.updates import UpdateManager,active_release,foundation_home,safe_label

class Runtime:
    def __init__(self): self.closed=0
    async def close(self): self.closed+=1

def git(path,*args):
    return subprocess.check_output(['git',*args],cwd=path,text=True,stderr=subprocess.DEVNULL).strip()

@pytest.fixture
async def app(tmp_path,monkeypatch,repo):
    from amplifier_web import app_updates,updates
    async def check():return {"id":"application","label":"Amplifier Unified","status":"current"}
    monkeypatch.setattr(app_updates,"check",check)
    original=updates.process
    async def process(*args,**kwargs):
        if 'fetch' in args:
            args=tuple(str(repo[0]) if arg=='https://example.invalid/repo' else arg for arg in args)
        return await original(*args,**kwargs)
    monkeypatch.setattr(updates,'process',process)
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{'bundle':'anchors-amp-dev'})
    service.update_manager=UpdateManager(service)
    yield service
    await service.close()

@pytest.fixture
def repo(tmp_path):
    remote=tmp_path/'remote';remote.mkdir()
    git(remote,'init','-b','main');git(remote,'config','user.email','test@example.invalid');git(remote,'config','user.name','Fixture')
    (remote/'bundle.md').write_text('first')
    git(remote,'add','.');git(remote,'commit','-m','first');old=git(remote,'rev-parse','HEAD')
    (remote/'bundle.md').write_text('second');git(remote,'commit','-am','second');new=git(remote,'rev-parse','HEAD')
    return remote,old,new

async def prepare(app,repo):
    remote,old,new=repo
    root=app.data_dir/'foundation/cache/repository';root.parent.mkdir(parents=True)
    git(root.parent,'clone',str(remote),str(root));git(root,'checkout','--detach',old)
    (root/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo','ref':'main','commit':old}))
    (app.data_dir/'config').mkdir(exist_ok=True);(app.data_dir/'config/settings.yaml').write_text('{}')
    manager=app.update_manager
    manager.inventory=[{'id':'repo','path':'cache/repository','url':'https://example.invalid/repo','label':'Fixture','current':old,'latest':new,'ref':'main','eligible':True,'status':'update'}]
    return manager,root

async def test_stage_activate_and_rollback_preserve_original_cache(app,repo):
    manager,root=await prepare(app,repo)
    async def validate(stage,release):
        assert git(stage/'foundation/cache/repository','rev-parse','HEAD')==repo[2]
        assert git(root,'rev-parse','HEAD')==repo[1]
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert git(foundation_home(app.data_dir)/'cache/repository','rev-parse','HEAD')==repo[2]
    assert git(root,'rev-parse','HEAD')==repo[1]
    await manager.rollback()
    assert active_release(app.data_dir)['current'] is None
    assert foundation_home(app.data_dir)==app.data_dir/'foundation'

async def test_failed_validation_does_not_activate_or_close_sessions(app,repo):
    manager,root=await prepare(app,repo)
    async def fail(*args):raise RuntimeError('incompatible')
    manager.validate=fail
    await manager.install()
    assert app.state['updates']['phase']=='error'
    assert not active_release(app.data_dir)
    assert app.runtime.closed==0
    assert git(root,'rev-parse','HEAD')==repo[1]

async def test_active_work_defers_activation_then_uses_validated_snapshot(app,repo):
    manager,root=await prepare(app,repo)
    async def validate(*args):pass
    manager.validate=validate
    app.state['sessions'][0]['status']='working'
    await manager.install()
    assert app.state['updates']['phase']=='staged'
    assert not active_release(app.data_dir)
    app.state['sessions'][0]['status']='idle'
    await manager.tick()
    assert active_release(app.data_dir)['current']

async def test_modified_cache_cannot_be_overwritten(app,repo):
    manager,root=await prepare(app,repo)
    (root/'bundle.md').write_text('User edit')
    await manager.install()
    assert app.state['updates']['phase']=='error'
    assert (root/'bundle.md').read_text()=='User edit'
    assert not active_release(app.data_dir)

async def test_check_is_read_only_and_failures_are_not_current(app,repo,monkeypatch):
    manager,root=await prepare(app,repo)
    from amplifier_web import updates
    original=updates.process
    async def process(*args,**kwargs):
        if args[1]=='ls-remote':raise RuntimeError('offline')
        return await original(*args,**kwargs)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    assert app.state['updates']['items'][0]['status']=='check_failed'
    assert git(root,'rev-parse','HEAD')==repo[1]
    assert 'url' not in app.state['updates']['items'][0]

async def test_default_and_update_settings_preserve_existing_conversation(app):
    assert app.state['settings']['bundle']=='work'
    assert app.state['sessions'][0]['bundle']=='anchors-amp-dev'
    await app.dispatch('settings.update',{'patch':{'updates':{'autoCheck':False}}})
    with pytest.raises(AppError):await app.dispatch('settings.update',{'patch':{'updates':{'autoInstall':True}}})
    with pytest.raises(AppError):await app.dispatch('settings.update',{'patch':{'updates':{'intervalHours':0}}})
    app.state['updates']['phase']='activating'
    with pytest.raises(AppError):await app.dispatch('conversation.send',{'text':'new work'})
    with pytest.raises(AppError):await app.voice_delegate('new work','voice:1')

async def test_background_policy_does_not_install_without_opt_in(app):
    manager=app.update_manager;events=[]
    async def check():
        events.append('check');app.state['updates'].update(phase='available',available=1,lastCheck=time.time())
    async def install():events.append('install')
    manager.check=check;manager.install=install
    await manager.tick();assert events==['check']
    app.state['settings']['updates']['autoInstall']=True
    await manager.tick();assert events==['check','install']


def test_source_labels_omit_credentials():
    assert safe_label('https://user:private@example.org/owner/repo.git?token=secret')=='example.org/owner/repo'

def test_both_release_pointer_fields_reject_path_traversal(tmp_path):
    (tmp_path/'updates').mkdir()
    for field in ('current','previous'):
        (tmp_path/'updates/active.json').write_text(json.dumps({field:'../../outside'}))
        with pytest.raises(ValueError,match='release identity'):active_release(tmp_path)

async def test_repeated_install_reuses_pending_generation(app,repo):
    manager,root=await prepare(app,repo)
    validations=[]
    async def validate(stage,release):validations.append(release)
    manager.validate=validate
    app._session()['status']='working'
    await manager.install()
    pending=app.state['updates']['pendingRelease']
    await manager.install()
    assert app.state['updates']['pendingRelease']==pending
    assert len(validations)==1

async def test_staging_preserves_symlinks_without_copying_external_workspace(app,repo,tmp_path):
    manager,root=await prepare(app,repo)
    outside=tmp_path/'outside';outside.mkdir();(outside/'private.txt').write_text('outside staging ownership')
    (root/'external').symlink_to(outside,target_is_directory=True)
    async def validate(stage,release):
        link=stage/'foundation/cache/repository/external'
        assert link.is_symlink()
        assert link.resolve()==outside
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert (outside/'private.txt').read_text()=='outside staging ownership'


def test_identical_cache_copies_group_without_losing_refs_or_versions():
    from amplifier_web.updates import group_sources
    first={'id':'a','label':'github.com/owner/computer-use','ref':'main','kind':'bundle / module','current':'old','latest':'new','status':'update','path':'private/cache','url':'https://private','eligible':True}
    rows=group_sources([first,{**first,'id':'b','path':'other/cache'},{**first,'id':'c','ref':'dev'},{**first,'id':'d','current':'different'}])
    assert len(rows)==3
    assert rows[0]['cacheCopies']==2
    assert not {'url','path','eligible'} & rows[0].keys()
    assert group_sources(rows)==rows


async def test_grouped_display_still_installs_every_cached_copy(app,repo):
    import shutil
    manager,root=await prepare(app,repo)
    second=root.parent/'second';shutil.copytree(root,second)
    manager.inventory.append({**manager.inventory[0],'id':'second','path':'cache/second'})
    async def validate(stage,release):
        for name in ('repository','second'):
            assert git(stage/'foundation/cache'/name,'rev-parse','HEAD')==repo[2]
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert git(foundation_home(app.data_dir)/'cache/second','rev-parse','HEAD')==repo[2]


async def test_ecosystem_activation_does_not_claim_application_was_installed(app,repo):
    manager,_=await prepare(app,repo)
    release={'id':'application','label':'Amplifier Unified','kind':'app','current':'0.1.0','latest':'v99.0.0','status':'update'}
    app.state['updates'].update(application=release,items=[release,{'id':'module','label':'Fixture module','current':'old','latest':'new','status':'update'}],appAvailable=True)
    # A staged ecosystem can coexist with a subsequently discovered app update.
    app.state['updates']['appAvailable']=False
    async def validate(*args):pass
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['items'][0]==release
    assert app.state['updates']['application']==release
    assert app.state['updates']['available']==1
    assert app.state['updates']['items'][1]['status']=='current'
