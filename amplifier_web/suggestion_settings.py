"""App-global, opt-in utility model selection; never a conversation setting."""
import hashlib
import json
import uuid

from .suggestion_provider import INSTRUCTION, OUTPUT_CONTRACT, MAX_PROMPT_BYTES, validate_options


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _values(manager, workspace):
    effective = manager.config(workspace)
    saved = effective.settings.get('prompt_suggestions') or {}
    value = {key: saved.get(key, default) for key, default in {
        'enabled': False, 'provider': None, 'model': None,
        'effort': None, 'prompt': INSTRUCTION}.items()}
    value['enabled'] = value['enabled'] is True and saved.get('shareRecentConversation') is True
    value.update(revision=digest(['plain-transcript-v1', saved, value, INSTRUCTION, OUTPUT_CONTRACT]),
                 defaultPrompt=INSTRUCTION, takesEffect='next_request')
    return value


def selected(manager, workspace):
    value = _values(manager, workspace)
    rows = manager.provider_rows(workspace, ordinary_only=True)
    row = next((row for row in rows if row['id'] == value['provider'] and row['enabled']), None)
    # Include the actual connection implementation/config/credential witness.
    catalog_key = manager.catalog_key({'id': row['id']}, workspace) if row else None
    if row:
        row = {**row, 'sharedCatalogKey': catalog_key}
    revision = digest([value['revision'], catalog_key])
    value['revision'] = revision
    return value, row, revision


def describe(manager, workspace):
    return selected(manager, workspace)[0]


def input_budget(manager, row, model, prompt):
    from .provider_catalog import model_key
    from .suggestion_provider import transcript_budget
    limits = manager.catalog.suggestion_model_limits(
        model_key(row['sharedCatalogKey']), row['module'], model)
    return transcript_budget(prompt, *limits)


def recommendation(manager, row):
    """Project module-owned purpose ranking from this exact cached catalog.

    This optional model-list metadata extension is not a host vendor policy or
    an availability/strict-output attestation. Reading Settings never discovers
    models, and a saved manual choice is never rewritten by a recommendation.
    """
    from .provider_catalog import model_key
    if not row.get('enabled') or row.get('authenticationRequired'):
        return None
    return manager.catalog.purpose_recommendation(
        model_key(row['sharedCatalogKey']), row['module'], 'prompt-suggestions')


def configure(manager, args, workspace, scope):
    if scope != 'global' or not manager.global_only:
        raise ValueError('Prompt suggestions use app-global Settings.')
    snapshot = getattr(manager, '_config_snapshot', None)
    def mutate(settings):
        # SettingsStore/ Foundation holds the ordinary global per-file writer
        # lock here, including against other processes editing provider settings.
        # Never compare against a caller's earlier inventory read_snapshot.
        manager._config_snapshot = None
        try:
            with manager.read_snapshot(workspace):
                if ('expectedSuggestionModelsRevision' in args
                        and args['expectedSuggestionModelsRevision'] != describe(manager, workspace)['revision']):
                    from .errors import AppError
                    raise AppError('Suggestion settings changed. Review the current settings before saving.',
                                   409, code='suggestion_configuration_changed')
                previous = _values(manager, workspace)
                saved = manager.config(workspace).settings.get('prompt_suggestions') or {}
                consent = saved.get('shareRecentConversation') is True
                value = {key: args.get(key, previous[key]) for key in (
                    'enabled', 'provider', 'model', 'effort', 'prompt')}
                validate_options(value['effort'], value['prompt'])
                if value['enabled'] and not consent and args.get('shareRecentConversation') is not True:
                    raise ValueError('Review and confirm recent conversation sharing before enabling suggestions.')
                value['shareRecentConversation'] = consent or (
                    args.get('enabled') is True and args.get('shareRecentConversation') is True)
                if value['enabled']:
                    if not any(row['id'] == value['provider'] and row['enabled']
                               for row in manager.provider_rows(workspace, ordinary_only=True)):
                        raise ValueError('Choose an enabled saved chat connection for prompt suggestions.')
                    if not isinstance(value['model'], str) or not value['model'].strip() or len(value['model']) > 200:
                        raise ValueError('Choose an explicit suggestion model.')
                    if value['effort'] is not None:
                        # Cached capability validation only, never discovery.
                        from .provider_catalog import model_key
                        from .next_message_configuration import validate_selection
                        entry = manager.catalog.peek(model_key(manager.catalog_key({'id': value['provider']}, workspace))) or {}
                        validate_selection({'instance': value['provider'], 'model': value['model'], 'effort': value['effort']}, {
                            'providers': [{'id': value['provider'], 'sharedCatalogKey': 'selected'}],
                            'catalogs': {'selected': {'metadata': entry.get('providerMetadata')}}})
                value['_revision'] = uuid.uuid4().hex  # A→B→A is not the original selection.
                settings.update(prompt_suggestions=value)
        finally:
            manager._config_snapshot = None
    try:
        manager.store.update(workspace, scope, mutate)
        return {'suggestionModels': describe(manager, workspace),
                'configurationChanged': True, 'takesEffect': 'next_request'}
    finally:
        manager._config_snapshot = snapshot


def definitions(schema, string):
    owner = {'sessionId': string(200)}
    return {
        'providers.suggestionModels': (
            'Read app-global prompt suggestion preferences and saved connections. No inference.',
            schema(owner, [])),
        'providers.configureSuggestions': (
            'Opt in to app-level prompt suggestions using one saved connection and explicit model. '
            'Sends only bounded display transcript on a later interested-client request; never drafts or native model context.',
            schema({**owner, 'enabled': {'type': 'boolean'}, 'provider': string(100),
                    'expectedSuggestionModelsRevision': {**string(64), 'pattern': '^[0-9a-f]{64}$'},
                    'model': string(200), 'shareRecentConversation': {'const': True},
                    'effort': {'type': ['string', 'null'], 'pattern': r'^[A-Za-z0-9_-]{1,100}$'},
                    'prompt': {**string(MAX_PROMPT_BYTES), 'minLength': 1},
                    'scope': {'const': 'global'}}, ['scope'])),
    }