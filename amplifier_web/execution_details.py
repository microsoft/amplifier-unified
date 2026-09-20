"""Bounded public tool details; provider requests and reasoning never enter here."""
import json
import os
import re

SECRET_KEY = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|(?:access|refresh|auth).?token|(?:^|_)(?:key|token)$)')
PRIVATE_KEYS = {'thinking', 'reasoning', 'analysis', 'chain_of_thought', 'reasoning_content', 'reasoning_text', 'thinking_signature', 'system_prompt', 'system_instructions', 'raw', 'provider_request', 'provider_response'}
DETAIL_LIMIT = 64000


def tool_detail(value):
    """Inspect tool inputs/results only, redacting known credentials and private blocks.

    This is a local history projection, not a diagnostics content opt-in. Unknown
    objects are never stringified, and truncation is explicit rather than silent.
    """
    secrets = [v for k, v in os.environ.items() if SECRET_KEY.search(k) and len(v) >= 8]
    remaining = 2000

    def clean(item, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 10:
            return '[detail limit]'
        if hasattr(item, 'model_dump'):
            item = item.model_dump()
        if isinstance(item, dict):
            if item.get('type') in ('thinking', 'reasoning', 'analysis', 'redacted_thinking') or item.get('visibility') in ('private', 'hidden'):
                return '[private content omitted]'
            result = {}
            for key, val in list(item.items())[:100]:
                key = str(key)[:100]
                normalized = key.lower().replace('-', '_')
                result[key] = '[private content omitted]' if normalized in PRIVATE_KEYS else '[REDACTED]' if SECRET_KEY.search(key) else clean(val, depth + 1)
            if len(item) > 100: result['…'] = '[remaining fields omitted]'
            return result
        if isinstance(item, (list, tuple)):
            return [clean(val, depth + 1) for val in item[:100]] + (['[remaining items omitted]'] if len(item) > 100 else [])
        if isinstance(item, str):
            for secret in secrets: item = item.replace(secret, '[REDACTED]')
            item = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', item)
            item = re.sub(r'(?i)(https?://)[^\s/@]+@', r'\1[REDACTED]@', item)
            item = re.sub(r'(?i)(?<![\w-])((?:[\w-]{0,80}(?:api[_-]?key|password|secret|[_-]token|[_-]key)|token|authorization)["\']?\s*[=:]\s*["\']?)[^\s,;"\']+', r'\1[REDACTED]', item)
            item = re.sub(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', item, flags=re.S)
            return item[:DETAIL_LIMIT] + ('\n[remaining text omitted]' if len(item) > DETAIL_LIMIT else '')
        if isinstance(item, (int, float, bool)) or item is None:
            return item
        return '[unsupported detail]'

    public = clean(value)
    text = public if isinstance(public, str) else json.dumps(public, ensure_ascii=False, indent=2, default=lambda _: '[unsupported detail]')
    return text[:DETAIL_LIMIT] + ('\n[remaining detail omitted]' if len(text) > DETAIL_LIMIT else '')
