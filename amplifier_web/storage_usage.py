"""On-demand bounded disk accounting and reviewed workspace-file cleanup.

History and configuration are never cleanup targets. A durable attempt receipt
precedes rename/removal; an interrupted attempt is inspected, never replayed.
"""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

from .managed_deletion import _safe, _inventory, _idle, _hold
from .session_files import amplifier_home, sessions_dir, validate_id

LIMIT = 200000


def definitions(schema, string):
    return {
        'storage.inspect': ('Read disk usage on the owning host. Bounded scan, no symlink traversal; incomplete counts are labelled. scope selects app, history, or workspace (workspaceId required). path is relative to that scope.', schema({'scope':{'enum':['app','history','workspace']},'workspaceId':string(100),'path':string(4000)}, [])),
        'workspace.deleteFilesPreview': ('Review deletion of a workspace folder while retaining conversation history. Refuses active, overlapping, linked, or configuration-bearing folders. Does not delete anything.', schema({'id':string(100)}, ['id'])),
        'workspace.deleteFiles': ('Only after explicit user confirmation of the exact preview: permanently delete workspace files, preserve chats/history, archive its registration. Requires confirmationToken and exact confirmationPath. Never retry an interrupted filesystem mutation with a new token.', schema({'id':string(100),'confirmationToken':string(100),'confirmationPath':string(4000)}, ['id','confirmationToken','confirmationPath'])),
    }


def _workspace(app, identity):
    row = next((row for row in app.state['workspaces'] if row['id']==identity), None)
    if not row or not row.get('path'):
        raise ValueError('This workspace has no local folder.')
    return row


def _scan(path, seen, budget):
    total=0; files=0; incomplete=False
    try:
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode):return {'bytes':0,'files':0,'incomplete':True,'linked':True}
        if budget[0]<=0:return {'bytes':0,'files':0,'incomplete':True}
        budget[0]-=1
        if stat.S_ISREG(info.st_mode):
            key=(info.st_dev,info.st_ino)
            if key not in seen:total=info.st_size;files=1;seen.add(key)
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if budget[0]<=0:incomplete=True;break
                    child=_scan(Path(entry.path),seen,budget)
                    total+=child['bytes'];files+=child['files'];incomplete|=child['incomplete']
        else:incomplete=True
    except (OSError,RecursionError):incomplete=True
    return {'bytes':total,'files':files,'incomplete':incomplete}


def inspect(home, workspaces, args):
    home=Path(home).absolute();history=amplifier_home()/'projects'
    scope=args.get('scope')
    if not scope:
        roots=[{'id':'app','label':'Amplifier Unified','path':str(home),'scope':'app'},
               {'id':'history','label':'Shared CLI and Unified history (read only)','path':str(history),'scope':'history'}]
        roots += [{'id':row['id'],'workspaceId':row['id'],'label':row.get('name') or Path(row['path']).name,
                   'path':row['path'],'scope':'workspace','archived':row.get('archived',False),
                   'managed':Path(row['path']).is_relative_to(home/'workspaces')} for row in workspaces if row.get('path')]
        # Unique inode accounting in the overview. Nested roots are labelled,
        # not counted twice. Drilling in measures that folder independently.
        seen=set();budget=[LIMIT];rows=[]
        for root in roots:
            path=Path(root['path']).absolute()
            included=next((r['label'] for r in rows if path.is_relative_to(Path(r['path']))), None)
            usage=_scan(path,set() if included else seen,budget)
            rows.append({**root,**usage,**({'includedIn':included} if included else {})})
        return {'rows':rows,'bytes':sum(row['bytes'] for row in rows if not row.get('includedIn')),'incomplete':any(row['incomplete'] for row in rows),
                'description':'File sizes, counted once in this overview. Shared CLI history is read only. Links and unreadable files are excluded; this is not filesystem free-space accounting.'}
    workspace=next((r for r in workspaces if r['id']==args.get('workspaceId')),None)
    root=home if scope=='app' else history if scope=='history' else Path(workspace['path']) if workspace and workspace.get('path') else None
    if root is None:raise ValueError('Choose a workspace with a folder.')
    relative=Path(args.get('path') or '.')
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Choose a path within this storage category.')
    path=_safe(root/relative)
    if not path.is_dir():raise ValueError('This folder is no longer available.')
    seen=set();budget=[LIMIT];rows=[]
    for entry in sorted(path.iterdir(),key=lambda p:p.name.casefold()):
        usage=_scan(entry,seen,budget)
        rows.append({'id':entry.name,'label':entry.name,'path':str(entry.relative_to(root)),
                     'directory':not entry.is_symlink() and entry.is_dir(),**usage})
        if len(rows)>=2000:break
    return {'rows':sorted(rows,key=lambda row:row['bytes'],reverse=True),'path':str(path),
            'parent':str(relative.parent) if relative!=Path('.') else None,
            'bytes':sum(row['bytes'] for row in rows),'incomplete':len(rows)>=2000 or any(row['incomplete'] for row in rows),
            'workspaceId':workspace['id'] if workspace else None}


def _scope(app,identity):
    if (not shutil.rmtree.avoids_symlink_attacks or os.rename not in os.supports_dir_fd
            or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
        raise ValueError('Safe workspace-file cleanup is unavailable on this host. Use its file manager; storage inspection and archive remain available.')
    row=_workspace(app,identity);path=_safe(row['path'])
    if not path.is_dir():raise ValueError('The workspace folder is no longer available.')
    # Preserve configuration, CLI state, application state, and all independent
    # working trees. App-created workspace roots may live below app data.
    for protected in (Path.home(),app.data_dir.absolute(),amplifier_home(),Path('/')):
        if protected==path or protected.is_relative_to(path):
            raise ValueError('This folder contains application, home, or CLI state. Its files cannot be deleted here.')
    if path.is_relative_to(amplifier_home()) or (path.is_relative_to(app.data_dir.absolute()) and not path.is_relative_to(app.data_dir.absolute()/'workspaces')):
        raise ValueError('Application and CLI data are protected. Only workspace files can be cleared here.')
    if (path/'.amplifier').exists() or (path/'.amplifier').is_symlink():
        raise ValueError('This folder contains CLI configuration. Preserve that configuration and clean up files individually outside the app.')
    for other in app.state['workspaces']:
        if other['id']==identity or not other.get('path'):continue
        candidate=Path(other['path']).absolute()
        if candidate==path or candidate.is_relative_to(path) or path.is_relative_to(candidate):
            raise ValueError('Another registered workspace overlaps this folder. Its files were preserved.')
    sessions=[s for s in app.state['sessions'] if s.get('workspace')==str(path)]
    for other in app.state['sessions']:
        if other in sessions or not other.get('workspace'):continue
        candidate=Path(other['workspace']).absolute()
        if candidate.is_relative_to(path) or path.is_relative_to(candidate):
            raise ValueError('Another chat uses an overlapping folder. Its files were preserved.')
    ids={value for s in sessions for value in (s['id'],s.get('runtimeSessionId'),s.get('nativeIdentity')) if value}
    _idle(app,sessions,ids)
    from .workspace_resources import command
    resources=command(app.data_dir,identity,'workspace.resources.list',{})
    if any(r['status']=='active' for r in resources['resources']):
        raise ValueError('This workspace has active external resources. Retire them before deleting files.')
    from amplifier_foundation.session.shared_state import SharedSessionStore
    native=set()
    history=sessions_dir(path)
    if history.exists():
        for directory in history.iterdir():
            if directory.is_dir():native.add(validate_id(directory.name))
    for s in sessions:
        identity=validate_id(s.get('runtimeSessionId') or s.get('nativeIdentity') or s['id']);native.add(identity)
        for retained in (history/identity,app.data_dir/'sessions'/identity,SharedSessionStore(path,identity).checkpoint_path):
            if _safe(retained).is_relative_to(path):raise ValueError('Saved history is inside this folder. Move it safely before deleting files.')
        if s.get('historyManaged') and not (history/identity/'transcript.jsonl').is_file():
            raise ValueError('This imported chat’s retained history could not be verified. Its files were preserved.')
    return row,path,sessions,{'workspace':str(path),'nativeIds':sorted(native)},ids


def _remove(path,inventory,quarantine):
    # Rename beneath the held, non-linked parent, then compare the full tree
    # before deleting. A changed tree is retained at its recorded quarantine.
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open(_safe(path.parent),flags)
    try:
        if _inventory(path)!=inventory:raise ValueError('The folder changed. Review its contents again.')
        os.rename(path.name,quarantine.name,src_dir_fd=fd,dst_dir_fd=fd)
        moved=_inventory(quarantine)
        if not moved or {**moved,'path':str(path)}!=inventory:
            raise ValueError('The folder changed during cleanup. Its remaining files were preserved in the recorded cleanup folder.')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('Safe folder removal is unavailable on this host. The cleanup folder was preserved.')
        shutil.rmtree(quarantine.name,dir_fd=fd)
    finally:os.close(fd)


async def dispatch(app,action,args,include_state):
    from .service import AppError
    try:
        if action=='storage.inspect':
            rows=copy.deepcopy(app.state['workspaces'])
            result=await asyncio.to_thread(inspect,app.data_dir,rows,args)
            return {'accepted':True,'result':result,'effects':[]}
        async with app.lock:
            app.db.execute('CREATE TABLE IF NOT EXISTS workspace_cleanups (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
            saved=app.db.execute('SELECT value FROM workspace_cleanups WHERE id=?',(args['id'],)).fetchone()
            value=json.loads(saved[0]) if saved else None
            if action=='workspace.deleteFiles' and value and value['token']==args['confirmationToken'] and value['status']!='preview':
                return {'accepted':True,'result':value,'effects':[]}
            if action=='workspace.deleteFilesPreview' and value and value['status'] in {'attempting','interrupted'}:
                raise ValueError('A previous cleanup needs inspection. Remaining files may be at '+value['cleanupPath']+'. No deletion was retried.')
            row,path,sessions,locks,ids=_scope(app,args['id'])
            if action=='workspace.deleteFilesPreview':
                inventory=await asyncio.to_thread(_inventory,path)
                value={'id':row['id'],'name':row['name'],'path':str(path),'inventory':inventory,'status':'preview',
                       'token':uuid.uuid4().hex,'expiresAt':time.time()+600,'chatCount':len(sessions),
                       'managed':path.is_relative_to(app.data_dir/'workspaces'),
                       'description':'Permanently delete all files, including Git history and uncommitted work. Chats and saved artifacts remain; links to these original files will stop working. Archive hides a workspace without deleting files. Stop any work outside Amplifier before confirming.'}
                app.db.execute('INSERT OR REPLACE INTO workspace_cleanups VALUES (?,?)',(row['id'],json.dumps(value)));app.db.commit()
                return {'accepted':True,'result':value,'effects':[]}
            if not value or value['token']!=args['confirmationToken'] or value['expiresAt']<time.time() or args['confirmationPath']!=value['path'] or str(path)!=value['path']:
                raise ValueError('Review this workspace again and confirm its exact folder path.')
            # Only idle workers may hold locks; refuse instead of stopping work.
            held=await asyncio.to_thread(_hold,locks)
            try:
                if await asyncio.to_thread(_inventory,path)!=value['inventory']:raise ValueError('Files changed since review. Review the folder again.')
                quarantine=path.with_name('.unified-cleanup-'+value['token'])
                if quarantine.exists() or quarantine.is_symlink():raise ValueError('The cleanup destination already exists. Nothing was changed.')
                value.update(status='attempting',cleanupPath=str(quarantine))
                app.db.execute('UPDATE workspace_cleanups SET value=? WHERE id=?',(json.dumps(value),row['id']));app.db.commit()
                try:
                    await asyncio.to_thread(_remove,path,value['inventory'],quarantine)
                    value.update(status='completed',deletedBytes=value['inventory']['fileBytes'])
                except Exception:
                    value.update(status='interrupted',message='Cleanup did not complete. Inspect the original and cleanup folders; no deletion will be replayed.',deletedBytes=None)
                if not path.exists():
                    row.update(archived=True,available=False)
                    from .conversation_library import organization
                    from .session_navigation import is_top_level
                    archived = organization(app.state)['archived']
                    for session in sessions:
                        if is_top_level(session):archived.setdefault(session['id'],time.time())
                app.db.execute('UPDATE workspace_cleanups SET value=? WHERE id=?',(json.dumps(value),row['id']))
                app._publish_changes(globals={'workspaces','conversationOrganization'},sessions={s['id'] for s in sessions})
                app.db.commit()
                return {'accepted':True,'result':value,'effects':[],**({'state':app.browser_state()} if include_state else {})}
            finally:
                for lock in reversed(held):lock.release()
    except (ValueError,OSError) as error:
        raise AppError(str(error),409) from None
