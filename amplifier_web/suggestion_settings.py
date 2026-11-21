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
        'effort': None, 'prompt': INSTRUCTION,
        'transcriptMode': 'last_n', 'humanTurns': 6}.items()}
    value.update(revision=digest([saved, value, INSTRUCTION, OUTPUT_CONTRACT]),
                 defaultPrompt=INSTRUCTION, takesEffect='next_request')
    return value


def selected(manager, workspace):
    value = _values(manager, workspace)
    rows = manager.provider_rows(workspace, ordinary_only=True)
    row = next((row for row in rows if row['id'] == value['provider'] and row['enabled']), None)
    # Include the actual connection implementation/config/credential witness.
    revision = digest([value['revision'], manager.catalog_key({'id': row['id']}, workspace)
                       if row else None])
    value['revision'] = revision
    return value, row, revision


def describe(manager, workspace):
    return selected(manager, workspace)[0]


def configure(manager, args, workspace, scope):
    if scope != 'global' or not manager.global_only:
        raise ValueError('Prompt suggestions use app-global Settings.')
    previous = _values(manager, workspace)
    # Independent Settings controls submit only their changed fields. Merge all
    # omitted preferences before validation, then publish one complete value.
    value = {key: args.get(key, previous[key]) for key in (
        'enabled', 'provider', 'model', 'effort', 'prompt', 'transcriptMode', 'humanTurns')}
    validate_options(value['effort'], value['prompt'])
    if value['transcriptMode'] not in {'full', 'last_n'} or type(value['humanTurns']) is not int or not 1 <= value['humanTurns'] <= 20:
        raise ValueError('Choose full transcript or 1 to 20 recent human turns.')
    if value['enabled']:
        if not any(row['id'] == value['provider'] and row['enabled']
                   for row in manager.provider_rows(workspace, ordinary_only=True)):
            raise ValueError('Choose an enabled saved chat connection for prompt suggestions.')
        if not isinstance(value['model'], str) or not value['model'].strip() or len(value['model']) > 200:
            raise ValueError('Choose an explicit suggestion model.')
        if value['effort'] is not None:
            # Reuse ordinary model/effort capability validation without
            # discovering models or making a request merely to save Settings.
            from .provider_catalog import model_key
            from .next_message_configuration import validate_selection
            entry = manager.catalog.peek(model_key(manager.catalog_key({'id': value['provider']}, workspace))) or {}
            validate_selection({'instance': value['provider'], 'model': value['model'], 'effort': value['effort']}, {
                'providers': [{'id': value['provider'], 'sharedCatalogKey': 'selected'}],
                'catalogs': {'selected': {'metadata': entry.get('providerMetadata')}}})
    value['_revision'] = uuid.uuid4().hex  # A→B→A is not the original selection.
    manager.store.update(workspace, scope, lambda settings: settings.update(prompt_suggestions=value))
    return {'suggestionModels': describe(manager, workspace),
            'configurationChanged': True, 'takesEffect': 'next_request'}


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
                    'model': string(200), 'transcriptMode': {'enum': ['full', 'last_n']},
                    'effort': {'type': ['string', 'null'], 'pattern': r'^[A-Za-z0-9_-]{1,100}$'},
                    'prompt': {**string(MAX_PROMPT_BYTES), 'minLength': 1},
                    'humanTurns': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'scope': {'const': 'global'}}, ['scope'])),
    }