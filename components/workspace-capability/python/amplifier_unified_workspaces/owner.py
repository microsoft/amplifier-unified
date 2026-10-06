from __future__ import annotations
from .sqlite_authority import inspect_authority, SCHEMA
from .retention import selected, result, exists, managed_selected, add_protection
import asyncio
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import base64
import hmac
import heapq
import secrets
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import unicodedata
import uuid
from amplifier_operations.quiescence import DurableIntakeFence

MUTATIONS={'locations.create','workspace.defaults.set','workspace.prepare','workspace.create','workspace.add','workspace.rename','workspace.remove'}

class WorkspaceError(ValueError):
    def __init__(self,message,*,executed=False,receipt=None,code='rejected'):
        super().__init__(message);self.executed=executed;self.receipt=receipt;self.code=code

def text(value,name,limit=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\0' in value:raise WorkspaceError('Invalid '+name)
    return value

def identity(path):return 'workspace:'+hashlib.sha256(str(path).encode()).hexdigest()

def display_name(value):
    name=unicodedata.normalize('NFKC',text(value,'workspace name',200)).strip()
    if any(ord(c)<32 for c in name):raise WorkspaceError('Workspace display name contains control characters')
    return name

def name_and_slug(value):
    name=display_name(value)
    if any(c in name for c in '/\\') or name in {'.','..'}:raise WorkspaceError('Use a workspace name, not a path')
    slug=re.sub(r'[^\w.-]+','-',name.casefold()).strip(' .-_')
    if not slug or len(slug.encode())>200 or slug.split('.')[0] in {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}:raise WorkspaceError('Choose a different workspace name')
    return name,slug

class Owner:
    def __init__(self,config,catalog,on_idle=None):
        self.on_idle=on_idle;self.closed=False
        self.library_query_enabled=False
        self.missing_workspace_history=False
        self.catalog=catalog;self.lock=asyncio.Lock();self.sync_lock=asyncio.Lock()
        self.directory=Path(text(config.get('stateDirectory'),'state directory')).expanduser()
        if not self.directory.is_absolute():raise WorkspaceError('State directory must be absolute')
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory=self.directory.resolve()
        roots=config.get('allowedRoots')
        if not isinstance(roots,list) or not roots or len(roots)>128:raise WorkspaceError('Explicit allowed workspace roots required')
        self.roots=[]
        for root in roots:
            candidate=Path(text(root,'root')).expanduser()
            if not candidate.is_absolute():raise WorkspaceError('Allowed roots must be absolute')
            self.roots.append(str(candidate.resolve()))
        self.launcher_default=self.authorize(config.get('defaultRoot') or str(self.directory/'workspaces'),existing=False)
        self.default_root=self.launcher_default
        self.config_revision=hashlib.sha256(json.dumps([self.roots,str(self.default_root)],sort_keys=True).encode()).hexdigest()
        self.lease=(self.directory/'owner.lock').open('a+b')
        os.chmod(self.directory/'owner.lock',0o600)
        try:fcntl.flock(self.lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close();raise WorkspaceError('Workspace owner is already running for this state directory')
        self.db_path=self.directory/'workspaces.sqlite'
        try:
            inspect_authority(self.db_path,SCHEMA,workspace=True)
            with self.db() as db:
                db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS registrations(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,name TEXT NOT NULL,hidden INTEGER NOT NULL,revision INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS registration_changes ON registrations(revision);
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL,command_id TEXT);
                CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,operation TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT,updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS command_status ON commands(status);
                ''')
                db.execute("INSERT OR IGNORE INTO meta VALUES('source',?)",('unified-workspaces:'+str(uuid.uuid4()),));db.execute("INSERT OR IGNORE INTO meta VALUES('revision','0')")
                db.execute("UPDATE commands SET status='unknown' WHERE status='admitted'")
                self.source=db.execute("SELECT value FROM meta WHERE key='source'").fetchone()[0]
                saved=db.execute("SELECT value FROM meta WHERE key='defaultRoot'").fetchone()
                if saved and saved[0]:self.default_root=self.authorize(saved[0],existing=False)
                db.execute("INSERT OR IGNORE INTO meta VALUES('configSequence','0')")
                db.execute("PRAGMA user_version=1")
                self.config_sequence=int(db.execute("SELECT value FROM meta WHERE key='configSequence'").fetchone()[0])
            self.refresh_config_revision();self.cursor_secret=secrets.token_bytes(32)
            os.chmod(self.db_path,0o600)
            self.intake=DurableIntakeFence(self.directory/'intake.sqlite')
            with self.db() as db:db.execute('CREATE INDEX IF NOT EXISTS retention_workspace ON commands(status)')
        except BaseException:
            if hasattr(self,"intake"):self.intake.close()
            fcntl.flock(self.lease.fileno(),fcntl.LOCK_UN);self.lease.close()
            raise

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.db_path,timeout=5);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    def authorize(self,value,*,existing=True):
        path=Path(text(value,'workspace path')).expanduser()
        if not path.is_absolute():raise WorkspaceError('Workspace path must be absolute')
        try:path=path.resolve(strict=existing)
        except (OSError,RuntimeError) as error:raise WorkspaceError('Workspace path is unavailable') from error
        if not any(path==Path(root) or Path(root) in path.parents for root in self.roots):raise WorkspaceError('Workspace is outside configured roots')
        if existing and not path.is_dir():raise WorkspaceError('Workspace path must be an existing directory')
        return path

    def refresh_config_revision(self):
        self.config_revision=hashlib.sha256(json.dumps([self.roots,str(self.launcher_default),str(self.default_root),self.config_sequence],sort_keys=True).encode()).hexdigest()

    def defaults(self):
        return {'defaultRoot':str(self.default_root),'configRevision':self.config_revision,'creationSupported':os.name=='posix'}

    def location_cursor(self,value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        return base64.urlsafe_b64encode(hmac.new(self.cursor_secret,raw,hashlib.sha256).digest()+raw).decode().rstrip('=')

    def locations(self,args,client):
        path=self.authorize(args.get('path') or str(self.default_root));only=args.get('directoriesOnly',True);limit=args.get('limit',100)
        if type(only) is not bool or type(limit) is not int or not 1<=limit<=100:raise WorkspaceError('Directory page requires boolean directoriesOnly and limit1..100')
        if 'controlId' in args:text(args['controlId'],'control ID',200)
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            before=os.fstat(fd);revision=hashlib.sha256(str((before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns)).encode()).hexdigest()
            scope=[client,str(path),only,revision];after=None
            if args.get('cursor'):
                try:
                    encoded=text(args['cursor'],'directory cursor',8192);raw=base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4));saved=json.loads(raw[32:])
                    if not hmac.compare_digest(raw[:32],hmac.new(self.cursor_secret,raw[32:],hashlib.sha256).digest()) or saved['scope']!=scope or saved['expires']<time.time():raise ValueError()
                    after=tuple(saved['after'])
                except (ValueError,KeyError,TypeError):raise WorkspaceError('Directory changed or cursor expired; fetch a fresh first page') from None
            def candidates():
                with os.scandir(fd) as entries:
                    for count,entry in enumerate(entries):
                        if count>=10000:raise WorkspaceError('Selected directory exceeds10000-entry scan budget; choose a narrower path',code='directory_scan_budget')
                        if entry.name.startswith('.'):continue
                        try:
                            # Never follow symlinks or expose their targets. Picker authority is explicit.
                            if entry.is_symlink():continue
                            directory=entry.is_dir(follow_symlinks=False)
                            if not directory and (only or not entry.is_file(follow_symlinks=False)):continue
                        except OSError:continue
                        key=(not directory,entry.name.casefold(),entry.name)
                        if after is None or key>after:yield (key,{'name':entry.name,'path':str(path/entry.name),'directory':directory})
            selected=heapq.nsmallest(limit+1,candidates(),key=lambda row:row[0]);current=os.fstat(fd)
            if (before.st_mtime_ns,before.st_ctime_ns)!=(current.st_mtime_ns,current.st_ctime_ns) or path.stat().st_ino!=before.st_ino or path.resolve()!=path:raise WorkspaceError('Directory changed during listing; fetch a fresh first page')
        finally:os.close(fd)
        parent=path.parent
        if parent==path or not any(parent==Path(root) or Path(root) in parent.parents for root in self.roots):parent=None
        result={'path':str(path),'parent':str(parent) if parent else None,'entries':[row[1] for row in selected[:limit]],'revision':revision,'truncated':len(selected)>limit,'coverage':{'recursive':False,'fileBodiesRead':False,'scanBudget':10000,'symlinks':'omitted','hiddenEntries':'omitted'}}
        if 'controlId' in args:result['controlId']=args['controlId']
        if len(selected)>limit:result['nextCursor']=self.location_cursor({'scope':scope,'after':selected[limit-1][0],'expires':time.time()+300})
        return result

    def location_plan(self,args):
        parent=self.authorize(args.get('path'));name=text(args.get('name'),'folder name',255)
        if name!=name.strip() or name in {'.','..'} or any(c in name for c in '/\\') or any(ord(c)<32 for c in name) or len(name.encode())>255:raise WorkspaceError('Use one folder name without slashes or surrounding spaces')
        if 'controlId' in args:text(args['controlId'],'control ID',200)
        if self.collision(parent,unicodedata.normalize('NFKC',name).casefold()).exists():raise WorkspaceError('A file or directory with this name exists; nothing was overwritten')
        path=parent/name
        if path.is_symlink():raise WorkspaceError('A symbolic link already uses this name')
        info=parent.stat()
        return {'path':str(path),'root':str(parent),'ancestor':str(parent),'ancestorIdentity':[info.st_dev,info.st_ino]}

    def local(self,id):
        with self.db() as db:row=db.execute('SELECT * FROM registrations WHERE id=?',(id,)).fetchone()
        return dict(row) if row else None

    async def selected(self,id,*,existing=False):
        text(id,'workspace ID',128);row=self.local(id) or await self.catalog('getWorkspace',{'id':id})
        if not row:raise WorkspaceError('Workspace is not indexed; attach its existing directory explicitly')
        self.authorize(row['path'],existing=existing);return row

    def record(self,path,name,hidden):
        with self.db() as db:
            db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
            revision=int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            row={'id':identity(path),'path':str(path),'name':name,'hidden':bool(hidden),'registered':not hidden,'revision':revision}
            db.execute('INSERT INTO registrations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,hidden=excluded.hidden,revision=excluded.revision',(row['id'],row['path'],name,int(hidden),revision))
        try:availability='present' if stat.S_ISDIR(path.stat().st_mode) else 'missing'
        except (FileNotFoundError,NotADirectoryError):availability='missing'
        except OSError:availability='unknown'
        return {**row,'available':availability=='present','availability':availability,'source':'registered','checkedAt':datetime.now(timezone.utc).isoformat()}

    async def synchronize(self):
        if self.intake.fence:
            checkpoint=await self.catalog('workspaceProjectionStatus',{'source':self.source})
            with self.db() as db:current=int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            return {'revision':checkpoint['revision'],'ownerRevision':current,'complete':checkpoint['revision']==current,'held':True,'repairDeferred':checkpoint['revision']!=current}
        async with self.sync_lock:
            checkpoint=await self.catalog('workspaceProjectionStatus',{'source':self.source});after=checkpoint['revision']
            while True:
                with self.db() as db:
                    current=int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]);rows=db.execute('SELECT * FROM registrations WHERE revision>? ORDER BY revision LIMIT 100',(after,)).fetchall()
                if after>current:raise WorkspaceError('Catalog projection is newer than its configured owner; inspect owner identity')
                if after==current:return {'revision':current,'complete':True}
                values=[{**dict(row),'hidden':bool(row['hidden']),'registered':not row['hidden']} for row in rows]
                through=values[-1]['revision'] if len(values)==100 else current
                result=await self.catalog('projectWorkspaces',{'source':self.source,'afterRevision':after,'throughRevision':through,'records':values});after=result['revision']

    def public_receipt(self,command):
        with self.db() as db:row=db.execute('SELECT * FROM commands WHERE id=?',(command,)).fetchone()
        if not row:return None
        result={'commandId':row['id'],'operation':row['operation'],'status':row['status'],'updatedAt':row['updated']}
        if row['result']:result['result']=json.loads(row['result'])
        return result

    def finish(self,command,status,result):
        with self.db() as db:db.execute('UPDATE commands SET status=?,result=?,updated=? WHERE id=?',(status,json.dumps(result),time.time(),command))
        return {**result,'receipt':self.public_receipt(command)}

    def inspection(self,path):
        try:
            lexical=Path(path);info=lexical.lstat();canonical=self.authorize(path,existing=False)
            return {'exists':True,'directory':stat.S_ISDIR(info.st_mode),'symlink':stat.S_ISLNK(info.st_mode),'canonicalPath':str(canonical),'directoryIdentity':[info.st_dev,info.st_ino]}
        except FileNotFoundError:return {'exists':False}
        except (OSError,WorkspaceError,RuntimeError):return {'available':False,'reason':'Destination requires explicit authorized inspection'}

    def collision(self,root,slug):
        if not root.exists():return root/slug
        with os.scandir(root) as entries:
            for count,entry in enumerate(entries):
                if count>=10000:raise WorkspaceError('Creation root exceeds bounded name-collision check; choose a narrower root')
                if unicodedata.normalize('NFKC',entry.name).casefold()==slug:return root/entry.name
        return root/slug

    def prepare(self,args):
        name,slug=name_and_slug(args.get('name'));root=self.authorize(args.get('root') or str(self.default_root),existing=False)
        if root.exists() and not root.is_dir():raise WorkspaceError('Creation root must be a directory')
        path=self.collision(root,slug)
        if path.is_symlink():raise WorkspaceError('This name is a symbolic link; attach its destination explicitly')
        registered=self.local(identity(path));disposition='open' if registered and not registered['hidden'] and path.is_dir() else 'attach' if path.is_dir() else 'blocked' if path.exists() else 'create'
        ancestor=root
        while not ancestor.exists():ancestor=ancestor.parent
        info=ancestor.stat();plan={'planId':str(uuid.uuid4()),'name':name,'path':str(path),'root':str(root),'configRevision':self.config_revision,'disposition':disposition,'workspaceId':registered['id'] if registered else None,'ancestor':str(ancestor),'ancestorIdentity':[info.st_dev,info.st_ino],'expires':time.time()+86400}
        with self.db() as db:db.execute('INSERT INTO plans VALUES(?,?,NULL)',(plan['planId'],json.dumps(plan)))
        return {key:value for key,value in plan.items() if key not in {'ancestor','ancestorIdentity','expires'}}

    def allocate(self,plan):
        if os.name!='posix':raise WorkspaceError('Safe name-based creation requires POSIX directory handles on this host')
        ancestor=Path(plan['ancestor']);fd=os.open(ancestor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if [info.st_dev,info.st_ino]!=plan['ancestorIdentity'] or ancestor.resolve()!=ancestor:raise WorkspaceError('Destination changed after preparation')
            for part in Path(plan['root']).relative_to(ancestor).parts:
                try:os.mkdir(part,mode=0o755,dir_fd=fd)
                except FileExistsError:pass
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
            target=Path(plan['path']);os.mkdir(target.name,mode=0o755,dir_fd=fd)
            created=os.stat(target.name,dir_fd=fd,follow_symlinks=False);actual=target.stat()
            if target.resolve()!=target or (created.st_dev,created.st_ino)!=(actual.st_dev,actual.st_ino):raise OSError('Destination moved during allocation')
            return [created.st_dev,created.st_ino]
        finally:os.close(fd)

    async def mutate(self,operation,args,command):
        text(command,'command ID',200);payload=json.dumps(args,sort_keys=True)
        if len(payload.encode())>16384:raise WorkspaceError('Workspace command exceeds16KiB')
        async with self.lock:
            previous=self.public_receipt(command)
            if previous:
                with self.db() as db:original=db.execute('SELECT payload FROM commands WHERE id=?',(command,)).fetchone()[0]
                if previous['operation']!=operation or original!=payload:raise WorkspaceError('Command identity was reused with different arguments')
                if previous['status']=='completed':await self.synchronize();return {**previous['result'],'receipt':previous,'replayed':False}
                if previous['status']=='rejected':raise WorkspaceError(previous['result']['reason'],receipt=previous)
                raise WorkspaceError('Workspace command is unresolved; inspect its receipt instead of replaying',executed=None,receipt=previous,code='unknown_outcome')
            plan=None;selected=None
            if operation=='locations.create':
                plan=self.location_plan(args)
            elif operation=='workspace.defaults.set':
                if args.get('expectedConfigRevision')!=self.config_revision:raise WorkspaceError('Workspace default revision changed; refresh before editing')
                raw=args.get('defaultRoot')
                if not isinstance(raw,str) or len(raw)>4000:raise WorkspaceError('Invalid workspace default root')
                path=self.authorize(raw or str(self.launcher_default),existing=False)
                if path.exists() and not path.is_dir():raise WorkspaceError('Workspace default root must be a directory')
            elif operation=='workspace.prepare':
                name_and_slug(args.get('name'));self.authorize(args.get('root') or str(self.default_root),existing=False)
            elif operation=='workspace.create':
                if os.name!='posix':raise WorkspaceError('Safe name-based creation requires POSIX directory handles on this host')
                with self.db() as db:row=db.execute('SELECT * FROM plans WHERE id=?',(text(args.get('planId'),'plan ID',100),)).fetchone()
                if not row:raise WorkspaceError('Workspace plan is unavailable; prepare again')
                plan=json.loads(row['payload'])
                if row['command_id']:
                    previous=self.public_receipt(row['command_id'])
                    if previous['status']!='completed':raise WorkspaceError('Prior allocation is unresolved; inspect and explicitly attach the destination',receipt=previous)
                    inspected=self.inspection(plan['path'])
                    if inspected.get('directoryIdentity')!=previous['result'].get('directoryIdentity'):raise WorkspaceError('Previously allocated directory changed; explicit attachment required')
                    with self.db() as db:db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',(command,operation,payload,'completed',json.dumps(previous['result']),time.time()))
                    await self.synchronize();return {**previous['result'],'receipt':self.public_receipt(command),'replayed':False}
                if plan['expires']<time.time() or plan['configRevision']!=self.config_revision:raise WorkspaceError('Workspace plan is stale; prepare again')
                if plan['disposition']!='create':raise WorkspaceError('Existing directory requires explicit attachment')
                self.authorize(plan['root'],existing=False)
                ancestor=Path(plan['ancestor']);info=ancestor.stat()
                if [info.st_dev,info.st_ino]!=plan['ancestorIdentity'] or ancestor.resolve()!=ancestor:raise WorkspaceError('Destination changed after preparation')
                if self.collision(Path(plan['root']),Path(plan['path']).name).exists():raise WorkspaceError('Destination now exists; inspect and attach explicitly')
            elif operation=='workspace.add':
                path=self.authorize(args.get('path'));selected=self.local(identity(path));name=args.get('name') or (selected['name'] if selected else path.name or str(path));name=display_name(name)
            elif operation in {'workspace.rename','workspace.remove'}:
                selected=await self.selected(args.get('id'));expected=args.get('expectedRevision')
                if type(expected) is not int or expected!=selected['revision']:raise WorkspaceError('Workspace revision changed; refresh before editing')
                path=Path(selected['path']);name=display_name(args.get('name')) if operation=='workspace.rename' else selected['name']
            else:raise WorkspaceError('Unknown workspace mutation')
            with self.db() as db:
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',(command,operation,payload,'admitted',json.dumps({'path':plan['path'] if plan else str(locals().get('path','')),'filesDeleted':False,'historyPreserved':True}),time.time()))
                if plan and 'planId' in plan:db.execute('UPDATE plans SET command_id=? WHERE id=?',(command,plan['planId']))
            try:
                if operation=='workspace.prepare':result=self.prepare(args)
                elif operation=='workspace.defaults.set':
                    with self.db() as db:
                        db.execute("INSERT OR REPLACE INTO meta VALUES('defaultRoot',?)",(str(path) if args['defaultRoot'] else '',))
                        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='configSequence'")
                        self.config_sequence=int(db.execute("SELECT value FROM meta WHERE key='configSequence'").fetchone()[0])
                    self.default_root=path;self.refresh_config_revision();result={**self.defaults(),'filesDeleted':False,'historyPreserved':True}
                elif operation=='locations.create':
                    directory_identity=self.allocate(plan)
                    result={'path':plan['path'],'parent':plan['root'],'entries':[],'truncated':False,'createdBy':command,'directoryIdentity':directory_identity,'registered':False,'filesDeleted':False,'historyPreserved':True,**({'controlId':args['controlId']} if 'controlId' in args else {})}
                else:
                    if plan:directory_identity=self.allocate(plan);path=Path(plan['path']);name=plan['name']
                    record=self.record(path,name,operation=='workspace.remove' or operation=='workspace.rename' and bool(selected['hidden']))
                    result={'workspace':record,'outcome':'created' if plan else 'attached' if operation=='workspace.add' else 'renamed' if operation=='workspace.rename' else 'removed','filesDeleted':False,'historyPreserved':True,**({'directoryIdentity':directory_identity} if plan else {})}
                result=self.finish(command,'completed',result)
            except BaseException as error:
                result=self.finish(command,'unknown',{'path':plan['path'] if plan else str(locals().get('path','')),'reason':str(error),'filesDeleted':False,'historyPreserved':True})
                raise WorkspaceError('Workspace operation interrupted; inspect before further action',executed=None,receipt=result['receipt'],code='unknown_outcome') from error
            await self.synchronize();return result

    def unresolved(self):
        with self.db() as db:
            return {status:db.execute('SELECT COUNT(*) FROM commands WHERE status=?',(status,)).fetchone()[0] for status in ('admitted','unknown')}

    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            with self.db() as db:
                if exists(db,"SELECT 1 FROM commands WHERE status IN ('admitted','unknown') LIMIT 1"):reasons.append('workspace-unsettled')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);sessions=args['sessions'];root=args['allocation']['executionDirectory']
        with self.db() as db:
            # All registrations, including hidden/cross-conversation references.
            hit=exists(db,'SELECT 1 FROM registrations WHERE path=? OR (path>=? AND path<?) LIMIT 1',(root,root+'/',root+'0'))
            if not hit:
                parents=root.split('/')
                hit=any(exists(db,'SELECT 1 FROM registrations WHERE path=? LIMIT 1',('/'.join(parents[:i]) or '/',)) for i in range(1,len(parents)))
        return add_protection(base,sessions,lambda s:['workspace-registration'] if hit else [])


    async def request(self,method,params):
        if method=='quiescence.retention':return self.retention_references(params)
        if method=='quiescence.managedFiles':return self.managed_references(params)
        if self.closed:raise WorkspaceError('Workspace owner is closed')
        if method=='quiescence/inspect':return {'version':1,'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'calls':self.intake.calls,'background':self.intake.background,'commands':self.unresolved()}
        if method=='quiescence/refuseAdmission':
            if params.get('purpose')!='distribution-update':raise ValueError('Distribution admission refusal required')
            return self.intake.acquire(params,pending=1)
        if method=='quiescence/abortAdmission':return self.intake.abort_admission(params,owner_id=params['ownerId'],pending=self.unresolved()['admitted'])
        if method=='quiescence/admissionAbortReceipt':return self.intake.admission_abort_receipt(params,owner_id=params['ownerId'])
        if method=='quiescence/acquire':return self.intake.acquire(params,pending=self.unresolved()['admitted'])
        if method=='quiescence/release':
            try:return self.intake.release(params)
            except ValueError as error:
                error.known_refusal=True
                raise
        mutates=method=='action' and params.get('operation') in MUTATIONS
        if self.intake.fence and mutates:raise WorkspaceError('Workspace owner intake is held; no mutation admitted',code='quiescence_fenced')
        if method=='initialize':return await self._request(method,params)
        # Register before lock waits, mkdir intent, and the complete catalog callback.
        # Held reads below skip synchronization writes and may disclose stale projection.
        self.intake.calls+=1
        try:return await self._request(method,params)
        finally:
            self.intake.calls-=1
            if self.intake.calls==0 and self.on_idle:
                self.intake.background += 1
                try:
                    try:await self.on_idle()
                    except Exception:pass
                finally:
                    self.intake.background -= 1

    async def _request(self,method,params):
        if method=='initialize':
            self.library_query_enabled=params.get('libraryQueryVersion')==1
            self.missing_workspace_history=self.library_query_enabled and params.get('missingWorkspaceHistory') is True
            return {'protocolVersion':1,'quiescence':{'version':1,'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'admissionAbort':{'version':1}} if getattr(DurableIntakeFence,'ADMISSION_ABORT_VERSION',0)==1 else {}),**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})},'source':self.source,'defaultRoot':str(self.default_root),'configRevision':self.config_revision,'creationSupported':os.name=='posix'}
        if method=='snapshot':return {**await self.listing({},params.get('clientId','snapshot')),'defaultRoot':str(self.default_root),'configRevision':self.config_revision,'creationSupported':os.name=='posix'}
        if method!='action':raise WorkspaceError('Unknown owner method')
        operation=params.get('operation');args=params.get('args') or {};client=text(params.get('clientId') or 'agent','client ID',512)
        if not isinstance(args,dict):raise WorkspaceError('Workspace arguments must be an object')
        fields={'defaults':set(),'defaults.set':{'defaultRoot','expectedConfigRevision'},'list':{'query','cursor','limit','includeHidden','includeUnavailable'},'inspect':{'id'},'prepare':{'name','root'},'create':{'planId'},'add':{'path','name'},'rename':{'id','name','expectedRevision'},'remove':{'id','expectedRevision'},'sessions':{'id','query','cursor','limit','parentUri','archive','libraryQueryVersion','sort','activity','location'},'receipt':{'commandId'}}
        allowed={'workspace.'+key:value for key,value in fields.items()}
        allowed.update({'locations.list':{'path','directoriesOnly','controlId','limit','cursor'},'locations.create':{'path','name','controlId'}})
        if operation not in allowed or set(args)-allowed[operation]:raise WorkspaceError('Unknown workspace operation or arguments')
        if operation=='workspace.defaults':return self.defaults()
        if operation=='locations.list':return self.locations(args,client)
        if operation=='workspace.list':return await self.listing(args,client)
        if operation=='workspace.inspect':
            row=await self.selected(args.get('id'));projection=await self.synchronize();record=await self.catalog('getWorkspace',{'id':row['id']});return {**(record or row),'projection':projection,'inspection':self.inspection(row['path']),'filesDeleted':False,'historyPreserved':True}
        if operation=='workspace.receipt':
            receipt=self.public_receipt(text(args.get('commandId'),'command ID',200))
            if not receipt:raise WorkspaceError('Workspace receipt unavailable')
            if receipt['status']=='unknown' and receipt.get('result',{}).get('path'):receipt['inspection']=self.inspection(receipt['result']['path'])
            return receipt
        if operation=='workspace.sessions':
            row=await self.selected(args['id'],existing=True) if 'id' in args else None
            if row and row.get('hidden'):raise WorkspaceError('Workspace registration is hidden; attach explicitly before browsing')
            archive=args.get('archive','active')
            if archive not in {'active','all','archived'}:raise WorkspaceError('Invalid archive selector')
            await self.synchronize();limit=self.limit(args);query={'connectionId':self.scope(client),'limit':limit,'allowedWorkspaceRoots':self.roots,'archive':archive}
            if row:query['workingDirectory']=row['path']
            for source,target in [('cursor','cursor'),('query','search'),('parentUri','parentUri')]:
                if args.get(source):query[target]=args[source]
            if 'libraryQueryVersion' in args:
                if type(args['libraryQueryVersion']) is not int or args['libraryQueryVersion']!=1 or not self.library_query_enabled:raise WorkspaceError('Versioned library query unavailable')
                for key,values,default in [('sort',{'activity','created','name'},'activity'),('activity',{'all','working','attention'},'all'),('location',{'all','managed','missing'} if self.missing_workspace_history else {'all','managed'},'all')]:
                    value=args.get(key,default)
                    if value not in values:raise WorkspaceError('Invalid library selector')
                    query[key]=value
                return await self.catalog('libraryQuery',query)
            if any(key in args for key in ('sort','activity','location')):raise WorkspaceError('Library selectors require advertised version1')
            return await self.catalog('list',query)
        try:return await self.mutate(operation,args,params.get('commandId'))
        except WorkspaceError as error:
            command=params.get('commandId')
            if error.executed is False and isinstance(command,str) and 0<len(command)<=200 and len(json.dumps(args).encode())<=16384:
                with self.db() as db:
                    db.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,?,?,?)',(command,operation,json.dumps(args,sort_keys=True),'rejected',json.dumps({'reason':str(error),'executed':False}),time.time()))
                error.receipt=error.receipt or self.public_receipt(command)
            raise

    def scope(self,client):return 'workspace:'+hashlib.sha256((self.source+':'+client).encode()).hexdigest()
    def limit(self,args):
        value=args.get('limit',50)
        if type(value) is not int or not 1<=value<=50:raise WorkspaceError('Workspace page limit must be1..50')
        return value
    async def listing(self,args,client):
        projection=await self.synchronize();query={'connectionId':self.scope(client),'limit':self.limit(args),'allowedWorkspaceRoots':self.roots}
        for source,target in [('query','search'),('cursor','cursor'),('includeHidden','includeHidden'),('includeUnavailable','includeUnavailable')]:
            if source in args and args[source] is not None:query[target]=args[source]
        result=await self.catalog('listWorkspaces',query);return {**result,'sessionQuery':{'global':True,'archive':True,'search':True,**({'version':1,'sort':['activity','created','name'],'activity':['all','working','attention'],'location':['all','managed',*(['missing'] if self.missing_workspace_history else [])],'paging':'live','counts':False} if self.library_query_enabled else {})},'coverage':{'catalog':result.get('freshness'),'projection':projection,'nativeBodiesRead':False}}

    async def close(self):
        if self.closed:return
        self.closed=True
        self.intake.close()
        if not self.lease.closed:fcntl.flock(self.lease.fileno(),fcntl.LOCK_UN);self.lease.close()
