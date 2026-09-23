"""Internal, versioned readiness bounds; never a caller-supplied run budget."""
import hashlib
import json
import math
import re

CAPABILITY = 'completion:single_attempt:v2'
LEGACY_CAPABILITY = 'completion:single_attempt:v1'
MAX_OUTPUT_TOKENS = 1024
LEGACY_REQUEST_TIMEOUT_SECONDS = 45
PROMPT = 'Reply with OK.'


def policy_digest(value):
    """Match the host protocol's JSON bytes without importing host packages."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _policy(module, instance, model, effort, *, version, timeout_seconds):
    for value, maximum in ((module, 128), (instance, 200), (model, 200), (effort, 50)):
        if (not isinstance(value, str) or not 1 <= len(value) <= maximum
                or any(ord(char) < 33 for char in value)):
            raise ValueError('Readiness requires an explicit provider, model and effective reasoning effort')
    if timeout_seconds is not None:
        try:
            valid = type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds) and timeout_seconds > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('An explicit readiness deadline must be finite and positive')
    return {'version': version, 'capability': LEGACY_CAPABILITY if version == 1 else CAPABILITY, 'providerModule': module,
            'providerInstance': instance, 'model': model, 'reasoningEffort': effort,
            'maxOutputTokens': MAX_OUTPUT_TOKENS, 'maxCountRequests': 1,
            'maxGenerationRequests': 1, 'retries': 0, 'continuations': 0,
            'timeoutSeconds': timeout_seconds, 'prompt': PROMPT, 'tools': False}


def readiness_policy(module, instance, model, effort, *, timeout_seconds=None):
    """New admission has no elapsed deadline; experiments may explicitly bind one."""
    return _policy(module, instance, model, effort, version=2, timeout_seconds=timeout_seconds)


def validate_policy(policy, expected_hash=None):
    try:
        version = policy['version']
        if type(version) is not int or version not in (1, 2):
            raise ValueError()
        timeout = LEGACY_REQUEST_TIMEOUT_SECONDS if version == 1 else policy['timeoutSeconds']
        expected = _policy(policy['providerModule'], policy['providerInstance'],
                           policy['model'], policy['reasoningEffort'], version=version, timeout_seconds=timeout)
        # Hash comparison also distinguishes bools from ints, unlike dict equality.
        if policy_digest(policy) != policy_digest(expected) or (expected_hash is not None and expected_hash != policy_digest(policy)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Unsupported or changed readiness policy; existing attempts cannot be upgraded') from None
    return expected


def effective_effort(selection, config):
    if selection.get('effort') is not None:
        return selection['effort']
    if config.get('reasoning_effort') is not None:
        return config['reasoning_effort']
    reasoning = config.get('reasoning')
    return reasoning.get('effort') if isinstance(reasoning, dict) else None


def completion_receipt(value, policy):
    """Require closed evidence for the exact historical or new admitted policy."""
    expected = {'version': policy['version'], 'model': policy['model'], 'reasoning_effort': policy['reasoningEffort'],
                'max_output_tokens': policy['maxOutputTokens'], 'timeout_seconds': policy['timeoutSeconds'],
                'native_count_requests': 1, 'generation_requests': 1, 'retries': 0, 'continuations': 0, 'closed': True}
    if not isinstance(value, dict) or set(value) != set(expected) | {'native_input_tokens', 'input_sha256', 'request_sha256'}:
        raise ValueError('Missing bounded completion receipt')
    for key, expected_value in expected.items():
        if key == 'timeout_seconds':
            valid_type = value[key] is None if expected_value is None else type(value[key]) in (int, float)
        else:
            valid_type = type(value[key]) is type(expected_value)
        if not valid_type or value[key] != expected_value:
            raise ValueError('Completion receipt differs from admitted readiness policy')
    if type(value['native_input_tokens']) is not int or value['native_input_tokens'] < 0:
        raise ValueError('Invalid native token-count receipt')
    for key in ('input_sha256', 'request_sha256'):
        if not isinstance(value[key], str) or not re.fullmatch('[a-f0-9]{64}', value[key]):
            raise ValueError('Invalid completion request digest')
    expected_input = [{'role': 'user', 'content': [{'type': 'input_text', 'text': PROMPT}]}]
    input_hash = hashlib.sha256(json.dumps(expected_input, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if value['input_sha256'] != input_hash:
        raise ValueError('Completion input differs from the fixed readiness prompt')
    return dict(value)
