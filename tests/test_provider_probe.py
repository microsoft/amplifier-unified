import pytest
from amplifier_web import provider_probe

async def test_native_metadata_and_catalog_keep_config_close_provider_and_hide_secrets(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key,config):
            assert api_key=='private' and config['base_url']=='https://example.test'
        def get_info(self):return {'config_fields':[{'id':'api_key','field_type':'secret','default':'private'},{'id':'reasoning_effort','field_type':'choice','choices':['low','high']}]}
        async def list_models(self):return [{'id':'model','api_key':'private'}]
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    result=await provider_probe.query({'module':'test','action':'providers.models','config':{'api_key':'private','base_url':'https://example.test'}})
    assert result['models']==[{'id':'model'}] and closed==[True]
    assert result['configSchema']['fields'][1]['choices']==['low','high']
    assert 'private' not in str(result)

async def test_failed_catalog_still_closes_provider(monkeypatch):
    closed=[]
    class Provider:
        def get_info(self):return {}
        async def list_models(self):raise ValueError('private exception')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    with pytest.raises(ValueError):await provider_probe.query({'module':'test','action':'providers.models'})
    assert closed==[True]
