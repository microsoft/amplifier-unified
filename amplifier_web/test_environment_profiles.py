"""Application-owned test profiles; callers never provide a container recipe."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

_ENVIRONMENT = {
    'PATH': '/opt/unified-prepared/venv/bin:/opt/unified-prepared/node-runtime/bin:/usr/local/bin:/usr/bin:/bin',
    'PYTHONPATH': '/workspace', 'PYTHONDONTWRITEBYTECODE': '1',
    'PLAYWRIGHT_BROWSERS_PATH': '/opt/unified-prepared/browsers',
    'HOME': '/artifacts/home', 'XDG_CACHE_HOME': '/artifacts/cache',
    'UV_OFFLINE': '1', 'PIP_NO_INDEX': '1', 'npm_config_offline': 'true',
}
_PROFILES = {
    'linux-python': {
        'id': 'linux-python', 'label': 'Small Python check',
        'description': 'Python standard library checks without project dependency preparation.',
        'preparationRequired': False, 'memoryMiB': 1024, 'cpus': 2, 'pids': 128,
        'tmpMiB': 64, 'shmMiB': 64, 'defaultTimeoutSeconds': 300,
        'maxTimeoutSeconds': 300, 'environment': {},
    },
    'python-project': {
        'id': 'python-project', 'label': 'Python project',
        'description': 'Project and development dependencies prepared from pyproject.toml and uv.lock.',
        'preparationRequired': True, 'memoryMiB': 4096, 'cpus': 2, 'pids': 512,
        'tmpMiB': 512, 'shmMiB': 128, 'defaultTimeoutSeconds': 1200,
        'maxTimeoutSeconds': 1800, 'environment': _ENVIRONMENT,
    },
    'browser-test': {
        'id': 'browser-test', 'label': 'Browser and frontend checks',
        'description': 'Prepared Python, Node, locked project dependencies, Chromium and a virtual display.',
        'preparationRequired': True, 'memoryMiB': 6144, 'cpus': 4, 'pids': 1024,
        'tmpMiB': 1024, 'shmMiB': 1024, 'defaultTimeoutSeconds': 1200,
        'maxTimeoutSeconds': 1800, 'environment': _ENVIRONMENT,
    },
}


def get(profile_id='linux-python'):
    try:
        return deepcopy(_PROFILES[profile_id])
    except (KeyError, TypeError):
        raise ValueError('Choose one of the supported test profiles.') from None


def catalog():
    return [get(identity) for identity in _PROFILES]


def validate_timeout(profile_id, seconds):
    profile = get(profile_id)
    seconds = profile['defaultTimeoutSeconds'] if seconds is None else seconds
    if type(seconds) is not int or not 1 <= seconds <= profile['maxTimeoutSeconds']:
        raise ValueError('The check duration exceeds this test profile’s supported limit.')
    return seconds


RESOURCE_FIELDS = ('cpus', 'memoryMiB', 'pids', 'tmpMiB', 'shmMiB')

def freeze(profile_id):
    profile = get(profile_id)
    value = {'profileId': profile_id, **{key: profile[key] for key in RESOURCE_FIELDS}}
    return {**value, 'sha256': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}

def resources(profile_id, request=None):
    profile = get(profile_id)
    if request is None:  # Historical receipts keep their existing profile behavior.
        return profile
    if (not isinstance(request, dict) or set(request) != {'profileId', 'sha256', *RESOURCE_FIELDS}
            or request['profileId'] != profile_id
            or any(type(request[key]) is not int or not 1 <= request[key] <= 1048576 for key in RESOURCE_FIELDS)
            or request['sha256'] != hashlib.sha256(json.dumps({key: value for key, value in request.items() if key != 'sha256'}, sort_keys=True).encode()).hexdigest()):
        raise ValueError('The admitted test resource request changed.')
    return {**profile, **{key: request[key] for key in RESOURCE_FIELDS}}
