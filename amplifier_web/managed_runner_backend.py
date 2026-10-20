"""Fixed backend dispatch for the managed runner update protocol."""
import hashlib
import json
import importlib


def backend(kind):
    modules = {'managed-dtu-core': 'managed_runner_updates',
               'managed-incus-engine': 'managed_incus_updates'}
    if kind not in modules:
        raise ValueError('Unsupported managed runner update.')
    return importlib.import_module('.' + modules[kind], __package__)


def skip_key(source):
    """A revision skip belongs to the exact source/selection, not its UI attempt."""
    if not source.get('runnerKind'):
        return source.get('installationId')
    binding = {key: source.get(key) for key in (
        'runnerKind', 'id', 'runnerSelection', 'runnerPolicy', 'url', 'ref')}
    return 'runner:' + hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
