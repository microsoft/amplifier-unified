"""Passive estimates for the next-message settings, based on actual foreground usage."""
from .token_usage import with_gross_tokens

# A presentation threshold only. Runtime fit and compaction use the provider's budget.
LARGE_INPUT = 32_768
MODEL_SCOPE = 'prompt-cache:model-scoped'
TOOLS_PREFIX = 'prompt-cache:top-level-tools-prefix'


def cache_traits(info):
    caps = info.get('capabilities') if isinstance(info, dict) else getattr(info, 'capabilities', ())
    return [flag for flag in (MODEL_SCOPE, TOOLS_PREFIX) if flag in (caps or ())]


def capture_basis(coordinator, provider, info, model):
    """Bind the measurement to the actual mounted provider while the call is in scope."""
    mounted = coordinator.get('providers') or {}
    aliases = [key for key, value in mounted.items()
               if value is provider or getattr(value, 'original', None) is provider]
    if len(aliases) != 1:
        return {}
    instance = aliases[0]
    basis = {'instance': instance, 'traits': cache_traits(info)}
    continuity = coordinator.get_capability('web.continuity.recovery')
    measure = getattr(continuity, 'native_history_input', None)
    if callable(measure):
        covered = measure(instance, model)
        if covered is not None:
            basis['portableHistoryTokens'] = covered
    return basis


def observation(receipt):
    """Keep only the fields needed to reassess the latest completed foreground call."""
    if not isinstance(receipt, dict):
        return {}
    usage = receipt.get('usage')
    basis = receipt.get('promptCostBasis') or {}
    if not isinstance(usage, dict) or not isinstance(basis, dict):
        return {}
    tokens = with_gross_tokens(usage).get('grossInputTokens')
    model, family, instance = receipt.get('model'), receipt.get('provider'), basis.get('instance')
    if type(tokens) is not int or not all(isinstance(v, str) and 0 < len(v) <= 160 for v in (model, family)):
        return {}
    result = {'model': model, 'provider': family, 'inputTokens': tokens}
    window = basis.get('modelWindow') or (receipt.get('limits') or {}).get('modelWindow')
    if type(window) is int and window > 0:
        result['modelWindow'] = window
    if isinstance(instance, str) and 0 < len(instance) <= 160:
        result['instance'] = instance
    traits = basis.get('traits')
    if isinstance(traits, (tuple, list)):
        result['traits'] = [flag for flag in (MODEL_SCOPE, TOOLS_PREFIX) if flag in traits]
    covered = basis.get('portableHistoryTokens')
    if type(covered) is int and covered >= LARGE_INPUT:
        result['portableHistoryTokens'] = covered
    return result


def prior_request(home, session):
    """For older sessions, read only the bounded index of root foreground requests."""
    from .context_inspection_store import InspectionStore, preferences
    from .session_files import capture_dir
    if not session.get('workspace') or session.get('externalAgent'):
        return None
    identity = session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
    try:
        policy = preferences(home)
        if policy['enabled']:
            index = InspectionStore(capture_dir(session['workspace'], identity)).index(days=policy['days'])
            return next((r for r in index if r.get('sessionId') == identity and r.get('phase') == 'completed'), None)
    except (AttributeError, OSError, ValueError, KeyError, TypeError):
        pass
    return None


def impact(previous, choices, mounted=()):
    if not previous or not previous.get('model'):
        return None
    tokens = previous['inputTokens']
    covered = previous.get('portableHistoryTokens')
    if tokens < LARGE_INPUT and not covered:
        return None
    # A mounted capability can qualify a catalog entry without fetching models.
    # A changed connection has a different existing credential/config/source key.
    mounted_traits = {r['sharedCatalogKey']: r.get('promptCacheTraits', ()) for r in mounted
                      if r.get('sharedCatalogKey') and r.get('available') is not False}
    families, qualified = {}, {}
    for row in choices:
        if not row.get('id') or row.get('available') is False or row.get('authenticationRequired'):
            continue
        info = row.get('info') or {}
        family = row.get('provider') or info.get('id') or (row.get('module') or '').removeprefix('provider-')
        families[row['id']] = family
        traits = row.get('promptCacheTraits', mounted_traits.get(row.get('sharedCatalogKey'), ()))
        qualified[row['id']] = cache_traits(info) if 'capabilities' in info else traits
    family = previous['provider']
    source = previous.get('instance')
    if not source:
        aliases = [name for name, owner in families.items() if owner == family]
        if len(aliases) != 1:
            return None
        source = aliases[0]
    if families.get(source) != family:
        return None
    result = {'source': {'instance': source, 'model': previous['model']}, 'inputTokens': tokens}
    prior = previous.get('traits', ())
    source_traits = qualified[source]
    if tokens >= LARGE_INPUT and MODEL_SCOPE in prior and MODEL_SCOPE in source_traits:
        result['coldModels'] = True
    if tokens >= LARGE_INPUT and TOOLS_PREFIX in prior and TOOLS_PREFIX in source_traits:
        result['toolChangesInvalidatePrefix'] = True
    other_family = {'openai': 'anthropic', 'anthropic': 'openai'}.get(family)
    if other_family and MODEL_SCOPE in prior:
        others = sorted(name for name, owner in families.items()
                        if name != source and owner == other_family and MODEL_SCOPE in qualified[name])
        if others:
            result['coldConnections'] = others
            if covered:
                result['portableHistoryTokens'] = covered
    return result if tokens >= LARGE_INPUT or 'portableHistoryTokens' in result else None


def context_summary(usage):
    if not usage or 'inputTokens' not in usage:
        return None
    return {k: usage[k] for k in ('model', 'instance', 'inputTokens', 'modelWindow') if k in usage}
