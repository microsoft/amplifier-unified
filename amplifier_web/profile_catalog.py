"""Characteristics of composed bundles, recorded by isolated qualification.

Foundation registration is a lookup mechanism, not a claim that a bundle can
run a conversation. Inspect the composed Bundle, never its filename or URI.
The browser consumes only the small, generation-owned result, without loading
bundles or installing modules during startup or picker rendering.
"""
import json


def characteristics(bundle):
    from amplifier_foundation.validator import BundleValidator
    validator = BundleValidator()
    result = validator.validate_completeness(bundle)
    session = bundle.session or {}
    def module(value):
        return value if isinstance(value, str) else (value or {}).get('module')
    loop = module(session.get('orchestrator'))
    context = module(session.get('context'))
    missing = [name for name, present in
               [('orchestrator', loop), ('context', context), ('providers', bundle.providers)] if not present]
    if not missing:
        validator.validate_or_raise(bundle)
    return {'complete': result.valid and not missing, 'missing': missing,
            'supportedLoop': loop in {'loop-streaming', 'loop-live'},
            'hasLoop': bool(loop)}


def configuration_key(config):
    from .runtime_profiles import configuration_key as runtime_key
    return runtime_key(config.settings, config.home, config.registry_home.parent)


def read_catalog(config, candidates):
    if not hasattr(config, 'registry_home') or not hasattr(config, 'home'):
        return {}
    try:
        value = json.loads((config.registry_home / 'profile-characteristics.json').read_text())
        if isinstance(value, dict) and value.get('configuration') == configuration_key(config) and value.get('candidates') == candidates:
            profiles = value.get('profiles', {})
            if isinstance(profiles, dict) and set(profiles) == set(candidates) and all(
                    isinstance(row, dict) and isinstance(row.get('complete'), bool) for row in profiles.values()):
                return profiles
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_catalog(config, candidates, profiles):
    from .host.config import write_private
    write_private(config.registry_home / 'profile-characteristics.json', json.dumps({
        'configuration': configuration_key(config), 'candidates': candidates, 'profiles': profiles}))
