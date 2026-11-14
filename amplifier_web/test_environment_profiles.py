"""Resource presets and ordinary Compose selection for task-owned tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

_ENVIRONMENT = {
    'PATH': '/opt/unified-prepared/venv/bin:/opt/unified-prepared/node-runtime/bin:/usr/local/bin:/usr/bin:/bin',
    'PYTHONPATH': '/workspace', 'PYTHONDONTWRITEBYTECODE': '1',
    'PLAYWRIGHT_BROWSERS_PATH': '/opt/unified-prepared/browsers',
    'HOME': '/tmp/unified-test/home', 'XDG_CACHE_HOME': '/tmp/unified-test/cache',
    'AMPLIFIER_TEST_ARTIFACTS': '/artifacts',
    'AMPLIFIER_TEST_PYTHON': '/opt/unified-prepared/venv/bin/python',
    'AMPLIFIER_TEST_CONTRACT': '/opt/unified-prepared/execution-contract.json',
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
    'app-preview': {
        'id': 'app-preview', 'label': 'App preview',
        'description': 'Retained Python-capable Compose workspace with a loopback preview port; start the app with its normal command.',
        'preparationRequired': False, 'memoryMiB': 4096, 'cpus': 2, 'pids': 512,
        'tmpMiB': 512, 'shmMiB': 128, 'defaultTimeoutSeconds': 1200,
        'maxTimeoutSeconds': 1800, 'environment': {},
    },
}


def get(profile_id='linux-python'):
    try:
        return deepcopy(_PROFILES[profile_id])
    except (KeyError, TypeError):
        raise ValueError('Choose one of the supported test profiles.') from None


def execution_contract(profile_id, source=None):
    """Presentation/invocation contract, never a substitute for an owner receipt."""
    if not get(profile_id)['preparationRequired']:
        return None
    return {
        'version': 1, 'profileId': profile_id, 'workspace': '/workspace',
        'artifacts': '/artifacts', 'python': '/opt/unified-prepared/venv/bin/python',
        'manifest': '/opt/unified-prepared/execution-contract.json',
        'source': {'gitMetadataAvailable': False, **(source or {})},
        'pythonBuild': {'status': 'pending', 'command': '/opt/unified-prepared/build-venv/bin/python -m build --wheel --no-isolation --outdir /artifacts/dist /workspace'},
        'retention': {'mode': 'archive-only', 'maxBytesPerSelection': 4 * 1024 ** 3},
    }


def catalog():
    return [{**get(identity), **({'executionContract': execution_contract(identity)} if get(identity)['preparationRequired'] else {})} for identity in _PROFILES]


def compose_fields(string):
    return {
        'composeFile': {**string(2000), 'minLength': 1,
                        'description': 'Trusted ordinary Compose YAML/JSON file relative to the selected source. Build contexts come from the copied source, never the writable original.'},
        'offline': {'type': 'boolean', 'description': 'Disable external networking. Default false permits ordinary dependency commands and Docker build cache.'},
        'preserveSource': {'type': 'boolean', 'description': 'Export the full disposable guest source before removal. Default false retains selected /artifacts and the original source snapshot, not a mandatory multi-GB workspace export.'},
    }


def compose_options(args):
    from pathlib import PurePosixPath
    file = args.get('composeFile')
    if file is not None and (not isinstance(file, str) or not file or len(file) > 2000
            or '\\' in file or PurePosixPath(file).is_absolute() or '..' in PurePosixPath(file).parts):
        raise ValueError('Choose a Compose file relative to the selected source.')
    if any(key in args and type(args[key]) is not bool for key in ('offline', 'preserveSource')):
        raise ValueError('Compose network and source retention choices must be boolean.')
    return {'composeFile': file, 'offline': args.get('offline', False), 'preserveSource': args.get('preserveSource', False)}


def validate_timeout(profile_id, seconds, timing_request=None):
    profile = timing(profile_id, timing_request)
    seconds = profile['defaultTimeoutSeconds'] if seconds is None else seconds
    if type(seconds) is not int or not 1 <= seconds <= profile['maxTimeoutSeconds']:
        raise ValueError('The check duration exceeds this test profile’s supported limit.')
    return seconds


RESOURCE_FIELDS = ('cpus', 'memoryMiB', 'pids', 'tmpMiB', 'shmMiB')


def freeze(profile_id, overrides=None):
    profile = {**get(profile_id), **(overrides or {})}
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


# Serialization bounds, not the operator's workload budget.
MAX_SECONDS = 2147483647
TIMING_FIELDS = ('defaultTimeoutSeconds', 'maxTimeoutSeconds', 'preparationTimeoutSeconds')


def freeze_timing(profile_id, overrides=None):
    profile = {**get(profile_id), 'preparationTimeoutSeconds': 1800, **(overrides or {})}
    value = {'profileId': profile_id, **{key: profile[key] for key in TIMING_FIELDS}}
    request = {**value, 'sha256': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}
    timing(profile_id, request)
    return request


def timing(profile_id, request=None):
    profile = {**get(profile_id), 'preparationTimeoutSeconds': 1800}
    if request is None:  # Historical work keeps the pre-settings defaults.
        return profile
    if (not isinstance(request, dict) or set(request) != {'profileId', 'sha256', *TIMING_FIELDS}
            or request['profileId'] != profile_id
            or any(type(request[key]) is not int or not 1 <= request[key] <= MAX_SECONDS for key in TIMING_FIELDS)
            or request['defaultTimeoutSeconds'] > request['maxTimeoutSeconds']
            or request['sha256'] != hashlib.sha256(json.dumps({key: value for key, value in request.items() if key != 'sha256'}, sort_keys=True).encode()).hexdigest()):
        raise ValueError('The admitted test timing policy changed.')
    return {**profile, **{key: request[key] for key in TIMING_FIELDS}}
