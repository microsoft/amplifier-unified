"""Workspace resource inventory, retained outside removable working folders."""
from __future__ import annotations
import json
import time
import uuid
from filelock import FileLock
from amplifier_worktrees.git import atomic


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
