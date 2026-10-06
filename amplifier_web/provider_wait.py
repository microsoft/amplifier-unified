"""Payload-free provider observations; shared validation at both public seams."""
import math


def _number(value, *, positive=False):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and (value > 0 if positive else value >= 0))
    except OverflowError:
        return False


def _metadata(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        return None
    attempt = value.get('attempt')
    if type(attempt) is not int or not _number(attempt, positive=True):
        return None
    limits = value.get('limits')
    if not isinstance(limits, dict) or limits.get('mode') not in ('none', 'elapsed', 'phase'):
        return None
    safe = {'mode': limits['mode']}
    for field in ('elapsed_seconds', 'connect_seconds', 'pool_seconds', 'read_seconds', 'write_seconds'):
        if field not in limits:
            return None
        number = limits[field]
        if number is not None and not _number(number, positive=field == 'elapsed_seconds'):
            return None
        safe[field] = number
    if (safe['mode'] == 'elapsed') != (safe['elapsed_seconds'] is not None):
        return None
    return {'version': 1, 'attempt': attempt, 'limits': safe}


def observation(value):
    safe = _metadata(value)
    if safe is None or value.get('observation') not in ('attempt_started', 'response_activity'):
        return None
    return {**safe, 'observation': value['observation']}


def public_wait(value):
    """Validate nested persisted/transport metadata, never spread plugin fields."""
    safe = _metadata(value)
    if safe is None or not _number(value.get('observedAt')):
        return None
    safe['observedAt'] = value['observedAt']
    if 'lastResponseActivityAt' in value:
        if not _number(value['lastResponseActivityAt']):
            return None
        safe['lastResponseActivityAt'] = value['lastResponseActivityAt']
    return safe