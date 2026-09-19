"""Private full backups and reversible app-owned reset operations."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tarfile
import time
import uuid

PARTS={'runtime':['runtime'],'cache':['foundation/cache','updates/releases','updates/active.json'],'settings':['config','routing','bundles'],'conversations':['sessions']}

def _backup_files(data_dir, session_paths):
    folder=data_dir/'backups'/uuid.uuid4().hex
    folder.mkdir(parents=True,mode=0o700)
    database=folder/'app.sqlite3'
    # This thread owns its connections; never use the event-loop connection here.
    with sqlite3.connect(f"file:{data_dir / 'app.sqlite3'}?mode=ro",uri=True) as source, sqlite3.connect(database) as target:
        # Hold one WAL read snapshot. Frequent UI writes must not restart a
        # multi-GB incremental backup indefinitely.
        source.execute('BEGIN')
        source.execute('SELECT id FROM state LIMIT 1').fetchone()
        source.backup(target,pages=256)
    database.chmod(0o600)
    archive=folder/'private-state.tar.gz'
    with tarfile.open(archive,'w:gz') as output:
        output.add(database,arcname='app.sqlite3')
        for name in ('config','routing','bundles','sessions','artifacts','smart-tools/work'):
            path=data_dir/name
            if path.exists():output.add(path,arcname=name,recursive=True)
        for path, name in session_paths:
            if path.exists():output.add(path,arcname=name,recursive=True)
        for name in ('events.sqlite3','index.sqlite3'):
            original=data_dir/'diagnostics'/name
            if original.exists():
                snapshot=folder/name
                with sqlite3.connect(f"file:{original}?mode=ro",uri=True) as source, sqlite3.connect(snapshot) as target:
                    source.backup(target,pages=256)
                snapshot.chmod(0o600)
                output.add(snapshot,arcname='diagnostics/'+name)
        config=data_dir/'diagnostics/config.json'
        if config.exists():output.add(config,arcname='diagnostics/config.json')
    archive.chmod(0o600)
    return {'backup':str(archive),'phase':'ready','detail':'Private backup includes shared session files, artifacts, configuration and saved credentials. Keep it private; it is not encrypted.'}


async def backup(service):
    async with service.lock:
        if getattr(service,'backup_in_progress',False):raise ValueError('A backup is already running.')
        service.backup_in_progress=True
        service.state['maintenance']={'phase':'backing-up','detail':'Creating a private backup in the background.'}
        service._publish()
        from .host.storage import SessionStore
        from .session_files import capture_dir
        paths=set()
        def include(workspace, identity):
            path=SessionStore.for_app(service.data_dir,workspace).directory(identity)
            name='shared-projects/'+path.parent.parent.name+'/sessions/'+path.name
            paths.add((path,name))
            capture=capture_dir(workspace,identity)
            if capture != path/'context-intelligence':
                paths.add((capture,'relocated-captures/'+path.parent.parent.name+'/sessions/'+identity+'/context-intelligence'))
        for s in service.state['sessions']:
            include(s['workspace'],s.get('runtimeSessionId') or s['id'])
        # Include the recorded worker sessions, without backing up unrelated CLI projects.
        for s in service.state['sessions']:
            for worker in s.get('workers',[]):
                if worker.get('id'):include(s['workspace'],worker['id'])
    try:
        task=asyncio.create_task(asyncio.to_thread(_backup_files,service.data_dir,paths))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    finally:
        service.backup_in_progress=False

async def reset(manager,args):
    service=manager.service
    parts=args.get('parts',['runtime'])
    if not parts or set(parts)-set(PARTS):raise ValueError('Choose runtime, cache, settings or conversations')
    targets=[p for part in parts for p in PARTS[part]]
    if not args.get('apply'):
        await manager.publish(maintenance={'resetPreview':{'parts':parts,'paths':targets},'detail':'Reset only affects this app’s data. A private backup and retained originals are created before applying.'})
        return
    if args.get('confirmation')!='RESET':raise ValueError('Type RESET to apply the selected reset')
    async with service.lock:
        if service.update_manager and (service.update_manager.busy() or service.update_manager.lock.locked()):raise ValueError('Finish active work and updates before resetting')
        service.state.setdefault('updates',{})['phase']='activating'
        service._publish()
    try:
        await service.runtime.close()
        if manager.setup_manager:await manager.setup_manager.close();manager.setup_manager=None
        result=await backup(service)
        async with service.lock:
            retained=service.data_dir/'backups'/('reset-'+uuid.uuid4().hex)
            retained.mkdir(mode=0o700)
            for name in targets:
                path=service.data_dir/name
                if path.is_symlink():raise ValueError('Refusing to reset a linked app directory')
                if path.exists():
                    target=retained/name;target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.move(str(path),str(target))
            if 'conversations' in parts:
                service.state.update(sessions=[],selectedSessionId=None,runtimeControl={},sessionConfiguration={},history=[])
                service.db.execute('DELETE FROM commands')
            if 'settings' in parts:
                from .host.config import write_private
                write_private(service.data_dir/'config/settings.yaml','_migration: {version: 1, reset: true}\n')
                import hashlib
                for workspace in {service.default_workspace,*[s['workspace'] for s in service.state['sessions']]}:
                    key=hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()[:20]
                    write_private(service.data_dir/'config/workspaces'/(key+'.yaml'),'{}\n')
                service.state['settings'].update(bundle='anchors',preferredVoice='gpt-live-1',fallbackVoice='gpt-realtime-2.1',updates={'autoCheck':True,'autoInstall':False,'intervalHours':24})
                service.state.update(setup={},bundles={},bundleDiscovery={},permissions={})
                service.state['notificationSettings']=manager.notifications.public()
                for session in service.state['sessions']:session['configurationPending']=True
            if 'cache' in parts:
                service.state['updates'].update(release=None,pendingRelease=None,canRollback=False,items=[],available=0)
                service.update_manager.inventory=[]
            service.state['updates'].update(phase='idle',pendingApp=None)
            result.update(retained=str(retained),detail='Selected app data reset. Backup and original files are retained privately; your workspaces and CLI data were untouched.')
            service.state['maintenance']=result
            service._publish()
    except BaseException:
        await manager.publish(updates={**service.state.get('updates',{}),'phase':'error','error':'Reset did not complete; retained originals are in backups.'})
        raise
