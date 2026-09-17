import asyncio
from collections import Counter
import pytest
import yaml
from amplifier_web.provider_catalog import ProviderCatalog
from amplifier_web.setup import SetupManager
from amplifier_web.host.config import write_private


def settings(home,models=('first','second')):
    write_private(home/'config/settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':str(i),'module':'provider-openai','config':{'default_model':model,'api_key':'${CATALOG_TEST_KEY}'}} for i,model in enumerate(models)]}}))


async def test_catalog_single_flight_refresh_and_failure_recovery():
    cache=ProviderCatalog();calls=0
    async def load():
        nonlocal calls
        calls+=1;await asyncio.sleep(.01);return {'models':['one']}
    results=await asyncio.gather(*(cache.get('one',load) for _ in range(5)))
    assert calls==1
    results[0]['models'].append('changed')
    assert (await cache.get('one',load))['models']==['one']
    await cache.get('one',load,refresh=True);assert calls==2
    async def fail():raise ValueError('Unavailable')
    with pytest.raises(ValueError):await cache.get('failed',fail)
    with pytest.raises(ValueError):await cache.get('failed',load)
    assert (await cache.get('failed',load,refresh=True))['models']==['one']
    await cache.close()


async def test_only_changed_provider_configuration_is_refetched(tmp_path,monkeypatch):
    monkeypatch.setenv('CATALOG_TEST_KEY','first-private-value');settings(tmp_path)
    cache=ProviderCatalog();calls=Counter()
    async def probe(self,action,args,workspace):
        calls[args['id']]+=1
        return {'models':[{'id':args['id']}],'modelsProviderId':args['id']}
    monkeypatch.setattr(SetupManager,'probe',probe)
    async def get(identity):
        return await SetupManager(tmp_path,catalog=cache).perform('providers.models',{'id':identity,'workspace':str(tmp_path)})
    await asyncio.gather(get('0'),get('1'));await asyncio.gather(get('0'),get('1'))
    assert calls=={'0':1,'1':1}
    settings(tmp_path,('changed','second'))
    await asyncio.gather(get('0'),get('1'));assert calls=={'0':2,'1':1}
    monkeypatch.setenv('CATALOG_TEST_KEY','rotated-private-value')
    await asyncio.gather(get('0'),get('1'));assert calls=={'0':3,'1':2}
    # Credentials and their fingerprints never become public model results.
    assert 'private-value' not in str(await get('0'))
    await cache.close()


async def test_startup_warms_all_providers_and_reuses_catalogs(tmp_path,monkeypatch):
    from amplifier_web.server import create_app
    monkeypatch.setenv('CATALOG_TEST_KEY','fixture');settings(tmp_path)
    calls=Counter()
    async def probe(self,action,args,workspace):
        calls[args['id']]+=1
        await asyncio.sleep(.005)
        return {'models':[{'id':'model-'+args['id']}],'modelsProviderId':args['id'],'providerMetadata':{'module':'provider-openai','info':{}}}
    monkeypatch.setattr(SetupManager,'probe',probe)
    class Runtime:
        async def close(self):pass
    app=await create_app(tmp_path,workspace=tmp_path,runtime=Runtime(),voice=False,background_updates=False)
    service=app['service']
    try:
        async def settled():
            for _ in range(100):
                entries=service.state.get('setup',{}).get('providerCatalogs',{})
                if len(entries)==2 and all(row['phase']=='ready' for row in entries.values()):return
                await asyncio.sleep(.01)
            pytest.fail('Catalogs did not finish')
        await settled();assert calls=={'0':1,'1':1}
        await service.management.command('providers.list',{})
        await asyncio.sleep(.03);assert calls=={'0':1,'1':1}
        await service.management.command('providers.save',{'id':'0','module':'provider-openai','config':{'default_model':'changed'}})
        await settled();await asyncio.sleep(.03)
        assert calls=={'0':2,'1':1}
    finally:await service.close()


async def test_remount_reuses_unchanged_provider_catalogs(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    calls=Counter()
    class Runtime:
        async def close(self):pass
        async def control(self,sid,operation,args):
            calls[args['instance']]+=1
            return {'models':[{'id':args['instance']+'-model'}]}
    service=AppService(tmp_path,Runtime(),workspace=tmp_path);service.management=Management(service)
    await service.dispatch('session.create',{})
    sid=service.state['selectedSessionId']
    try:
        await service.management.warm_runtime_models(sid,[{'id':'one','catalogKey':'one-config'},{'id':'two','catalogKey':'two-config'}],'first')
        assert calls=={'one':1,'two':1}
        await service.management.warm_runtime_models(sid,[{'id':'one','catalogKey':'changed-config'},{'id':'two','catalogKey':'two-config'}],'second')
        assert calls=={'one':2,'two':1}
        assert set(service.state['runtimeControl'][sid]['modelCatalogs'])=={'one','two'}
        await service.management.warm_runtime_models(sid,[],'third')
        assert service.state['runtimeControl'][sid]['modelCatalogs']=={}
    finally:await service.close()


async def test_changed_config_during_discovery_never_reuses_old_result(tmp_path,monkeypatch):
    settings(tmp_path);cache=ProviderCatalog();entered=asyncio.Event();finish=asyncio.Event()
    async def probe(self,action,args,workspace):
        entered.set();await finish.wait()
        return {'models':[{'id':'stale'}],'modelsProviderId':args['id']}
    monkeypatch.setattr(SetupManager,'probe',probe)
    manager=SetupManager(tmp_path,catalog=cache)
    old=('providers.models',manager.catalog_key({'id':'0'},str(tmp_path)))
    pending=asyncio.create_task(manager.perform('providers.models',{'id':'0','workspace':str(tmp_path)}))
    await entered.wait();settings(tmp_path,('changed','second'));finish.set()
    with pytest.raises(ValueError,match='configuration changed'):await pending
    assert old not in cache.entries
    await cache.close()
