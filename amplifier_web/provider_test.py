"""A bounded, explicit inference check without a conversation.

The test message carries one tool definition, because a chat whose bundle has
tools sends them and a service or a model can refuse a request for that reason.
The tool is never run.
"""
import asyncio
import os
import re
import time
from urllib.parse import quote


def safe_text(value, config):
    secrets = []
    def collect(node, sensitive=False):
        if isinstance(node, dict):
            for key, child in node.items():
                collect(child, sensitive or any(word in key.lower() for word in ('key', 'token', 'secret', 'password')))
        elif isinstance(node, (list, tuple)):
            for child in node: collect(child, sensitive)
        elif sensitive and isinstance(node, str) and node: secrets.append(node)
    collect(config)
    collect(dict(os.environ))
    text = str(value)
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]').replace(quote(secret, safe=''), '[REDACTED]')
    text = re.sub(r'https?://\S+', '[endpoint]', text)
    text = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|secret|password)[\s\"\x27:=]+)[^\s,}\"\x27]+', r'\1[REDACTED]', text)
    text = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    return ''.join(c for c in text if c.isprintable() or c in '\n\t')[:1000]


# What a failure names once the request exists. It says what the request held,
# not that it was sent or who answered.
REQUEST_HELD = 'The test request held one short message and one tool definition. The error: '


def _failure(error, config, built):
    text = safe_text(error, config)
    return (REQUEST_HELD + text)[:1000] if built else text


async def test_message(provider, config, model, info=None):
    from amplifier_core.message_models import ChatRequest, Message, ToolSpec
    model = model or config.get('default_model') or config.get('model')
    mode = ((info or {}).get('defaults') or {}).get('auth_mode') or config.get('auth_mode')
    if mode == 'chatgpt_plan':
        # This explicit test promises an output cap. Do not silently remove
        # that spending constraint to accommodate a different transport.
        return {'method': 'provider.complete', 'model': safe_text(model or '', config),
                'reachable': False, 'supported': False, 'elapsedMs': 0,
                'errorType': 'UnsupportedMessageTest',
                'error': 'ChatGPT plan does not support this test\'s output limit. Use Test connection to check the model catalog; no test message was sent.'}
    if not callable(getattr(provider, 'complete', None)):
        # The object that the helper built for this connection answers no
        # request (a component can make its answering provider only when a
        # chat mounts it). That is no failure of the service or of the key,
        # and a saved model would not change it.
        return {'method': 'provider.complete', 'model': safe_text(model or '', config),
                'reachable': False, 'supported': False, 'elapsedMs': 0,
                'errorType': 'UnsupportedMessageTest',
                'error': 'This connection cannot send a test message here. No test message was sent. To try the connection, start a chat with it.'}
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Choose and save a model before sending a test message.')
    started = time.monotonic()
    result = {'method': 'provider.complete', 'model': safe_text(model, config), 'reachable': False}
    built = False
    try:
        # One optional property: a service may refuse a tool with no property.
        tool = ToolSpec(name='connection_test', description='Not used. Reply with text.',
                        parameters={'type': 'object', 'properties': {'note': {'type': 'string'}}})
        request = ChatRequest(model=model, messages=[Message(role='user', content='Reply with only OK.')],
                              tools=[tool], stream=False, max_output_tokens=256, timeout=45)
        built = True
        reply = await asyncio.wait_for(provider.complete(request), 45)
        blocks = getattr(reply, 'content', None) or []
        text = ''.join(getattr(block, 'text', '') for block in blocks if getattr(block, 'type', None) == 'text')
        # Core has a tool call in either place. The call is never run.
        called = (any(getattr(block, 'type', None) == 'tool_call' for block in blocks)
                  or bool(getattr(reply, 'tool_calls', None)))
        if not text.strip() and not called:
            raise ValueError('The provider returned no text and no tool call. Check model compatibility or its output limit.')
        result.update(reachable=True, model=safe_text(getattr(reply, 'model', None) or model, config),
                      response=safe_text(text, config) if text.strip() else 'The model answered with a tool call.')
    except Exception as exc:
        error = 'The provider did not respond within 45 seconds.' if isinstance(exc, TimeoutError) else exc
        result.update(error=_failure(error, config, built), errorType=type(exc).__name__)
        status = getattr(exc, 'status_code', None)
        if type(status) is int: result['statusCode'] = status
    result['elapsedMs'] = round((time.monotonic() - started) * 1000)
    return result


async def naming_completion(provider, config, model, prompt):
    """One bounded naming call: no tools, no streaming, no extended thinking.

    Mirrors hooks-session-naming's own request so a backfilled name is made
    the same way as a live one. Returns {'text'} or a redacted {'error'}.
    """
    from amplifier_core.message_models import ChatRequest, Message
    model = model or config.get('default_model') or config.get('model') or None
    try:
        request = ChatRequest(model=model, messages=[Message(role='user', content=prompt)],
                              metadata={'stream': False}, max_output_tokens=256)
        reply = await asyncio.wait_for(provider.complete(request, extended_thinking=False), 60)
        text = ''.join(getattr(block, 'text', '') for block in getattr(reply, 'content', None) or []
                       if isinstance(getattr(block, 'text', None), str))
        if not text.strip():
            return {'error': 'The naming model returned no text.'}
        return {'text': text}
    except Exception as exc:
        return {'error': safe_text('The naming model did not respond within 60 seconds.'
                                   if isinstance(exc, TimeoutError) else exc, config)}
