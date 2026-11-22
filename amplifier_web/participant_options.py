"""Bounded ACP select controls, scoped to one attached execution."""
import hashlib
import json

from .errors import AppError


def visible_options(rows):
    """Retain supported choices and labels only. Agent metadata can contain private values."""
    options = []
    for item in rows or ():
        row = item.model_dump(mode='json', by_alias=True) if hasattr(item, 'model_dump') else item
        if row.get('type') != 'select':
            continue
        kind = row.get('category')
        if kind not in ('model', 'thought_level') and row.get('id') not in ('provider', 'connection', 'history-reuse'):
            continue
        choices = []
        for group in row['options']:
            for choice in group.get('options', [group]):
                if (isinstance(choice.get('value'), str) and len(choice['value']) <= 500 and
                        isinstance(choice.get('name'), str) and 0 < len(choice['name']) <= 500):
                    choices.append({k: choice[k] for k in ('value', 'name')})
        if (len(choices) > 256 or row['currentValue'] not in {c['value'] for c in choices}
                or not 0 < len(row['id']) <= 200 or not 0 < len(row['name']) <= 200):
            continue
        option = dict(id=row['id'], name=row['name'], currentValue=row['currentValue'],
                      category=kind, options=choices)
        if row.get('description') and len(row['description']) <= 240:
            option['description'] = row['description']
        options.append(option)
    return options if len(json.dumps(options).encode()) <= 32768 else []


def revision(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()


def require_choice(rows, config_id, value):
    row = next((row for row in rows if row['id'] == config_id), None)
    if not row or value not in {c['value'] for c in row['options']}:
        raise AppError('This choice is no longer available for this agent.', 409)


def advertised(service, session):
    from acp.schema import SessionConfigOptionSelect
    from .turn_model_settings import avoidable
    catalog = service.state.get('runtimeControl', {}).get(session['id'], {}).get('configuration.catalog', {})
    effective = session.get('pendingModelSelection') or session.get('selection') or catalog.get('effective') or {}
    instance, model = effective.get('instance'), effective.get('model')
    providers = {}
    for row in catalog.get('providers', []):
        if (row.get('available') is False or row.get('authenticationRequired') or
                row.get('config', {}).get('chat_enabled') is False or
                catalog.get('connectionBindings', {}).get(row['id'], {}).get('eligible') is False):
            continue
        entry = service.state.get('modelCatalogs', {}).get(row.get('sharedCatalogKey'), {})
        models = {}
        for item in entry.get('models', []):
            if isinstance(item, str):
                models[item] = item
            elif isinstance(item, dict) and item.get('id'):
                models[item['id']] = item.get('display_name') or item.get('displayName') or item.get('name') or item['id']
        if models:
            providers[row['id']] = (row.get('displayName') or row['id'], models, entry)
    if not providers:
        return [], {}
    options, changes = [], {}

    def add(key, label, category, current, values):
        options.append(SessionConfigOptionSelect(id=key, name=label, category=category, current_value=current,
            options=[{'value': v, 'name': name} for v, name in values.items()]))

    connections = {k: v[0] for k, v in providers.items()}
    if instance not in connections:
        connections[instance or ''] = instance or 'Agent default'
    add('provider', 'Connection', 'model_config', instance or '', connections)
    for name, (_, models, _) in providers.items():
        changes['provider', name] = {'selection': {'instance': name, 'model': model if model in models else next(iter(models))}}
    if instance not in providers:
        return options, changes
    _, models, entry = providers[instance]
    display_models = dict(models)
    if model not in models:
        display_models[model or ''] = model or 'Agent default'
    add('model', 'Model', 'model', model or '', display_models)
    for value in models:
        changes['model', value] = {'selection': {'instance': instance, 'model': value}}
    metadata = entry.get('metadata') or {}
    efforts = metadata.get('modelListInfo', {}).get('request_reasoning_efforts', {}).get(model)
    if efforts is None:
        # Only unconditional provider-wide overrides are valid without exact model metadata.
        fields = (metadata.get('configSchema') or {}).get('fields', [])
        choices = next((row.get('choices', []) for row in fields
                        if row.get('id') == 'reasoning_effort' and not row.get('show_when')), [])
        efforts = [choice.get('value') if isinstance(choice, dict) else choice for choice in choices]
    efforts = [e for e in efforts if isinstance(e, str) and e and e != 'default']
    if efforts and (not effective.get('effort') or effective['effort'] in efforts):
        values = {'default': 'Provider default', **{e:e.title() for e in efforts}}
        add('effort', 'Reasoning effort', 'thought_level', effective.get('effort') or 'default', values)
        for value in values:
            changes['effort', value] = {'selection': {'instance': instance, 'model': model,
                **({'effort': value} if value != 'default' else {})}}
    impact = session.get('nextMessageImpact') or {}
    if impact.get('coldModels') or impact.get('coldConnections'):
        options[0].description = 'Switching models or connections may make your next reply take longer if earlier messages need to be read again.'
    if avoidable(service, session):
        previous = session['previousTurnSettings']
        old = previous['selection']
        add('history-reuse', 'Earlier settings', 'model_config', 'pending',
            {'pending': 'Current choice', 'restore': f'Keep {old["instance"]} · {old["model"]}'})
        changes['history-reuse', 'restore'] = {'onlyIfAvoidable': True, 'restorePreviousInputId': previous['inputId'],
                                              'restorePreviousGenerationId': previous['generationId']}
    return options, changes
