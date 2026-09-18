import pytest
from amplifier_web import provider_probe


async def test_native_metadata_and_catalog_materialize_config_close_providers_and_hide_secrets(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key,config):
            if config:
                assert api_key=='private' and config=={'api_key':'private','base_url':''}
            else:
                assert api_key is None and config=={}
        def get_info(self):
            return {'config_fields':[
                {'id':'api_key','field_type':'secret','required':True},
                {'id':'base_url','field_type':'text','required':False},
                {'id':'reasoning_effort','field_type':'choice','required':False,'choices':['low','high']},
            ]}
        async def list_models(self):return [{'id':'model','api_key':'private'}]
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    monkeypatch.setenv('TEST_PROBE_KEY','private')
    monkeypatch.delenv('TEST_PROBE_BASE_URL',raising=False)
    result=await provider_probe.query({'module':'test','action':'providers.models','config':{'api_key':'${TEST_PROBE_KEY}','base_url':'${TEST_PROBE_BASE_URL}'}})
    assert result['models']==[{'id':'model'}] and closed==[True,True]
    assert result['configSchema']['fields'][2]['choices']==['low','high']
    assert 'private' not in str(result)


async def test_schema_probe_uses_empty_config_without_listing_models_and_closes_provider(monkeypatch):
    closed=[]
    metadata_calls=[]
    class Provider:
        def __init__(self,*,api_key,config):
            assert api_key is None and config=={}
        def get_info(self):
            metadata_calls.append(True)
            return {'config_fields':[]}
        async def list_models(self):raise AssertionError('schema discovery must not list models')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    result=await provider_probe.query({'module':'test','action':'providers.schema','config':{'ignored':'${MISSING}'}})
    assert result['configSchema']=={'fields':[]} and closed==[True]
    assert metadata_calls == [True]


async def test_failed_catalog_still_closes_provider(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key=None,config=None):pass
        def get_info(self):return {'config_fields':[]}
        async def list_models(self):raise ValueError('private exception')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    with pytest.raises(ValueError):await provider_probe.query({'module':'test','action':'providers.models'})
    assert closed==[True,True]
