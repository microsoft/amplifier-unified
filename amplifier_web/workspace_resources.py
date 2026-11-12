"""Workspace resource inventory, retained outside removable working folders."""
from __future__ import annotations
import json
import time
import uuid
import asyncio
import sqlite3
from filelock import FileLock
from amplifier_worktrees.git import atomic


def definitions(schema, string):
    from .task_resources import definitions as task_definitions
    scope = {'workspaceId': string(100)}
    return {
        **task_definitions(schema, string),
        'workspace.resources.managed': ('Read one task’s bounded publishing, recorded processes and owned task storage. Does not allocate, replay or stop anything. Agents inspect only their bound task. Storage is not a browser/process sandbox or dynamic preview discovery.', schema({**scope,
            'sessionId': string(200), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
            'operationsBefore': string(200),
            'publishingBefore': {'type': 'array', 'items': string(200), 'minItems': 2, 'maxItems': 2}}, ['workspaceId', 'sessionId'])),
    }


async def dispatch(app, action, args, origin, command_id, caller):
    """Route real allocations and bounded managed-resource observations."""
    from .errors import AppError
    workspace = next((w for w in app.state['workspaces'] if w['id'] == args['workspaceId']), None)
    if action in {'workspace.resources.provision', 'workspace.resources.read', 'workspace.resources.retire'}:
        from .task_resources import dispatch
        return await dispatch(app, action, args, origin, command_id, caller)
    if action == 'workspace.resources.managed':
        return await managed(app, workspace, args, origin, caller)
    raise AppError('Unknown workspace resource action.', 400)


async def managed(app, workspace, args, origin, caller):
    """Select explicit task scope without hydrating history or warming runtime."""
    from .errors import AppError
    sid = args['sessionId']
    if origin not in {'ui', 'user'} and sid != caller:
        raise AppError('Inspect resources through the calling conversation.', 403)
    session = app._session(sid, hydrate=False)
    if (workspace is None or session.get('workspace') != workspace['path']
            or session.get('sessionKind', 'root') != 'root'):
        raise AppError('Choose a root conversation in this workspace.', 403)
    # Capture identity before yielding; these owners bind every read to sid.
    binding = (session.get('workspace'), session.get('sessionKind', 'root'))
    limit = args.get('limit', 25)
    try:
        from .task_resources import read, access
        publishing, operations, storage = await asyncio.gather(
            asyncio.to_thread(app.publishing.store.resources, sid, limit, args.get('publishingBefore')),
            asyncio.to_thread(app.operations.journal.resources, sid, limit, args.get('operationsBefore')),
            asyncio.to_thread(read, app.data_dir, sid, {'limit': min(limit, 32)}),
        )
        storage['items'] = await access(app, sid, storage['items'])
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise AppError(str(exc), 409) from None
    current = app._session(sid, hydrate=False)
    if binding != (current.get('workspace'), current.get('sessionKind', 'root')):
        raise AppError('The conversation moved; inspect its current workspace.', 409)
    return {'accepted': True, 'result': {
        'workspaceId': workspace['id'], 'sessionId': sid, 'publishing': publishing, 'operations': operations,
        'storage': storage,
        'isolation': [{'kind': kind, 'status': 'inspect-storage-records',
                       'enforcement': 'separate-storage-only'}
                      for kind in ('scratch-directory', 'sqlite-database', 'browser-profile')],
        'coverage': {'publishing': 'local-static-listeners', 'operations': 'recorded-native-lifecycle',
                     'dynamicPreviewDiscovery': 'unsupported', 'remotePublishing': 'not-inspected',
                     'operationWorkingDirectory': 'unavailable', 'externalProcesses': 'not-inspected'},
    }}


def command(home, workspace_id, action, args, command_id=None):
    directory = home / 'workspace-resources'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.UUID(workspace_id).hex + '.json')
    with FileLock(str(target) + '.lock'):
        value = json.loads(target.read_text()) if target.exists() else {'revision': 0, 'resources': [], 'commands': {}}
        if action == 'workspace.resources.list':
            return {'revision': value['revision'], 'resources': value['resources']}
        fingerprint = json.dumps([action, args], sort_keys=True)
        prior = value['commands'].get(command_id) if command_id else None
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise ValueError('This resource request already belongs to a different update.')
            return prior['result']
        if action == 'workspace.resources.add':
            for key in ('kind', 'resourceId', 'owner', 'note'):
                if not isinstance(args.get(key, ''), str) or len(args.get(key, '')) > 1000:
                    raise ValueError('Invalid resource description.')
            if not all(args.get(key, '').strip() for key in ('kind', 'resourceId', 'owner')):
                raise ValueError('Identify the resource type, identifier and owner.')
            result = {key: args.get(key, '').strip() for key in ('kind', 'resourceId', 'owner', 'note')}
            result.update(id=str(uuid.uuid4()), status='active', revision=1, createdAt=time.time())
            value['resources'].append(result)
        else:
            result = next((row for row in value['resources'] if row['id'] == args['id']), None)
            if not result or result['revision'] != args['expectedRevision']:
                raise ValueError('The resource record changed. Inspect it before updating.')
            if args['status'] not in {'active', 'reaped', 'observed_absent'} or not args.get('evidence', '').strip():
                raise ValueError('An observed resource status needs evidence. Recording it does not perform teardown.')
            result.update(status=args['status'], evidence=args['evidence'].strip(), updatedAt=time.time(), revision=result['revision'] + 1)
        value['revision'] += 1
        if command_id:
            value['commands'][command_id] = {'fingerprint': fingerprint, 'result': dict(result)}
            value['commands'] = dict(list(value['commands'].items())[-200:])
        atomic(target, value)
        return result
