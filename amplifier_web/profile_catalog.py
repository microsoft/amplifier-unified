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


def incomplete(facts):
    """The refusal of an unusable selected profile, with its reason for update diagnostics."""
    error = ValueError('The selected conversation profile is incomplete: ' + ', '.join(facts['missing']))
    # A profile that lacks only a provider is waiting for the person's AI
    # connection. Any other gap is a fault of the selected bundle.
    error.diagnostic_facts = {'reason': 'ai-connection-needed'
                              if facts['missing'] == ['providers'] and facts['supportedLoop']
                              else 'incomplete-conversation-profile'}
    return error


def connection(config):
    """Whether a component update must wait for an AI connection, without composing.

    A component update qualifies the selected profile, and `characteristics`
    calls it incomplete while it has no provider. The app's own profiles with
    the app's own behaviors declare none: their providers are the person's
    connections, which composition merges, then drops by `provider_dropped`.
    There this answers the probe's question from the same settings by the same
    steps.
    Any other selection may declare a provider of its own, and only its
    composition can tell: `needed` stays false and the update's probe decides.
    """
    from .builtin_behaviors import DEFAULT_BEHAVIORS
    from .host.config import PRECONFIGURED_BUNDLES, merge, provider_dropped
    name = config.active_bundle
    own = (name in PRECONFIGURED_BUNDLES and config.registrations.get(name) == PRECONFIGURED_BUNDLES[name]
           and not config.bundle_sources
           and all(uri in DEFAULT_BEHAVIORS for uri in config.app_bundles))
    # Composition merges rows of one identity before it drops any.
    connected = any(not provider_dropped(row, config.settings) for row in merge([], config.providers))
    return {'appProfile': own, 'needed': own and not connected}


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
