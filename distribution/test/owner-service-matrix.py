"""Public installed Python owner service fences: persistent identity, no replay."""
import asyncio
import copy
import importlib
import tempfile
from pathlib import Path
import pytest

OWNERS = [
    ('diagnostics', 'amplifier_unified_diagnostics', '/'),
    ('notifications', 'amplifier_unified_notifications', '/'),
    ('workspace', 'amplifier_unified_workspaces', '/'),
    ('feedback', 'amplifier_unified_feedback', '.'),
    ('publishing', 'amplifier_unified_publishing', '.'),
    ('recall', 'amplifier_unified_recall', '.'),
    ('coordination', 'amplifier_unified_coordination', '.'),
]

async def unused(*_args, **_kwargs):
    raise AssertionError('Service lifecycle must not invoke host business actions')

async def notice(*_args, **_kwargs):
    return None

@pytest.mark.asyncio
@pytest.mark.parametrize('name,module,separator', OWNERS)
async def test_retained_service_fence(name,module,separator):
    Owner=importlib.import_module(module+'.owner').Owner
    with tempfile.TemporaryDirectory(prefix='owner-service-') as temporary:
        root=Path(temporary);workspace=root/'workspace';workspace.mkdir()
        config={'stateDirectory':str(root/'owner'),'dataDir':str(root/'owner'),
                'allowedRoots':[str(workspace)],'defaultRoot':str(workspace)}
        def create():
            if name in {'diagnostics','notifications'}:return Owner(config,notice,notice)
            if name=='workspace':return Owner(config,unused,notice)
            return Owner(config,unused,notice)
        owner=create()
        call=lambda op,args:owner.request('quiescence'+separator+op,args)
        identity={'installationId':'install','dataScope':'scope','ownerId':'platform',
                  'instanceId':'original','releaseDigest':'release'}
        context={'fenceId':'service-fence','commandId':'stop-once','purpose':'service-stop',
                 'instanceId':'original','dataScope':'scope','serviceIdentity':identity}
        try:
            info=await owner.request('initialize',{})
            assert info['quiescence']['serviceStop']=={'version':1}
            await asyncio.sleep(0)
            assert (await call('acquire',context))['acquired'] is True
            held=await call('inspect',{})
            assert held['intakeClosed'] and held['fence']['serviceIdentity']==identity
            changed=copy.deepcopy(context);changed['serviceIdentity']['ownerId']='foreign'
            with pytest.raises(ValueError):await call('release',{**changed,'outcome':'unknown'})
            await owner.close();owner=create()
            assert (await call('inspect',{}))['intakeClosed']
            with pytest.raises(ValueError):await call('release',{**context,'outcome':'unchanged','proof':{'kind':'admission-refused'}})
            generic={'verified':True,'fenceId':'service-fence','commandId':'stop-once',
                     'instanceId':'original','dataScope':'scope','outcome':'unchanged','receiptId':'proof'}
            with pytest.raises(ValueError):await call('release',{**context,'outcome':'unchanged','proof':generic})
            proof={**generic,'kind':'service-lifecycle','serviceOutcome':'stop-refused',
                   'expected':identity,'observed':identity,'refusalReceiptId':'refused'}
            assert (await call('release',{**context,'outcome':'unchanged','proof':proof}))['released']
            await owner.close();owner=create()
            with pytest.raises(ValueError):await call('release',{**context,'outcome':'unchanged','proof':{**proof,'refusalReceiptId':'first-changed-after-reopen'}})
            assert not (await call('inspect',{}))['intakeClosed']
            assert (await call('release',{**context,'outcome':'unchanged','proof':proof}))['released']
            with pytest.raises(ValueError):await call('release',{**context,'outcome':'unchanged','proof':{**proof,'refusalReceiptId':'changed'}})
            assert not (await call('inspect',{}))['intakeClosed']
            resumed=copy.deepcopy(context);resumed['fenceId']='second-fence';resumed['commandId']='second-stop'
            assert (await call('acquire',resumed))['acquired']
            await owner.close();owner=create()
            observed={**identity,'instanceId':'replacement'}
            ready={**generic,'fenceId':'second-fence','commandId':'second-stop','instanceId':'replacement',
                   'outcome':'ready','kind':'service-lifecycle','serviceOutcome':'resumed',
                   'expected':identity,'observed':observed,'resumeCommandId':'resume-once',
                   'exitReceiptId':'exit','readyReceiptId':'ready'}
            assert (await call('release',{**resumed,'outcome':'ready','proof':ready}))['released']
            assert not (await call('inspect',{}))['intakeClosed']
        finally:
            await owner.close()
