"""Application-owned test profiles; callers never provide a container recipe."""
from __future__ import annotations

from copy import deepcopy

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
