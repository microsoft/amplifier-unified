import json
from types import SimpleNamespace
import pytest
from amplifier_web import provider_probe
from amplifier_web.provider_test import safe_text


@pytest.mark.parametrize('failure', [False, True])
async def test_real_message_uses_saved_model_without_catalog_or_history(monkeypatch, failure):
    closed=[]
    class Provider:
        def __init__(self, config=None): self.config=config
        def get_info(self): return {'config_fields': []}
        async def list_models(self): raise AssertionError('A catalog is not an inference test')
        async def complete(self, request):
            assert request.model == 'saved-model'
            assert len(request.messages) == 1 and request.messages[0].content == 'Reply with only OK.'
            assert not request.tools and request.max_output_tokens == 256
            assert self.config['api_key'] == 'private-credential-12345'
            if failure:
                error=ValueError('insufficient_quota private-credential-12345')
                error.status_code=429
                raise error
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='OK')], model='saved-model')
        async def close(self): closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda _:Provider)
    result=await provider_probe.query({'action':'providers.testMessage','module':'test',
        'config':{'api_key':'private-credential-12345','default_model':'saved-model'}})
    assert len(closed)==2
    assert result['messageTest']['reachable'] is not failure
    assert 'private-credential-12345' not in json.dumps(result)
    if failure:
        assert result['messageTest']['statusCode']==429
        assert 'insufficient_quota' in result['messageTest']['error']
    else: assert result['messageTest']['response']=='OK'


def test_error_redacts_nested_tokens_environment_and_urls(monkeypatch):
    monkeypatch.setenv('CUSTOM_API_KEY','secret-56789')
    text=safe_text('error secret-56789 nested-secret https://host/path?token=abc sk-abcdef123', {'nested':{'token':'nested-secret'}})
    assert all(value not in text for value in ('secret-56789','nested-secret','host/path','sk-abcdef123'))
    assert 'error' in text


async def test_plan_connection_catalog_works_but_capped_message_is_unavailable(monkeypatch):
    calls=[]
    class Provider:
        def __init__(self,config=None):pass
        def get_info(self):return {'config_fields':[],'defaults':{'auth_mode':'chatgpt_plan'}}
        async def list_models(self):calls.append('catalog');return [{'id':'account-model'}]
        async def complete(self,request):raise AssertionError('Never send an uncapped Plan message')
        async def close(self):pass
    monkeypatch.setattr(provider_probe,'provider_class',lambda _:Provider)
    request={'module':'provider-openai-chatgpt','config':{'auth_mode':'chatgpt_plan','default_model':'account-model'}}
    check=await provider_probe.query({**request,'action':'providers.test'})
    assert check['test']['reachable'] and check['test']['modelCount']==1
    assert check['test']['method']=='provider.list_models'
    message=await provider_probe.query({**request,'action':'providers.testMessage'})
    assert message['messageTest']['supported'] is False
    assert message['messageTest']['errorType']=='UnsupportedMessageTest'
    assert 'no test message was sent' in message['messageTest']['error']
    assert calls==['catalog']
