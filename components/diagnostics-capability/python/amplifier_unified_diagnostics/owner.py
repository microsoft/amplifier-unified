from .retention import selected, result, exists, managed_selected, add_protection
"""A bounded event index and durable, explicitly routed Context Intelligence outbox.

Inputs are live observations supplied by the embedding owner. No session inventory,
canonical history, credential store or client draft is opened to manufacture them.
"""
import asyncio
import copy
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from amplifier_operations.quiescence import DurableIntakeFence
from context_intelligence import build_event_payload
from .policy import DEFAULT, STREAMS, clean, metadata_fields, route_revision, validate_config

READS={'diagnostics.get','diagnostics.records','diagnostics.environment','diagnostics.receipt'}
def token(value,name,limit=200):
    if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):raise ValueError('Invalid '+name)
    return value
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def integer(value,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Integer is outside the advertised bound')
    return value

async def send_ci(destination,payload,probe=False):
    # SDK auth is lazy. It sends once, follows no redirects and validates the
    # acceptance receipt. An exception never includes a reflected remote body.
    from context_intelligence.auth import build_auth_strategy
    from context_intelligence.client import AsyncCIClient
    strategy=build_auth_strategy(auth_mode=destination['authMode'],api_key=os.environ.get(destination['apiKeyEnv'],''),auth_resource=destination['authResource'])
    client=AsyncCIClient(destination['url'],auth_strategy=strategy,timeout=8)
    if probe:
        identity=await client.whoami()
        if not identity.get('contributor_id'):raise ValueError('Caller identity unconfirmed')
    return await client.ingest(payload)

class Owner:
    def __init__(self,config,changed=None,idle=None,transport=send_ci):
        directory=Path(token(config.get('stateDirectory'),'owner directory',4096))
        if not directory.is_absolute():raise ValueError('Owner directory must be absolute')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory=directory.resolve()
        self.lease=sqlite3.connect(self.directory/'owner-lock.sqlite',timeout=0)
        try:self.lease.executescript('PRAGMA journal_mode=DELETE;CREATE TABLE IF NOT EXISTS lease(id INTEGER);BEGIN EXCLUSIVE;')
        except BaseException:self.lease.close();raise
        try:
            database=self.directory/'diagnostics.sqlite';existing=os.path.lexists(database)
            if not existing and any(os.path.lexists(str(database)+suffix) for suffix in ['-wal','-shm','-journal']):
                raise sqlite3.DatabaseError('Diagnostic database missing with surviving storage evidence')
            self.db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) if existing else sqlite3.connect(database)
            self.db.row_factory=sqlite3.Row
            if existing:
                # Validate before any schema, settings or interrupted-command writes.
                # A pre-existing empty or damaged file is evidence, never a new store.
                columns={'settings':'id,revision,value','records':'seq,id,at,stream,session,workspace,event,data',
                         'commands':'id,signature,value','deliveries':'id,record_id,destination,revision,payload,status,attempts,error,updated',
                         'counters':'name,value'}
                for table,names in columns.items():self.db.execute('SELECT '+names+' FROM '+table+' LIMIT 0')
                row=self.db.execute('SELECT revision,value FROM settings WHERE id=1').fetchone()
                if row is None or type(row[0]) is not int or row[0]<0:raise sqlite3.DatabaseError('Diagnostic settings unavailable')
                self.config=validate_config(json.loads(row[1]))
                self.db.close()
                self.db=sqlite3.connect(database.as_uri()+'?mode=rw',uri=True);self.db.row_factory=sqlite3.Row
            else:
                self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
              CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER,value TEXT);
              CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT);
              CREATE INDEX IF NOT EXISTS record_stream ON records(stream,seq);
              CREATE INDEX IF NOT EXISTS record_session ON records(session,seq);
              CREATE INDEX IF NOT EXISTS record_scope ON records(session,stream,seq);
              CREATE INDEX IF NOT EXISTS record_time ON records(at,seq);
              CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT,value TEXT);
              CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,record_id TEXT,destination TEXT,revision TEXT,payload TEXT,status TEXT,attempts INTEGER,error TEXT,updated REAL);
              CREATE INDEX IF NOT EXISTS delivery_status ON deliveries(status,updated,id);
              CREATE INDEX IF NOT EXISTS delivery_destination ON deliveries(destination,status,updated);
              CREATE INDEX IF NOT EXISTS delivery_record ON deliveries(record_id);
              CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER);
              CREATE INDEX retention_delivery_records ON deliveries(record_id,status);
              CREATE INDEX retention_record_session ON records(session,id);''')
                self.db.execute('INSERT INTO settings VALUES(1,0,?)',(encoded(DEFAULT),))
            if existing:self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute("UPDATE deliveries SET status='unknown',error='owner-interrupted' WHERE status='dispatching'")
            self.db.execute("UPDATE commands SET value=json_set(value,'$.status','unknown','$.reason','owner-interrupted-no-replay') WHERE json_extract(value,'$.status')='dispatching'")
            self.db.commit();os.chmod(self.directory/'diagnostics.sqlite',0o600)
            self.config=validate_config(json.loads(self.db.execute('SELECT value FROM settings').fetchone()[0]))
            self.intake=DurableIntakeFence(self.directory/'intake.sqlite')
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            self.lease.close();raise
        self.changed=changed;self.idle=idle;self.transport=transport;self.closed=False;self.pausing=False
        self.tasks={};self.mutations=asyncio.Lock();self.revision=0
        self.storage_error=False;self.configuration_error=False
        self.policy_revision=self.db.execute("SELECT revision FROM settings WHERE id=1").fetchone()[0]


    async def notice(self,callback):
        if callback:
            self.intake.background += 1
            try:
                try:await callback()
                except Exception:pass
            finally:
                self.intake.background -= 1

    def count(self,name,amount=1):
        self.db.execute('INSERT INTO counters VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value',(name,amount))

    def unavailable(self):
        config=copy.deepcopy(self.config);config['enabled']=False
        return {'available':False,'revision':self.policy_revision,'config':config,
                'local':{'records':None,'storageError':self.storage_error,'configurationError':self.configuration_error},
                'destinations':[],'streams':[{'id':key,'label':label,'content':key=='conversation'} for key,label in STREAMS.items()],
                'capture':{'mode':'explicit-live-observations','historicalScan':False,'providerRequests':False},'results':{}}

    def preflight(self):
        if self.storage_error:return False
        try:
            row=self.db.execute('SELECT revision,value FROM settings WHERE id=1').fetchone()
            if row is None or type(row[0]) is not int or row[0]<0:raise sqlite3.DatabaseError('Diagnostic settings unavailable')
            self.policy_revision=row[0]
            for table in ['records','commands','deliveries','counters']:
                self.db.execute('SELECT * FROM '+table+' LIMIT 0')
        except sqlite3.Error:
            self.storage_error=True;return False
        try:validate_config(json.loads(row[1]))
        except (ValueError,TypeError,KeyError):self.configuration_error=True
        return True

    def snapshot(self):
        if not self.preflight() or self.configuration_error:return self.unavailable()
        try:return self.healthy_snapshot()
        except sqlite3.Error:
            self.storage_error=True;return self.unavailable()

    def healthy_snapshot(self):
        row=self.db.execute('SELECT revision FROM settings').fetchone()
        local=dict(self.db.execute('SELECT count(*) AS records,min(at) AS oldest,max(at) AS newest FROM records').fetchone())
        local.update({'dropped':0,'outboxDropped':0,'expiredPending':0,**dict(self.db.execute('SELECT name,value FROM counters')),'storageError':False,'configurationError':False})
        destinations=[]
        for dest in self.config['destinations']:
            counts=dict(self.db.execute('SELECT status,count(*) FROM deliveries WHERE destination=? GROUP BY status',(dest['id'],)))
            last=self.db.execute('SELECT status,error,updated,attempts FROM deliveries WHERE destination=? ORDER BY updated DESC LIMIT 1',(dest['id'],)).fetchone()
            destinations.append({'id':dest['id'],'counts':counts,'last':dict(last) if last else None,'credentialAvailable':bool(os.environ.get(dest['apiKeyEnv'])) if dest['authMode']=='static' else None})
        return {'available':True,'revision':row[0],'config':copy.deepcopy(self.config),'local':local,'destinations':destinations,
                'streams':[{'id':key,'label':label,'content':key=='conversation'} for key,label in STREAMS.items()],
                'capture':{'mode':'explicit-live-observations','historicalScan':False,'providerRequests':False},'results':{}}

    def exact(self,command):
        token(command,'command ID');row=self.db.execute('SELECT value FROM commands WHERE id=?',(command,)).fetchone()
        return json.loads(row[0]) if row else {'available':False,'commandId':command}

    def retain(self,command,signature,result):
        value={'commandId':command,**result,'updatedAt':time.time(),'replayed':False}
        self.db.execute('INSERT INTO commands VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(command,signature,encoded(value)))
        return value

    def prior(self,command,signature):
        row=self.db.execute('SELECT signature,value FROM commands WHERE id=?',(token(command,'command ID'),)).fetchone()
        if row:
            if row['signature']!=signature:raise ValueError('Diagnostics command identity changed')
            return json.loads(row['value'])

    async def configure(self,args,command):
        signature=fingerprint(['diagnostics.configure',args])
        async with self.mutations:
            if prior:=self.prior(command,signature):return prior
            try:
                if set(args)!={'config','expectedRevision'}:raise ValueError('Exact settings and revision required')
                cfg=validate_config(args['config']);revision=self.db.execute('SELECT revision FROM settings').fetchone()[0]
                if integer(args['expectedRevision'],0,2**53-1)!=revision:raise ValueError('Diagnostics settings changed; refresh before saving')
            except (ValueError,TypeError,KeyError):
                with self.db:return self.retain(command,signature,{'status':'rejected','executed':False,'reason':'invalid-or-stale-settings'})
            current={d['id']:route_revision(d) for d in cfg['destinations'] if cfg['enabled'] and d['enabled']}
            revoked=[task for task,(dest,revision) in self.tasks.items() if current.get(dest)!=revision]
            for task in revoked:task.cancel()
            await asyncio.gather(*revoked,return_exceptions=True)
            with self.db:
                self.db.execute('UPDATE settings SET revision=revision+1,value=? WHERE id=1',(encoded(cfg),))
                for row in self.db.execute("SELECT DISTINCT destination,revision FROM deliveries WHERE status IN ('pending','failed')").fetchall():
                    if current.get(row['destination'])!=row['revision']:
                        self.db.execute("UPDATE deliveries SET status='cancelled',payload='{}',updated=? WHERE destination=? AND revision=? AND status IN ('pending','failed')",(time.time(),row['destination'],row['revision']))
                result=self.retain(command,signature,{'status':'completed','revision':args['expectedRevision']+1,'historicalUpload':False})
            self.config=cfg;self.configuration_error=False;self.policy_revision=result['revision'];self.prune();return result

    def prune(self):
        # Bounded delete batches; a large retention reduction never hydrates all IDs.
        with self.db:
            threshold=self.db.execute('SELECT seq FROM records ORDER BY seq DESC LIMIT 1 OFFSET ?',(self.config['maxRecords'],)).fetchone()
            cutoff=time.time()-self.config['retentionDays']*86400
            rows=self.db.execute('SELECT id FROM records WHERE at<? OR seq<=? LIMIT 1000',(cutoff,threshold[0] if threshold else -1)).fetchall()
            for row in rows:
                count=self.db.execute("SELECT count(*) FROM deliveries WHERE record_id=? AND status IN ('pending','failed','unknown')",(row[0],)).fetchone()[0]
                if count:self.count('expiredPending',count)
                # In-flight authority survives record retention until it settles.
                self.db.execute("DELETE FROM deliveries WHERE record_id=? AND status!='dispatching'",(row[0],))
                self.db.execute('DELETE FROM records WHERE id=?',(row[0],))
            self.db.execute("DELETE FROM deliveries WHERE status!='dispatching' AND NOT EXISTS (SELECT 1 FROM records WHERE records.id=deliveries.record_id)")

    def record(self,rows):
        if not self.config['enabled']:return {'accepted':False,'disabled':True,'executed':False}
        if not isinstance(rows,list) or len(rows)>32:raise ValueError('Observation batch exceeds32')
        inserted=0
        with self.db:
            for value in rows:
                try:
                    if not isinstance(value,dict) or set(value)-{'id','stream','session','workspace','event','data'}:raise ValueError('Invalid observation')
                    stream=value.get('stream');event=token(value.get('event'),'event',200);session=token(value.get('session'),'session',512);workspace=token(value.get('workspace'),'workspace',4096);identity=token(value.get('id'),'event identity')
                    if stream not in self.config['streams']:continue
                    raw=value.get('data',{})
                    if not isinstance(raw,dict) or len(encoded(raw).encode())>49152:raise ValueError('Observation exceeds bound')
                    data=clean(raw if stream=='conversation' else metadata_fields(raw));now=time.time()
                    data.update({'session_id':session,'event_id':identity,'timestamp':datetime.fromtimestamp(now,timezone.utc).isoformat(),'stream':stream,'app':'amplifier-unified'})
                    if len(encoded(data).encode())>20000:raise ValueError('Record exceeds20KB')
                    changed=self.db.execute('INSERT OR IGNORE INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',(identity,now,stream,session,workspace,event,encoded(data))).rowcount
                    if not changed:continue
                    inserted+=1
                    for dest in self.config['destinations']:
                        if not dest['enabled'] or stream not in dest['streams'] or not fnmatch.fnmatchcase(workspace,dest['workspacePattern']):continue
                        pending=self.db.execute("SELECT count(*) FROM deliveries WHERE destination=? AND status IN ('pending','dispatching','failed','unknown')",(dest['id'],)).fetchone()[0]
                        if pending>=2000:self.count('outboxDropped');continue
                        payload=build_event_payload(event,dest['workspace'],data,workspace if dest['includePaths'] else None)
                        self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(fingerprint([identity,dest['id']]),identity,dest['id'],route_revision(dest),encoded(payload),'pending',0,None,now))
                except (ValueError,TypeError,KeyError):self.count('dropped')
        self.prune();return {'accepted':True,'recorded':inserted}

    def records(self,args):
        if set(args)-{'sessionId','stream','before','limit'}:raise ValueError('Unknown record query')
        limit=integer(args.get('limit',25),1,50);stream=args.get('stream','*');params=[];clauses=[]
        if stream!='*':
            if stream not in STREAMS:raise ValueError('Unknown diagnostic stream')
            clauses.append('stream=?');params.append(stream)
        if args.get('sessionId'):clauses.append('session=?');params.append(token(args['sessionId'],'selected session',512))
        if args.get('before') is not None:clauses.append('seq<?');params.append(integer(args['before'],1,2**53-1))
        rows=self.db.execute('SELECT seq,id,at,stream,session,workspace,event,data FROM records'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY seq DESC LIMIT ?',(*params,limit+1)).fetchall()
        items=[];size=0
        for row in rows[:limit]:
            item=dict(row);item['data']=json.loads(item['data']);length=len(encoded(item).encode())
            if items and size+length>24000:break
            items.append(item);size+=length
        return {'items':items,'nextBefore':items[-1]['seq'] if items and len(rows)>len(items) else None,'format':'context-intelligence','schemaVersion':'1.0.0'}

    def kick(self):
        if self.closed or self.pausing or self.storage_error or self.configuration_error or self.intake.fence or not self.config['enabled']:return
        for _ in range(4-len(self.tasks)):
            row=self.db.execute("SELECT * FROM deliveries WHERE status='pending' ORDER BY updated,id LIMIT 1").fetchone()
            if not row:return
            dest=next((d for d in self.config['destinations'] if d['id']==row['destination'] and d['enabled'] and route_revision(d)==row['revision']),None)
            if not dest:
                with self.db:self.db.execute("UPDATE deliveries SET status='cancelled',payload='{}' WHERE id=?",(row['id'],))
                continue
            with self.db:self.db.execute("UPDATE deliveries SET status='dispatching',attempts=attempts+1 WHERE id=?",(row['id'],))
            self.intake.background+=1
            task=asyncio.create_task(self.deliver(dict(row),copy.deepcopy(dest)));self.tasks[task]=(dest['id'],row['revision']);task.add_done_callback(self.finished)

    def finished(self,task):
        self.tasks.pop(task,None);self.kick()

    async def deliver(self,row,dest):
        status='unknown';error=None
        try:
            receipt=await asyncio.wait_for(self.transport(dest,json.loads(row['payload'])),15)
            if not isinstance(receipt,dict) or receipt.get('status') not in ('queued','duplicate'):raise ValueError('Unconfirmed receipt')
            status='duplicate' if receipt['status']=='duplicate' else 'accepted'
        except asyncio.CancelledError:error='cancelled-outcome-unknown'
        except Exception as exc:
            code=getattr(exc,'status_code',None)
            status='failed' if isinstance(code,int) and 400<=code<500 and code not in (408,429) else 'unknown'
            error='http-'+str(code) if isinstance(code,int) and 100<=code<=599 else 'delivery-unconfirmed'
        finally:
            with self.db:self.db.execute("UPDATE deliveries SET status=?,error=?,updated=?,payload=CASE WHEN ? IN ('accepted','duplicate') THEN '{}' ELSE payload END WHERE id=?",(status,error,time.time(),status,row['id']))
            self.intake.background-=1;await self.notice(self.changed);await self.notice(self.idle)

    async def effect(self,operation,args,command):
        signature=fingerprint([operation,args])
        async with self.mutations:
            if prior:=self.prior(command,signature):return prior
            if set(args)!={'id'}:raise ValueError('Exact saved destination required')
            dest=next((d for d in self.config['destinations'] if d['id']==args['id']),None)
            if not dest:raise ValueError('Save the destination first')
            if operation=='diagnostics.retry':
                if not self.config['enabled'] or not dest['enabled']:raise ValueError('Forwarding must be enabled for this saved destination')
                with self.db:
                    changed=self.db.execute("UPDATE deliveries SET status='pending',error=NULL,updated=? WHERE destination=? AND revision=? AND status='failed'",(time.time(),dest['id'],route_revision(dest))).rowcount
                    return self.retain(command,signature,{'status':'completed','queued':changed,'unknownReplayed':False})
            with self.db:self.retain(command,signature,{'status':'dispatching','destination':dest['id']})
            payload=build_event_payload('diagnostics:probe',dest['workspace'],{'session_id':'probe-'+str(uuid.uuid4()),'event_id':command,'synthetic':True,'timestamp':datetime.now(timezone.utc).isoformat()})
            try:
                receipt=await asyncio.wait_for(self.transport(copy.deepcopy(dest),payload,True),15)
                if not isinstance(receipt,dict) or receipt.get('status') not in ('queued','duplicate'):raise ValueError('Unconfirmed probe')
                result={'status':'completed','phase':'ready','message':'Synthetic event accepted by the server; indexing remains unverified.','acceptance':receipt['status']}
            except asyncio.CancelledError:
                with self.db:self.retain(command,signature,{'status':'unknown','phase':'error','reason':'probe-interrupted-no-replay'})
                raise
            except Exception:result={'status':'unknown','phase':'error','reason':'probe-unconfirmed-no-replay'}
            with self.db:return self.retain(command,signature,result)

    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            if exists(self.db,"SELECT 1 FROM records r JOIN deliveries d ON d.record_id=r.id WHERE r.session=? AND d.status IN ('pending','dispatching','failed','unknown') LIMIT 1",(session,)):reasons.append('diagnostic-delivery')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);sessions=args['sessions']
        return base


    async def request(self,method,args):
        operation=args.get('operation') if method=='action' else None
        if method not in ['initialize','quiescence/inspect']:
            self.preflight()
        if self.storage_error or self.configuration_error:
            if method=='snapshot' or operation=='diagnostics.get':return self.unavailable()
            if operation=='diagnostics.receipt':
                try:return self.exact(args.get('args',{}).get('commandId'))
                except sqlite3.Error:return {'available':False,'commandId':args.get('args',{}).get('commandId'),'status':'unknown','reason':'diagnostic-storage-unavailable-no-replay'}
            if method in {'quiescence/refuseAdmission','quiescence/abortAdmission','quiescence/admissionAbortReceipt'}:raise ValueError('Diagnostic storage or policy is unavailable; abort remains unknown')
            if method=='quiescence/acquire':return {'acquired':False,'executed':False,'reason':'Diagnostic storage or policy is unavailable'}
            if method=='quiescence/release':
                error=ValueError('Diagnostic storage or policy is unavailable; durable release is unconfirmed');error.known_refusal=True;raise error
            if method=='quiescence/inspect':return {'available':False,'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'calls':self.intake.calls,'background':self.intake.background}
            if method=='initialize':raise ValueError('Diagnostic storage or policy is unavailable; owner readiness unconfirmed')
            if self.storage_error or operation!='diagnostics.configure':return {'accepted':False,'executed':False,'reason':'Diagnostic storage or policy is unavailable; nothing was dispatched'}
        if method=='quiescence.retention':return self.retention_references(args)
        if method=='quiescence.managedFiles':return self.managed_references(args)
        if self.closed:raise ValueError('Diagnostics owner closed')
        if method=='initialize':
            self.kick();return {'protocolVersion':1,'quiescence':{'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'admissionAbort':{'version':1}} if getattr(DurableIntakeFence,'ADMISSION_ABORT_VERSION',0)==1 else {}),**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})}}
        if method=='quiescence/inspect':return {'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'calls':self.intake.calls,'background':self.intake.background}
        if method=='quiescence/refuseAdmission':
            if args.get('purpose')!='distribution-update':raise ValueError('Distribution admission refusal required')
            return self.intake.acquire(args,pending=1)
        if method=='quiescence/abortAdmission':return self.intake.abort_admission(args,owner_id=args['ownerId'],pending=len(self.tasks))
        if method=='quiescence/admissionAbortReceipt':return self.intake.admission_abort_receipt(args,owner_id=args['ownerId'])
        if method=='quiescence/acquire':
            self.pausing=True
            try:
                await asyncio.gather(*list(self.tasks),return_exceptions=True)
                result=self.intake.acquire(args)
                return result
            finally:
                self.pausing=False;self.kick()
        if method=='quiescence/release':
            try:result=self.intake.release(args)
            except ValueError as error:
                error.known_refusal=True
                raise
            self.kick();return result
        operation=args.get('operation') if method=='action' else None
        passive=method=='snapshot' or operation in READS
        if (self.intake.fence or self.pausing) and not passive:return {'accepted':False,'executed':False,'reason':'Diagnostics intake held'}
        self.intake.calls+=1
        try:
            if method=='snapshot':return self.snapshot()
            if method=='record':
                result=self.record(args.get('items'));self.kick()
                if result.get('recorded'):await self.notice(self.changed)
                return result
            if method!='action':raise ValueError('Unknown diagnostics method')
            inner=args.get('args',{})
            if not isinstance(inner,dict):raise ValueError('Diagnostics arguments must be object')
            if operation=='diagnostics.get' and not inner:return self.snapshot()
            if operation=='diagnostics.receipt' and set(inner)=={'commandId'}:return self.exact(inner['commandId'])
            if operation=='diagnostics.records':return self.records(inner)
            if operation=='diagnostics.environment' and set(inner)=={'name'}:
                name=token(inner['name'],'environment name',200)
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):raise ValueError('Enter an environment name, not a value')
                return {'name':name,'available':bool(os.environ.get(name))}
            if operation=='diagnostics.configure':result=await self.configure(inner,args.get('commandId'))
            elif operation in ('diagnostics.test','diagnostics.retry'):result=await self.effect(operation,inner,args.get('commandId'))
            else:raise ValueError('Unknown diagnostics operation')
            self.kick();await self.notice(self.changed);return result
        except sqlite3.Error:
            self.storage_error=True
            raise
        finally:self.intake.calls-=1;await self.notice(self.idle)

    async def close(self):
        if self.closed:return
        self.closed=True;await asyncio.gather(*list(self.tasks),return_exceptions=True)
        self.intake.close();self.db.close();self.lease.close()
