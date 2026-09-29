"""Opt-in provider event capture, separate from request construction and routing.

Providers own the emitted SDK/wire payload. This host never reconstructs one
from ChatRequest, and never edits the request that goes to a provider.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RAW_MODULES = frozenset('provider-' + name for name in (
    'anthropic', 'openai', 'openai-chatgpt', 'azure-openai',
    'chat-completions', 'gemini', 'github-copilot', 'ollama', 'vllm',
))
MAX_CAPTURE_CHARS = 1_000_000
_DEFAULT_MARKER = '_unified_request_recording_default'
_SECRET_KEY = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|access.?token|refresh.?token|id.?token)')


def recording_enabled(home=None):
    if home is None:
        from .host.config import app_home
        home = app_home()
    try:
        value = json.loads((Path(home) / 'diagnostics' / 'config.json').read_text())
        return isinstance(value, dict) and value.get('providerRequests') is True
    except (OSError, ValueError):
        return False


def apply_provider_recording(plan, *, enabled=None, home=None):
    """Apply defaults to every declared instance, retaining explicit opt-outs.

    Do not guess unknown adapters' config contracts. Mock has no wire request;
    LiteLLM uses raw_debug. The other catalog adapters implement raw. Custom
    adapters can opt in through their own documented config without this default.
    """
    enabled = recording_enabled(home) if enabled is None else enabled
    from .provider_environment import iter_provider_rows
    for row in iter_provider_rows(plan):
        # Snapshots and retained children can contain an earlier host default.
        # Keep its provenance outside provider config so switching the app
        # setting off does not turn that default into a permanent opt-in.
        previous = row.pop(_DEFAULT_MARKER, None)
        config = row.get('config', {})
        if previous in ('raw', 'raw_debug') and config.get(previous) is True:
            config.pop(previous)
        if not enabled:
            continue
        if row.get('enabled') is False:
            continue
        module = row['module']
        key = 'raw' if module in RAW_MODULES else 'raw_debug' if module == 'provider-litellm' else None
        if key and key not in row.setdefault('config', {}):
            row['config'][key] = True
            row[_DEFAULT_MARKER] = key
    return plan


def redact_request(value, *, limit=MAX_CAPTURE_CHARS):
    """Bound stored content and scrub recognizable secrets before CI sees it."""
    from .worker_diagnostics import redact_diagnostic
    remaining, truncated = limit, False
    def visit(item, depth=0):
        nonlocal remaining, truncated
        if remaining <= 0 or depth > 24:
            truncated = True
            return '[capture limit]'
        remaining -= 16
        if isinstance(item, str):
            # Scrub a bounded window, including a small lookahead for a token
            # spanning the cutoff. Never copy an unbounded inline image/audio.
            window = item[:max(0, remaining) + 512]
            safe = redact_diagnostic(window)
            if len(item) > remaining or len(safe) > remaining:
                truncated = True
                safe = safe[:max(0, remaining)] + '[capture limit]'
            remaining -= len(safe)
            return safe
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if remaining <= 0:
                    truncated = True
                    result['__capture_limit__'] = True
                    break
                name = redact_diagnostic(str(key)[:200])
                remaining -= len(name)
                result[name] = '[REDACTED]' if _SECRET_KEY.search(name) else visit(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for child in item:
                if remaining <= 0:
                    truncated = True
                    result.append('[capture limit]')
                    break
                result.append(visit(child, depth + 1))
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return '[unsupported capture value]'
    result = visit(value)
    return result, truncated


def install_request_redaction(coordinator):
    """Sanitize diagnostic copies ahead of CI's priority-100 logging hook."""
    from amplifier_core import HookResult
    if coordinator.get_capability('web.provider_request_redaction'):
        return
    coordinator.register_capability('web.provider_request_redaction', True)
    async def observe(event, data):
        fields = [key for key in ('raw', 'raw_request', 'raw_response') if key in data]
        if not fields:
            return HookResult()
        result = dict(data)
        truncated = False
        for key in fields:
            try:
                result[key], limited = redact_request(data[key])
            except Exception:
                # A diagnostic failure must never pass the unsanitized copy
                # to later loggers or interrupt the actual provider call.
                result[key], limited = '[capture unavailable]', True
            truncated = truncated or limited
        result['request_capture'] = {'redacted': True, 'truncated': truncated,
                                     'limit_chars': MAX_CAPTURE_CHARS}
        return HookResult(action='modify', data=result)
    for event in ('llm:request', 'llm:response'):
        coordinator.hooks.register(event, observe,
            name='unified-provider-request-redaction', priority=-100)
