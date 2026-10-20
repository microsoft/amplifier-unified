"""Fixed backend dispatch for the managed runner update protocol."""
import importlib


def backend(kind):
    modules = {'managed-dtu-core': 'managed_runner_updates',
               'managed-incus-engine': 'managed_incus_updates'}
    if kind not in modules:
        raise ValueError('Unsupported managed runner update.')
    return importlib.import_module('.' + modules[kind], __package__)
