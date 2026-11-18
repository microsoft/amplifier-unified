"""Bind provider-owned decision models to optional consumers. No credential I/O."""
import copy
from importlib.metadata import entry_points

from .decision_account_catalog import backend_metadata, runtime_metadata


def row_id(row):
    return row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')


def catalogs():
    result = {}
    for entry in entry_points(group='amplifier.decision_backends'):
        try:
            value = entry.load()()
            metadata = backend_metadata(value)
            if metadata is None:
                continue
            module, definition, validate = metadata
            # Preserve the settings catalog's first valid declaration. Runtime
            # discovery separately refuses ambiguous mounts of the same module.
            if module in result:
                continue
            if definition.get('configuration_key') == 'decisions':
                row = copy.deepcopy(definition)
                runtime = value.get('runtime') or {}
                # Settings only consumes this optional conditional hint. Other
                # runtime shapes have never hidden an otherwise usable account.
                if set(runtime) == {'kind', 'module', 'when'}:
                    hook, conditional = runtime_metadata(value)
                    if hook and conditional:
                        row['decision_only_when'] = {'chat_enabled': False}
                result[module] = (row, validate)
        except Exception:
            continue  # A broken optional provider cannot break ordinary setup.
    return result


def _backends(row):
    config = row.get('config', {}).get('decisions') or {}
    if not isinstance(config, dict):
        return {}
    if isinstance(config.get('backends'), dict):
        return config['backends']
    return {config['id']: config} if config.get('id') else {}


def describe(settings, providers=None):
    definitions = catalogs()
    from .decision_namespace import logical_rows
    providers = logical_rows(settings)
    disabled = settings.get('configurator', {}).get('disabled', {}).get('providers', [])
    available, backends = [], {}
    for row in providers:
        identity = row_id(row)
        definition = definitions.get(row.get('module'))
        if not definition or row.get('enabled') is False or identity in disabled:
            continue
        public = copy.deepcopy(definition[0])
        public.update(id=identity, module=row['module'], label=f"{public['label']} · {identity}")
        configured = {item.get('model') for item in _backends(row).values() if isinstance(item, dict) and item.get('model')}
        if not public.get('default_model') and len(configured) == 1:
            public['default_model'] = next(iter(configured))
        available.append(public)
        for name, backend in _backends(row).items():
            if isinstance(backend, dict):
                backends[name] = {key: backend[key] for key in ('model', 'endpoint', 'enabled') if key in backend}
                backends[name]['provider'] = identity
    value = settings.get('decision_models') or {}
    selection = {purpose: {key: row[key] for key in ('mode', 'backend', 'allow_external_state') if key in row}
                 for purpose, row in value.get('selection', {}).items() if isinstance(row, dict)}
    services = [{'module': module, 'name': row['label'], 'decisionOnly': True,
                 **({'decisionOnlyWhen': copy.deepcopy(row['decision_only_when'])}
                    if row.get('decision_only_when') else {}),
                 **copy.deepcopy(row.get('connection', {}))}
                for module, (row, _) in definitions.items()
                if row.get('decision_only') is True or row.get('decision_only_when')]
    return {'catalog': available, 'services': services, 'backends': backends, 'selection': selection,
            'takesEffect': 'new_sessions', 'activation': 'requires_ui_use_behavior'}


def configure(manager, args, workspace, scope):
    purpose, mode = args.get('purpose'), args.get('mode')
    if purpose not in {'browser', 'computer', 'both'} or mode not in {'standard', 'decision'}:
        raise ValueError('Choose browser, computer, or both and ordinary or decision selection')
    purposes = ('browser', 'computer') if purpose == 'both' else (purpose,)
    effective = manager.config(workspace)
    bindings = {name: {'mode': 'standard', 'backend': None, 'allow_external_state': False} for name in purposes}
    selected, backend = None, None
    if mode == 'decision':
        choice = args.get('backend') or {}
        if not isinstance(choice, dict) or set(choice) - {'provider', 'model', 'endpoint'}:
            raise ValueError('Select a saved connection; credentials belong to that connection')
        selected = next((row for row in effective.connections if row_id(row) == choice.get('provider')), None)
        available = {row['id'] for row in describe(effective.settings, effective.providers)['catalog']}
        if not selected or choice.get('provider') not in available:
            raise ValueError('This decision connection is unavailable. Refresh your connections.')
        if args.get('allowExternalState') is not True:
            raise ValueError('Choose whether observations may be sent to this decision service')
        definition = catalogs()[selected['module']]
        backend = {**definition[1]({k: v for k, v in choice.items() if k != 'provider'}), 'enabled': True}
        bindings = {name: {'mode': 'decision', 'backend': 'ui-' + name + '-' + row_id(selected),
                           'allow_external_state': True} for name in purposes}
    from .shared_settings import settings_paths, read_yaml
    from .decision_namespace import connections, key, logical_rows, merge_settings, translate
    paths = settings_paths(workspace, shared_home=manager.store.shared_home, global_only=manager.global_only)

    def preview(settings, stop=None):
        result = {}
        for level, path in paths.items():
            value = settings if level == scope else read_yaml(path)
            result = merge_settings(result, translate(value, catalogs(), inherited=result))
            if level == stop: break
        return result

    def save(settings):
        if selected:
            inherited = preview(settings, stop=scope)
            if not any(row_id(row) == row_id(selected) for row in logical_rows(inherited)):
                raise ValueError('This connection belongs to the workspace. Save in its workspace scope.')
            rows = settings.setdefault('decision_models', {}).setdefault('connections', {})
            identity = key(selected)
            row = rows.get(identity)
            if row is None:
                dedicated = connections(inherited).get(identity, {})
                row = ({k: copy.deepcopy(selected[k]) for k in ('module', 'id', 'instance_id') if k in selected}
                       if dedicated and 'provider' not in dedicated else
                       {'provider': identity, 'module': selected['module']})
                rows[identity] = row
            config = row.setdefault('config', {})
            old = config.get('decisions') or {}
            models = copy.deepcopy(old.get('backends') or {})
            if old.get('id') and 'backends' not in old:
                models[old['id']] = {k: v for k, v in old.items() if k != 'id'}
            for binding in bindings.values():
                models[binding['backend']] = copy.deepcopy(backend)
            config['decisions'] = {**old, 'enabled': True, 'backends': models}
        settings.setdefault('decision_models', {}).setdefault('selection', {}).update(bindings)
        combined = preview(settings)
        selections = combined.get('decision_models', {}).get('selection', {})
        if any(selections.get(name) != binding for name, binding in bindings.items()):
            raise ValueError('This workspace overrides decision preferences. Save in its workspace scope.')
        if selected:
            row = next((r for r in logical_rows(combined) if row_id(r) == row_id(selected)), {})
            if (any(_backends(row).get(binding['backend']) != backend for binding in bindings.values()) or
                    row.get('config', {}).get('decisions', {}).get('enabled') is not True):
                raise ValueError('This workspace overrides decision models. Save in its workspace scope.')
    manager.store.update(workspace, scope, save)
    effective = manager.config(workspace)
    return {'decisionModels': describe(effective.settings, effective.providers), 'decisionModelsError': None, 'configurationChanged': True,
            'decisionSetupCompletion': {'purpose': purpose, 'mode': mode, 'scope': scope, 'takesEffect': 'new_sessions'}}


def apply(bundle, settings):
    """Bind purpose choices only; normal host assembly supplies logical accounts."""
    value = settings.get('decision_models')
    if not isinstance(value, dict):
        return
    from .shared_settings import overlay
    for row in bundle.tools:
        if row.get('module') == 'tool-ui-use':
            row['config'] = overlay(row.get('config', {}), {'selection': copy.deepcopy(value.get('selection', {}))})
