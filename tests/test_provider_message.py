import json
from types import SimpleNamespace
import pytest
from amplifier_web import provider_probe
from amplifier_web.provider_test import safe_text, test_message as send_test_message

# What a person reads before the error once the request exists, word for word.
REQUEST_HELD = 'The test request held one short message and one tool definition. The error: '


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
            assert [tool.name for tool in request.tools] == ['connection_test']
            assert request.max_output_tokens == 256
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
        # The service's own sentence stays in the paragraph, after what the request held.
        assert result['messageTest']['error'] == REQUEST_HELD + 'insufficient_quota [REDACTED]'
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
        # "Test connection" accepts only a model list that the provider declares as its service's own.
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
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


class _Provider:
    """The object that the helper built for a connection. It answers one request and runs no tool."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def complete(self, request):
        self.requests.append(request)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _reply(content=(), tool_calls=None):
    from amplifier_core.message_models import ChatResponse
    return ChatResponse(content=list(content), tool_calls=tool_calls)


def _text(value):
    from amplifier_core.message_models import TextBlock
    return TextBlock(text=value)


def _call_in_the_content():
    from amplifier_core.message_models import ToolCallBlock
    return _reply([ToolCallBlock(id='call-1', name='connection_test', input={'note': 'x'})])


def _call_beside_the_content():
    from amplifier_core.message_models import ToolCall
    return _reply([], [ToolCall(id='call-1', name='connection_test', arguments={'note': 'x'})])


async def test_the_test_request_holds_one_short_message_and_one_tool_definition():
    provider = _Provider(_reply([_text('OK')]))
    result = await send_test_message(provider, {}, 'saved-model')
    assert result['reachable'] is True and result['response'] == 'OK'
    [request] = provider.requests
    assert [message.content for message in request.messages] == ['Reply with only OK.']
    [tool] = request.tools
    assert (tool.name, tool.description) == ('connection_test', 'Not used. Reply with text.')
    assert tool.parameters == {'type': 'object', 'properties': {'note': {'type': 'string'}}}
    assert request.tool_choice is None and request.max_output_tokens == 256 and request.stream is False


@pytest.mark.parametrize('answer', [_call_in_the_content, _call_beside_the_content])
async def test_an_answer_with_a_tool_call_and_no_text_passes_and_the_call_is_not_run(answer):
    provider = _Provider(answer())
    result = await send_test_message(provider, {}, 'saved-model')
    assert result['reachable'] is True
    assert result['response'] == 'The model answered with a tool call.'
    assert 'error' not in result
    # One request and no second one: nothing ran the tool and nothing sent a result back.
    assert len(provider.requests) == 1


async def test_text_beside_a_tool_call_is_what_the_person_reads():
    from amplifier_core.message_models import ToolCall
    provider = _Provider(_reply([_text('OK')], [ToolCall(id='call-1', name='connection_test', arguments={})]))
    result = await send_test_message(provider, {}, 'saved-model')
    assert result['reachable'] is True and result['response'] == 'OK'


@pytest.mark.parametrize('answer', [lambda: _reply([]), lambda: _reply([_text('  ')])])
async def test_an_answer_with_no_text_and_no_tool_call_fails(answer):
    result = await send_test_message(_Provider(answer()), {}, 'saved-model')
    assert result['reachable'] is False and result['errorType'] == 'ValueError'
    assert result['error'] == (REQUEST_HELD + 'The provider returned no text and no tool call. '
                               'Check model compatibility or its output limit.')


async def test_no_answer_in_time_keeps_its_sentence():
    # asyncio.wait_for raises this after 45 seconds. The case raises it at once.
    result = await send_test_message(_Provider(TimeoutError()), {}, 'saved-model')
    assert result['reachable'] is False and result['errorType'] == 'TimeoutError'
    assert result['error'] == REQUEST_HELD + 'The provider did not respond within 45 seconds.'


async def test_a_refusal_by_the_service_keeps_its_sentence_redacted_and_bounded():
    refusal = RuntimeError("Unsupported parameter: 'tools' is not supported with this model. key private-credential-12345")
    refusal.status_code = 400
    config = {'api_key': 'private-credential-12345'}
    result = await send_test_message(_Provider(refusal), config, 'saved-model')
    assert result['reachable'] is False and result['statusCode'] == 400
    assert result['error'] == (REQUEST_HELD + "Unsupported parameter: 'tools' is not supported with this model. "
                               'key [REDACTED]')
    long = await send_test_message(_Provider(RuntimeError('x' * 5000)), config, 'saved-model')
    assert long['error'].startswith(REQUEST_HELD) and len(long['error']) == 1000


async def test_a_failure_before_the_request_exists_does_not_say_what_the_request_held(monkeypatch):
    from amplifier_core import message_models

    def refuse(**fields):
        raise ValueError('The request could not be built.')
    monkeypatch.setattr(message_models, 'ChatRequest', refuse)
    provider = _Provider(_reply([_text('OK')]))
    result = await send_test_message(provider, {}, 'saved-model')
    assert result['reachable'] is False and result['error'] == 'The request could not be built.'
    assert provider.requests == []


@pytest.mark.parametrize('model', [None, '', '   '])
async def test_no_saved_model_keeps_its_sentence_and_sends_nothing(model):
    provider = _Provider(_reply([_text('OK')]))
    with pytest.raises(ValueError, match='^Choose and save a model before sending a test message\\.$'):
        await send_test_message(provider, {}, model)
    assert provider.requests == []

