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

def backup(service):
    folder=service.data_dir/'backups'/uuid.uuid4().hex
    folder.mkdir(parents=True,mode=0o700)
    database=folder/'app.sqlite3'
    with sqlite3.connect(database) as target:service.db.backup(target)
    database.chmod(0o600)
    archive=folder/'private-state.tar.gz'
    with tarfile.open(archive,'w:gz') as output:
        output.add(database,arcname='app.sqlite3')
        for name in ('config','routing','bundles','sessions','smart-tools/work'):
            path=service.data_dir/name
            if path.exists():output.add(path,arcname=name,recursive=True)
    archive.chmod(0o600)
    return {'backup':str(archive),'detail':'Private backup includes conversations, configuration and saved credentials. Keep it private; it is not encrypted.'}

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
        async with service.lock:
            result=backup(service)
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
