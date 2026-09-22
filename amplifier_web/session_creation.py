"""Fresh conversations can inherit reviewed configuration, never execution state."""
import copy
import json
import uuid

from .host.config import write_private
from .managed_chats import is_managed
from .runtime_controls import public_config
from amplifier_scheduling.store import fingerprint


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
    return config, {**location, 'workspace': config['workspace'], 'bundle': config['bundle'],
                    'selection': public_config(selection), 'configurationHash': fingerprint(config)}


def prepare(service, args, origin, caller_session_id):
    identity = args.get('id')
    if identity:
        if str(uuid.UUID(identity)) != identity: raise ValueError('Use a canonical UUID for a new conversation')
        from .session_files import amplifier_home
        native_exists = any((amplifier_home() / 'projects').glob('*/sessions/' + identity))
        if native_exists or (service.data_dir / 'sessions' / identity).exists() or any(row['id'] == identity for row in service.state['sessions']):
            raise ValueError('This conversation identity already exists; reuse the original creation command receipt')
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
    directory = service.data_dir / 'sessions' / target['id']
    # The existing configuration override/restore mechanism owns these files.
    write_private(directory / 'configuration.json', json.dumps(config['plan']))
    write_private(directory / 'control-state.json', json.dumps(config['controls']))
    if config['selection']: target['selection'] = copy.deepcopy(config['selection'])
