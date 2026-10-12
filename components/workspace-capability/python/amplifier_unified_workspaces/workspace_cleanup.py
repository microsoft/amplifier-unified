"""Registration half of coordinated cleanup; this owner never deletes files."""
import hashlib
import json
import os
import time
from pathlib import Path
from . import workspace_navigation as navigation
from . import workspace_resources as resources
from .retention import KEYS


def held(owner, context):
    fence = owner.intake.fence
    if owner.closed or not isinstance(context, dict) or context.get('purpose') != 'recovery' or not fence or any(context.get(k) != fence.get(k) for k in KEYS) or fence.get('state', 'held') != 'held' or fence.get('phase', 'held') != 'held':
        raise ValueError('Exact held recovery intake required for workspace cleanup')


def inspect(owner, args):
    held(owner, args.get('context'))
    if set(args) != {'context', 'workspaceId'}:
        raise ValueError('Exact registered workspace identity required')
    with owner.db() as db:
        row = db.execute('SELECT * FROM registrations WHERE id=? AND hidden=0', (args['workspaceId'],)).fetchone()
        if not row:
            raise ValueError('Register the workspace before reviewing folder cleanup')
        if db.execute("SELECT 1 FROM commands WHERE status IN ('admitted','unknown') LIMIT 1").fetchone():
            raise ValueError('Resolve uncertain workspace requests before cleanup')
        registrations = [dict(r) for r in db.execute('SELECT * FROM registrations ORDER BY id LIMIT 10001')]
        if len(registrations) > 10000:
            raise ValueError('Registered workspace census exceeds the cleanup bound')
    owner.authorize(row['path'], existing=True)
    resource_file = owner.directory / 'workspace-resources' / (hashlib.sha256(row['id'].encode()).hexdigest() + '.json')
    if resource_file.is_symlink() or resource_file.exists() and resource_file.stat().st_size > 1024*1024:
        raise ValueError('External resource evidence is unavailable within the cleanup bound')
    external = resources.listing(owner.directory, row['id'])
    if any(r.get('status') not in {'reaped', 'observed_absent'} for r in external['resources']):
        raise ValueError('Resolve recorded external resources before removing this folder')
    basis = {'registrations': registrations, 'externalResources': external}
    return {'workspaceId': row['id'], 'path': row['path'], 'registrationRevision': row['revision'],
            'registeredPaths': sorted(r['path'] for r in registrations),
            'revision': hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest(),
            'coverage': 'workspace-registrations-and-external-resources'}


def receipt(owner, command):
    with owner.db() as db:
        row = db.execute('SELECT * FROM commands WHERE id=?', (command,)).fetchone()
    if not row:
        return {'receipt': None}
    if row['operation'] != 'workspace.cleanup.archiveRegistration':
        raise ValueError('Command belongs to a different workspace operation')
    return {'receipt': {'commandId': command, 'operation': row['operation'],
                        'state': 'succeeded' if row['status'] == 'completed' else 'unknown',
                        **({'result': json.loads(row['result'])} if row['status'] == 'completed' else {})}}


async def archive(owner, args):
    held(owner, args.get('context'))
    if set(args) != {'context', 'commandId', 'registration', 'nativeReceipt'}:
        raise ValueError('Exact reviewed workspace registration and native receipt required')
    from .owner import text
    command = text(args['commandId'], 'cleanup command identity', 200)
    registration, native = args['registration'], args['nativeReceipt']
    native_result = native.get('result', {})
    if native.get('state') != 'succeeded' or native.get('operation') != 'maintenance.workspace.remove' or not native.get('commandId') or native_result.get('workspaceId') != registration.get('workspaceId') or native_result.get('removed') is not True or native_result.get('historyPreserved') is not True or native_result.get('canonicalFilesChanged') != 0 or native_result.get('replayed') is not False:
        raise ValueError('Conclusive exact native folder-removal receipt required')
    payload = json.dumps({'registration': registration, 'nativeCommandId': native['commandId'],
                          'nativePreviewHash': native_result.get('previewHash')}, sort_keys=True)
    async with owner.lock:
        held(owner, args['context'])
        with owner.db() as db:
            prior = db.execute('SELECT * FROM commands WHERE id=?', (command,)).fetchone()
            if prior:
                if prior['operation'] != 'workspace.cleanup.archiveRegistration' or prior['payload'] != payload:
                    raise ValueError('Cleanup command identity has different exact arguments')
                return receipt(owner, command)
            row = db.execute('SELECT * FROM registrations WHERE id=? AND hidden=0', (registration['workspaceId'],)).fetchone()
            if not row or row['path'] != registration['path'] or row['revision'] != registration['registrationRevision']:
                raise ValueError('Workspace registration changed after cleanup review')
            owner.authorize(row['path'], existing=False)
            if os.path.lexists(row['path']):
                raise ValueError('The folder exists again; preserve it and inspect the original cleanup')
            db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            db.execute('UPDATE registrations SET revision=? WHERE id=?', (revision, row['id']))
            db.execute('INSERT OR REPLACE INTO workspace_archives VALUES(?,1)', (row['id'],))
            pins = navigation.saved(db)
            navigation.write(db, pins, pins['ids'], force=True)
            result = {'workspaceId': row['id'], 'archived': True, 'historyPreserved': True,
                      'nativeCommandId': native['commandId'], 'registrationRevision': revision,
                      'filesDeletedByWorkspaceOwner': False, 'replayed': False}
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)', (command, 'workspace.cleanup.archiveRegistration', payload, 'completed', json.dumps(result), time.time()))
        # Projection refresh happens after the common hold releases. Never write
        # through another owner's held catalog or synthesize a discovery result.
        return receipt(owner, command)
