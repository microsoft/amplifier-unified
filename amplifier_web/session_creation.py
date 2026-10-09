"""Fresh conversations can inherit reviewed configuration, never execution state."""
import copy
import json
import uuid

from .host.config import write_private
from .managed_chats import is_managed
from .runtime_controls import public_config
from .shared_settings import settings_paths, read_yaml, update_settings, overlay
from amplifier_scheduling.store import fingerprint


def source_settings(service, source, plan):
    """Retain only used session source choices, with shared source precedence.

    Foundation resolves activation specs without replacing mount-plan sources.
    Read the history/config workspace, not a handed-off execution worktree.
    Provider identity/source fields are sufficient; their configs and credentials
    remain owned by the ordinary credential-reference binding mechanism.
    """
    from .host.config import HostConfig, read_config
    from .host.components import declarations
    identity = source.get('runtimeSessionId') or source['id']
    scoped = read_yaml(settings_paths(source['workspace'], session_id=identity)['session'])
    selected = HostConfig(service.data_dir, source['workspace'], scoped, service.data_dir).module_sources
    used = {row['module'] for row in declarations(plan) if isinstance(row, dict) and 'module' in row}
    needed = used.intersection(selected)
    if not needed:
        return {}
    # Worker preparation eagerly visits the root and immediate agents only.
    # A source callback is not retained by Foundation's lazy resolver. Settings
    # alone therefore cannot bind a module declared only in a deeper agent or
    # spawn.tools; refuse that special case instead of lazily using its old hint.
    eager = set()
    for node in (plan, *plan.get('agents', {}).values()):
        if isinstance(node, dict):
            rows = [row for kind in ('providers', 'tools', 'hooks') for row in node.get(kind, [])]
            rows += [node.get('session', {}).get(kind) for kind in ('orchestrator', 'context')]
            eager.update(row['module'] for row in rows
                         if isinstance(row, dict) and row.get('source') and 'module' in row)
    if needed - eager:
        raise ValueError('Session module sources without an eager source declaration cannot be inherited; '
                         'Foundation does not retain the source resolver for lazy activation. No task was started.')
    configured = read_config(source['workspace'], home=service.data_dir, session_id=identity)
    result = {}
    modules = {name: copy.deepcopy(value) for name, value in
               configured.settings.get('sources', {}).get('modules', {}).items() if name in needed}
    if modules:
        result['sources'] = {'modules': modules}
    overrides = {name: {'source': copy.deepcopy(row['source'])} for name, row in
                 configured.settings.get('overrides', {}).items()
                 if name in needed and isinstance(row, dict) and 'source' in row}
    if overrides:
        result['overrides'] = overrides
    providers = [{key: copy.deepcopy(row[key]) for key in ('module', 'id', 'instance_id', 'source') if key in row}
                 for row in configured.providers if row.get('module') in needed and row.get('source')]
    if providers:
        result['config'] = {'providers': providers}
    if any(not isinstance(configured.module_sources.get(name), str) or not configured.module_sources[name]
           for name in needed):
        raise ValueError('The source session has an unsupported module source selection; no task was started.')
    return result


def template(service, source):
    identity = source.get('runtimeSessionId') or source['id']
    directory = service.data_dir / 'sessions' / identity
    effective = directory / 'effective-configuration.json'
    override = directory / 'configuration.json'
    path = effective if effective.exists() else override
    if not path.exists():
        raise ValueError('Prepare the source configuration before previewing a new-task schedule')
    plan = json.loads(path.read_text())
    state_path = directory / 'control-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    # Never inherit task/goal/history, usage, receipts, active jobs or approvals.
    controls = {key: copy.deepcopy(state[key]) for key in ('selection', 'configurator', 'mode', 'budget') if key in state}
    if 'capacity' in state:
        controls['capacity'] = {**copy.deepcopy(state['capacity']), 'revision': 0}
    selection = copy.deepcopy(source.get('selection') or controls.get('selection'))
    location = {'location': {'kind': 'managed'}} if is_managed(source) else {}
    config = {**location, 'workspace': source.get('workingDirectory') or source['workspace'], 'bundle': source['bundle'],
              'selection': selection, 'plan': plan, 'controls': controls}
    sources = source_settings(service, source, plan)
    if sources:
        config['sourceSettings'] = sources
    return config, {**location, 'workspace': config['workspace'], 'bundle': config['bundle'],
                    'selection': public_config(selection), 'configurationHash': fingerprint(config)}


def prepare(service, args, origin, caller_session_id):
    identity = args.get('id')
    from .managed_deletion import removed
    if removed(service.db, identity, args.get('workspace')):
        raise ValueError('This conversation was permanently deleted. Start a new chat instead.')
    if identity:
        if str(uuid.UUID(identity)) != identity: raise ValueError('Use a canonical UUID for a new conversation')
        from .session_files import amplifier_home
        native_exists = any((amplifier_home() / 'projects').glob('*/sessions/' + identity))
        if native_exists or (service.data_dir / 'sessions' / identity).exists() or any(row['id'] == identity for row in service.state['sessions']):
            raise ValueError('This conversation identity already exists; reuse the original creation command receipt')
    if any(row.get('_deleting') and row.get('workspace') == args.get('workspace') for row in service.state['sessions']):
        raise ValueError('This chat folder is being deleted. Start a new chat instead.')
    inheritance = args.get('inheritConfiguration')
    if not inheritance: return None
    source_id = inheritance['sessionId']
    if origin not in {'ui', 'user', 'scheduler'} and source_id != caller_session_id:
        raise ValueError('Configuration inheritance belongs to the calling conversation')
    if inheritance.get('scheduledRunId'):
        if origin != 'scheduler': raise ValueError('Scheduled creation is reserved for the owning scheduler')
        from .scheduled_destinations import creation_guard
        creation_guard(service.schedules, source_id, inheritance['scheduledRunId'], identity)
    source = service._session(source_id)
    if source.get('configurationBusy'):
        raise ValueError('Source configuration is changing; review it again')
    config, summary = template(service, source)
    if inheritance['configurationHash'] != summary['configurationHash']:
        raise ValueError('The reviewed source configuration changed; preview again')
    if args.get('selection') and args['selection'] != config['selection']:
        raise ValueError('The explicit model selection must match the reviewed inherited configuration')
    # Managed inheritance copies reviewed configuration, never the source's
    # files directory. session.create allocates a fresh folder for its identity.
    managed = is_managed(config)
    if (is_managed(args) != managed or args.get('bundle') != config['bundle']
            or (bool(args.get('workspace', '').strip()) if managed else args.get('workspace') != config['workspace'])):
        raise ValueError('The new conversation must use the reviewed location and bundle')
    return config


def apply(service, target, config):
    if config is None: return
    identity = target.get('runtimeSessionId') or target['id']
    directory = service.data_dir / 'sessions' / identity
    sources = config.get('sourceSettings')
    if sources:
        from .host.config import HostConfig, read_config, merge
        # Use the ordinary session path: qualification and workers both read it.
        path = settings_paths(target['workspace'], session_id=identity)['session']
        def inherit(current):
            result = overlay(current, sources)
            if sources.get('config', {}).get('providers'):
                result['config']['providers'] = merge(current.get('config', {}).get('providers', []),
                                                       sources['config']['providers'])
            return result
        update_settings(path, inherit)
        configured = read_config(target['workspace'], home=service.data_dir, session_id=identity)
        # Provider lists merge by instance in shared settings; a new/different
        # destination provider source can still outrank a sources.modules choice.
        # Refuse before writing the execution plan, never quietly execute it.
        expected = HostConfig(service.data_dir, target['workspace'], sources, service.data_dir).module_sources
        if any(configured.module_sources.get(name) != value for name, value in expected.items()):
            raise ValueError('The new conversation cannot preserve the reviewed module sources; no task was started.')
    # The existing configuration override/restore mechanism owns these files.
    write_private(directory / 'configuration.json', json.dumps(config['plan']))
    write_private(directory / 'control-state.json', json.dumps(config['controls']))
    if config['selection']: target['selection'] = copy.deepcopy(config['selection'])
