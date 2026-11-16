"""App-global, opt-in utility model selection; never a conversation setting."""
import hashlib
import json
import uuid


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _values(manager, workspace):
    effective = manager.config(workspace)
    saved = effective.settings.get('prompt_suggestions') or {}
    value = {key: saved.get(key, default) for key, default in {
        'enabled': False, 'provider': None, 'model': None,
        'transcriptMode': 'last_n', 'humanTurns': 6}.items()}
    value.update(revision=digest(saved), takesEffect='next_request')
    return value


def selected(manager, workspace):
    value = _values(manager, workspace)
    rows = manager.provider_rows(workspace)
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
    value = {key: args.get(key, default) for key, default in {
        'enabled': False, 'provider': None, 'model': None,
        'transcriptMode': 'last_n', 'humanTurns': 6}.items()}
    if value['transcriptMode'] not in {'full', 'last_n'} or type(value['humanTurns']) is not int or not 1 <= value['humanTurns'] <= 20:
        raise ValueError('Choose full transcript or 1 to 20 recent human turns.')
    if value['enabled']:
        if not any(row['id'] == value['provider'] and row['enabled'] for row in manager.provider_rows(workspace)):
            raise ValueError('Choose an enabled saved connection for prompt suggestions.')
        if not isinstance(value['model'], str) or not value['model'].strip() or len(value['model']) > 200:
            raise ValueError('Choose an explicit suggestion model.')
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
                    'humanTurns': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'scope': {'const': 'global'}}, ['enabled', 'transcriptMode', 'humanTurns', 'scope'])),
    }