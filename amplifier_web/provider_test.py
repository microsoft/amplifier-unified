"""A bounded, explicit inference check without a conversation or tools."""
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


async def test_message(provider, config, model):
    from amplifier_core.message_models import ChatRequest, Message
    model = model or config.get('default_model') or config.get('model')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Choose and save a model before sending a test message.')
    started = time.monotonic()
    result = {'method': 'provider.complete', 'model': safe_text(model, config), 'reachable': False}
    try:
        request = ChatRequest(model=model, messages=[Message(role='user', content='Reply with only OK.')],
                              tools=[], stream=False, max_output_tokens=256, timeout=45)
        reply = await asyncio.wait_for(provider.complete(request), 45)
        text = ''.join(getattr(block, 'text', '') for block in reply.content if getattr(block, 'type', None) == 'text')
        if not text.strip():
            raise ValueError('The provider returned no text. Check model compatibility or its output limit.')
        result.update(reachable=True, response=safe_text(text, config), model=safe_text(getattr(reply, 'model', None) or model, config))
    except Exception as exc:
        result.update(error=safe_text('The provider did not respond within 45 seconds.' if isinstance(exc, TimeoutError) else exc, config), errorType=type(exc).__name__)
        status = getattr(exc, 'status_code', None)
        if type(status) is int: result['statusCode'] = status
    result['elapsedMs'] = round((time.monotonic() - started) * 1000)
    return result
