"""Local-first Context Intelligence capture and independently routed durable outboxes.

Only bounded summaries enter app state. Payloads, credentials and delivery storage
never become implicit model context. Network failure cannot stop a conversation.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence.auth import build_auth_strategy
from context_intelligence import build_event_payload

STREAMS = {
    'app': 'App actions', 'sessions': 'Session lifecycle', 'workers': 'Worker activity',
    'tools': 'Tool activity', 'usage': 'Models, tokens and cost', 'canvas': 'Canvas activity',
    'smartTools': 'Smart Tool activity', 'updates': 'Update diagnostics',
    'conversation': 'Conversation text (content)',
}
METADATA = [key for key in STREAMS if key != 'conversation']
DEFAULT = {'enabled': True, 'streams': METADATA, 'retentionDays': 30, 'maxRecords': 25000, 'destinations': []}
# Content is never inferred from arbitrary args, exceptions, HTML, results or prompts.
META_KEYS = {'id','sessionId','rootSessionId','parentId','turnId','inputId','messageId','toolCallId','callId',
             'tool_name','tool_call_id','input_tokens','output_tokens','total_tokens','cache_read_input_tokens','cache_creation_input_tokens','cost_usd',
             'commandId','attemptId','action','origin','kind','phase','status','role','via','tool','provider','model',
             'startedAt','endedAt','at','durationMs','exitCode','stdoutBytes','stderrBytes','errorType','timedOut',
             'expectedVersion','observedVersion','revision','probe','usage','costUsd','costType','inputTokens',
             'outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens','serverId','operationId','artifactId',
             'ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent',
             'cliAbsent','appSessionId','pythonVersion','version','stage','size','count','errorCode','runtimeSessionId'}
SECRET_KEY = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|access.?token|refresh.?token)')


def clean(value, depth=0):
    """Best-effort content redaction; explicit content opt-in is still sensitive."""
    if depth > 12: return '[depth limit]'
    if isinstance(value, dict):
        return {str(k)[:100]: '[REDACTED]' if SECRET_KEY.search(str(k)) else clean(v,depth+1) for k,v in list(value.items())[:100]}
    if isinstance(value, list): return [clean(v,depth+1) for v in value[:100]]
    if isinstance(value,str):
        text=value[:16000]
        text=re.sub(r'(?i)(bearer\s+)[^\s\"\']+',r'\1[REDACTED]',text)
        text=re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@',r'\1[REDACTED]@',text)
        for name,secret in os.environ.items():
            if SECRET_KEY.search(name) and len(secret)>=8: text=text.replace(secret,'[REDACTED]')
        return text
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,(int,float,bool)) or value is None: return value
    return '[unsupported]'


def validate_config(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT): raise ValueError('Unknown diagnostics setting.')
    cfg={**copy.deepcopy(DEFAULT),**copy.deepcopy(value)}
    if type(cfg['enabled']) is not bool: raise ValueError('Capture enabled must be true or false.')
    for key,minimum,maximum in [('retentionDays',1,365),('maxRecords',100,100000)]:
        if type(cfg[key]) is not int or not minimum<=cfg[key]<=maximum: raise ValueError(f'{key} must be between {minimum} and {maximum}.')
    def streams(items):
        if not isinstance(items,list) or any(s not in STREAMS for s in items): raise ValueError('Choose known data streams.')
        return list(dict.fromkeys(items))
    cfg['streams']=streams(cfg['streams'])
    if not isinstance(cfg['destinations'],list) or len(cfg['destinations'])>10: raise ValueError('Use at most ten destinations.')
    seen=set()
    for dest in cfg['destinations']:
        allowed={'id','name','url','enabled','streams','workspace','workspacePattern','includePaths','authMode','apiKeyEnv','authResource'}
        if not isinstance(dest,dict) or set(dest)-allowed: raise ValueError('Unknown destination setting.')
        dest.setdefault('id',str(uuid.uuid4()));dest.setdefault('name','Context Intelligence')
        dest.setdefault('enabled',False);dest.setdefault('streams',[])
        dest.setdefault('workspace','amplifier-unified');dest.setdefault('workspacePattern','*')
        dest.setdefault('includePaths',False);dest.setdefault('authMode','static')
        dest.setdefault('apiKeyEnv','AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY');dest.setdefault('authResource','')
        for key in ('id','name','url','workspace','workspacePattern','apiKeyEnv','authResource'):
            if not isinstance(dest.get(key),str) or len(dest[key])>2000: raise ValueError(f'Invalid destination {key}.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',dest['id']) or dest['id'] in seen: raise ValueError('Destination IDs must be unique.')
        seen.add(dest['id'])
        if not dest['name'].strip() or not dest['workspace'].strip(): raise ValueError('Give each destination a name and workspace label.')
        parsed=urlsplit(dest['url'])
        try: parsed.port
        except ValueError: raise ValueError('Invalid server port.') from None
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use an HTTP(S) server URL without credentials, query or fragment.')
        if parsed.scheme=='http' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('Use HTTPS for remote servers; HTTP is allowed for localhost.')
        dest['url']=dest['url'].rstrip('/')
        if dest['authMode'] not in {'static','entra'}: raise ValueError('Choose API key or Microsoft Entra authentication.')
        if dest['authMode']=='static' and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',dest['apiKeyEnv']): raise ValueError('Enter an environment variable name, not an API key.')
        if dest['authMode']=='entra' and not dest['authResource'].strip(): raise ValueError('Entra authentication requires a resource URI.')
        if type(dest['enabled']) is not bool or type(dest['includePaths']) is not bool: raise ValueError('Destination switches must be true or false.')
        dest['streams']=streams(dest['streams'])
        if set(dest['streams'])-set(cfg['streams']): raise ValueError('Enable local capture for every stream selected by a destination.')
    return cfg


def route_revision(dest):
    return hashlib.sha256(json.dumps(dest,sort_keys=True).encode()).hexdigest()


class Diagnostics:
    def __init__(self,service):
        self.service=service
        self.directory=Path(service.data_dir)/'diagnostics'
        self.path=self.directory/'events.sqlite3'
        self.config_path=self.directory/'config.json'
        self.configuration_error=False
        try:self.config=validate_config(json.loads(self.config_path.read_text())) if self.config_path.exists() else copy.deepcopy(DEFAULT)
        except (ValueError,OSError):
            self.configuration_error=True
            self.config={**copy.deepcopy(DEFAULT),'enabled':False}
        self.flush_lock=asyncio.Lock()
        self.pending=[];self.dropped=service.state.get('diagnostics',{}).get('local',{}).get('dropped',0);self.storage_error=False;self.task=None;self.stopping=False
        self.delivery_tasks={}
        self.policy_generation=0;self.configuring=False
        self.instance_id='unified-'+str(uuid.uuid4())
        self.route_since=time.time()
        self.results={};self._last_summary=None
        self.storage_ready=False
        try:self._initialize_storage()
        except (OSError,sqlite3.Error,ValueError):self.storage_error=True
        self.service.state['diagnostics']={'config':copy.deepcopy(self.config),'streams':[{'id':k,'label':v,'content':k=='conversation'} for k,v in STREAMS.items()],'destinations':[],'local':{'records':None,'storageError':self.storage_error},'results':{}}

    def _initialize_storage(self):
        """Never delete/replace an unreadable capture database; retry after repair."""
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self._db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,at REAL,stream TEXT,session TEXT,workspace TEXT,event TEXT,data TEXT);
              CREATE INDEX IF NOT EXISTS record_session ON records(session,seq);
              CREATE TABLE IF NOT EXISTS deliveries(record_id TEXT,destination TEXT,revision TEXT,payload TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_at REAL DEFAULT 0,error TEXT,updated REAL,PRIMARY KEY(record_id,destination));
              CREATE INDEX IF NOT EXISTS delivery_ready ON deliveries(destination,status,next_at);
              CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER);
              CREATE TABLE IF NOT EXISTS captures(path TEXT PRIMARY KEY,offset INTEGER,inode INTEGER);
            ''')
            # The original database stored another event archive. Export it once
            # into the shared CI capture, then keep only bounded offset indexes.
            from .session_files import capture_dir, event_index, append_event
            indexes = {}
            for row in db.execute('SELECT id,workspace,session,event,data FROM records'):
                data = json.loads(row['data'])
                if '$event' in data:
                    continue
                directory = capture_dir(row['workspace'], row['session'])
                if directory not in indexes:
                    indexes[directory] = event_index(directory)
                reference = indexes[directory].get(row['id'])
                if reference is None:
                    reference = append_event(row['workspace'], row['session'], row['event'], data)
                    indexes[directory][row['id']] = reference
                db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(reference), row['id']))
        self.path.chmod(0o600)
        self.storage_ready=True

    def _storage_failed(self):
        self.storage_error=True;self.storage_ready=False

    def _unavailable_summary(self):
        return {'config':copy.deepcopy(self.config),'local':{'records':None,'oldest':None,'newest':None,
                'dropped':self.dropped,'storageError':True,'configurationError':self.configuration_error},
                'destinations':[],'results':copy.deepcopy(self.results)}

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def start(self):
        if self.task is None:
            self.task=asyncio.create_task(self._loop())
            self.record('app',{'event':'app:started','data':{'version':__import__('amplifier_web').__version__}})

    def record(self,stream,event,*,session_id=None,workspace=None,parent_id=None):
        try:
            self._record(stream,event,session_id=session_id,workspace=workspace,parent_id=parent_id)
        except Exception:
            self.dropped+=1

    def _record(self,stream,event,*,session_id=None,workspace=None,parent_id=None):
        cfg=self.config
        if self.stopping or not cfg['enabled'] or stream not in cfg['streams']: return
        if not self.storage_ready:self.dropped+=1;return
        if len(self.pending)>=2000: self.dropped+=1;return
        data=event.get('data',{})
        if stream!='conversation': data={k:v for k,v in data.items() if k in META_KEYS}
        data=clean(data)
        for key in ('usage','probe'):
            if isinstance(data.get(key),dict):data[key]={k:v for k,v in data[key].items() if k in META_KEYS}
        if len(json.dumps(data))>48000:
            self.dropped+=1;return
        now=time.time();identity=str(uuid.uuid4())
        capture_session=session_id or data.get('rootSessionId') or data.get('sessionId') or self.instance_id
        # Merely listing or viewing a CLI chat does not enroll its capture in
        # app diagnostics. Keep that action in the app's own stream instead.
        owner=next((s for s in self.service.state.get('sessions', [])
                    if capture_session in {s.get('id'), s.get('runtimeSessionId'), s.get('nativeIdentity')}
                    and (not workspace or s.get('workspace') == workspace)), None)
        from .session_files import validate_id
        try:
            validate_id(capture_session)
            valid_identity=True
        except ValueError:
            valid_identity=False
        if (owner and owner.get('historyManaged')) or not valid_identity:
            data.setdefault('runtimeSessionId', capture_session)
            if owner:data.setdefault('appSessionId', owner['id'])
            capture_session=self.instance_id
            workspace=self.service.default_workspace
        data.update({'session_id':capture_session,
                     'timestamp':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                     'event_id':identity,'stream':stream,'app':'amplifier-unified'})
        if parent_id: data['parent_id']=parent_id
        workspace=workspace or self.service.default_workspace
        routes=[(d['id'],route_revision(d),build_event_payload(event['event'],d['workspace'],data,workspace if d['includePaths'] else None))
                for d in cfg['destinations'] if d['enabled'] and stream in d['streams'] and fnmatch.fnmatchcase(workspace,d['workspacePattern'])]
        self.pending.append((identity,now,stream,data['session_id'],workspace,event['event'],data,routes))

    def _persist(self,items,cfg):
        if not self.storage_ready:self._initialize_storage()
        revisions={d['id']:route_revision(d) for d in cfg['destinations'] if cfg['enabled'] and d['enabled']}
        with self._db() as db:
            for identity,now,stream,session,workspace,event,data,routes in items:
                from .session_files import append_event
                reference=append_event(workspace,session,event,data)
                db.execute('INSERT INTO records(id,at,stream,session,workspace,event,data) VALUES(?,?,?,?,?,?,?)',(identity,now,stream,session,workspace,event,json.dumps(reference)))
                for dest,revision,payload in routes:
                    if revisions.get(dest)!=revision: continue
                    count=db.execute("SELECT count(*) FROM deliveries WHERE destination=? AND status IN ('pending','failed')",(dest,)).fetchone()[0]
                    if count>=2000:
                        db.execute("INSERT INTO counters VALUES('outboxDropped',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
                        continue
                    db.execute('INSERT OR IGNORE INTO deliveries(record_id,destination,revision,payload,status,updated) VALUES(?,?,?,?,?,?)',(identity,dest,revision,json.dumps(payload),'pending',now))
            from .capture_index import index_shared
            # Navigation contains all CLI projects, including unresolved and
            # read-only workers. Discovery is not consent to scan their captures.
            sessions=[s for s in list(self.service.state.get('sessions', []))
                      if not s.get('historyManaged') and s.get('workspace')]
            scopes=[]
            from .session_files import validate_id
            for session in sessions:
                identities=[session.get('runtimeSessionId') or session['id'],
                            *(worker.get('id') for worker in session.get('workers', []))]
                for capture_session in identities:
                    try:validate_id(capture_session)
                    except ValueError:continue
                    scopes.append((session['workspace'], capture_session))
            for identity,at,stream,session,workspace,event,data in index_shared(db,scopes,cfg):
                if not cfg['enabled'] or at<self.route_since:continue
                # Forward a selected projection of new hook records. Reading
                # historical CLI captures never backfills any destination.
                selected=clean({k:v for k,v in data.items() if k in META_KEYS or (stream=='conversation' and k in {'prompt','response','text','content'})})
                selected.update(session_id=session,event_id=data.get('event_id') or identity,
                                timestamp=data.get('timestamp') or datetime.fromtimestamp(at,timezone.utc).isoformat())
                if data.get('parent_id'):selected['parent_id']=data['parent_id']
                for dest in cfg['destinations']:
                    if not dest['enabled'] or stream not in dest['streams'] or not fnmatch.fnmatchcase(workspace,dest['workspacePattern']):continue
                    count=db.execute("SELECT count(*) FROM deliveries WHERE destination=? AND status IN ('pending','failed')",(dest['id'],)).fetchone()[0]
                    if count>=2000:
                        db.execute("INSERT INTO counters VALUES('outboxDropped',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
                        continue
                    payload=build_event_payload(event,dest['workspace'],selected,workspace if dest['includePaths'] else None)
                    db.execute('INSERT OR IGNORE INTO deliveries(record_id,destination,revision,payload,status,updated) VALUES(?,?,?,?,?,?)',
                               (identity,dest['id'],route_revision(dest),json.dumps(payload),'pending',time.time()))
            cutoff=time.time()-cfg['retentionDays']*86400
            obsolete=[r[0] for r in db.execute('SELECT id FROM records WHERE at<? OR seq NOT IN (SELECT seq FROM records ORDER BY seq DESC LIMIT ?)',(cutoff,cfg['maxRecords']))]
            for identity in obsolete:
                count=db.execute("SELECT count(*) FROM deliveries WHERE record_id=? AND status IN ('pending','failed')",(identity,)).fetchone()[0]
                if count: db.execute("INSERT INTO counters VALUES('expiredPending',?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(count,))
                db.execute('DELETE FROM deliveries WHERE record_id=?',(identity,));db.execute('DELETE FROM records WHERE id=?',(identity,))
            # Revocation applies on restart too. Accepted rows remain as receipts.
            for row in db.execute("SELECT DISTINCT destination,revision FROM deliveries WHERE status IN ('pending','failed')").fetchall():
                if revisions.get(row['destination'])!=row['revision']:
                    db.execute("UPDATE deliveries SET status='cancelled',payload='{}' WHERE destination=? AND revision=? AND status IN ('pending','failed')",tuple(row))
            # Reuse freed pages and checkpoint WAL; maxRecords bounds logical storage.
            db.execute('PRAGMA incremental_vacuum(100)')

    async def flush(self):
        async with self.flush_lock:
            await self._flush()

    async def _flush(self):
        items,self.pending=self.pending,[]
        try:
            task=asyncio.create_task(asyncio.to_thread(self._persist,items,copy.deepcopy(self.config)))
            try:await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            self.storage_error=False
        except Exception:
            self._storage_failed();self.dropped+=len(items)

    def _client(self,dest):
        strategy=build_auth_strategy(auth_mode=dest['authMode'],api_key=os.environ.get(dest['apiKeyEnv'],''),auth_resource=dest['authResource'])
        return AsyncCIClient(dest['url'],auth_strategy=strategy,timeout=8)

    @staticmethod
    def _failure(exc):
        if isinstance(exc,CIClientError):
            return {'type':exc.error_type,'statusCode':exc.status_code}
        return {'type':'configuration' if isinstance(exc,ValueError) else 'connection_error'}

    def _next(self,dest):
        with self._db() as db:
            row=db.execute("SELECT * FROM deliveries WHERE destination=? AND revision=? AND status='pending' AND next_at<=? ORDER BY rowid LIMIT 1",(dest['id'],route_revision(dest),time.time())).fetchone()
            return dict(row) if row else None

    def _receipt(self,row,error=None,receipt=None):
        attempts=row['attempts']+1;status='duplicate' if (receipt or {}).get('status')=='duplicate' else 'accepted'
        retry=bool(error and (error.get('type') in {'timeout','connection_error'} or error.get('statusCode') in {408,429} or (error.get('statusCode') or 0)>=500))
        if error: status='pending' if retry and attempts<8 else 'failed'
        with self._db() as db:
            db.execute("UPDATE deliveries SET status=?,attempts=?,next_at=?,error=?,updated=?,payload=CASE WHEN ? IN ('accepted','duplicate') THEN '{}' ELSE payload END WHERE record_id=? AND destination=? AND status='pending' AND revision=?",(status,attempts,time.time()+min(300,2**attempts),json.dumps(error) if error else None,time.time(),status,row['record_id'],row['destination'],row['revision']))

    def _authorized(self,dest,generation):
        current=next((d for d in self.config['destinations'] if d['id']==dest['id']),None)
        return (not self.stopping and self.storage_ready and not self.configuring and generation==self.policy_generation and self.config['enabled']
                and current is not None and current['enabled'] and route_revision(current)==route_revision(dest))

    async def deliver(self,dest):
        generation=self.policy_generation
        for _ in range(20):
            if not self._authorized(dest,generation):return
            try:row=await asyncio.to_thread(self._next,dest)
            except (OSError,sqlite3.Error):self._storage_failed();return
            # The DB read can overlap a completed policy change. Its detached
            # row is not permission to send a now-cancelled queued payload.
            if not row or not self._authorized(dest,generation):return
            try:
                receipt=await self._client(dest).ingest(json.loads(row['payload']))
                error=None
            except Exception as exc:error=self._failure(exc);receipt=None
            try:await asyncio.to_thread(self._receipt,row,error,receipt)
            except (OSError,sqlite3.Error):self._storage_failed();return
            if error:return

    def _summary(self):
        try:return self._read_summary()
        except (OSError,sqlite3.Error):
            self._storage_failed()
            return self._unavailable_summary()

    def _read_summary(self):
        with self._db() as db:
            local=dict(db.execute('SELECT count(*) AS records,min(at) AS oldest,max(at) AS newest FROM records').fetchone())
            local.update({'dropped':self.dropped,'storageError':self.storage_error,'configurationError':self.configuration_error,**dict(db.execute('SELECT name,value FROM counters'))})
            rows=[]
            for dest in self.config['destinations']:
                counts=dict(db.execute('SELECT status,count(*) FROM deliveries WHERE destination=? GROUP BY status',(dest['id'],)))
                last=db.execute('SELECT status,error,updated,attempts FROM deliveries WHERE destination=? ORDER BY updated DESC LIMIT 1',(dest['id'],)).fetchone()
                failure=db.execute("SELECT error FROM deliveries WHERE destination=? AND status IN ('failed','pending') AND error IS NOT NULL ORDER BY updated DESC LIMIT 1",(dest['id'],)).fetchone()
                rows.append({'id':dest['id'],'counts':counts,'last':dict(last) if last else None,'error':json.loads(failure[0]) if failure else None,'credentialAvailable':bool(os.environ.get(dest['apiKeyEnv'])) if dest['authMode']=='static' else None})
            return {'config':copy.deepcopy(self.config),'local':local,'destinations':rows,'results':copy.deepcopy(self.results)}

    async def publish(self):
        try:summary=await asyncio.to_thread(self._summary)
        except Exception:
            self._storage_failed();summary=self._unavailable_summary()
        if summary!=self._last_summary and not self.service.closed:
            async with self.service.lock:
                self.service.state['diagnostics'].update(summary)
                self.service._publish()
            self._last_summary=summary

    async def _loop(self):
        try:
            while not self.stopping:
                try:
                    await self.flush()
                    for dest in self.config['destinations']:
                        previous=self.delivery_tasks.get(dest['id'])
                        if self.storage_ready and dest['enabled'] and (previous is None or previous.done()):
                            self.delivery_tasks[dest['id']]=asyncio.create_task(self.deliver(copy.deepcopy(dest)))
                    await self.publish()
                except Exception:
                    # Diagnostics must not terminate its loop (or a chat) when
                    # storage disappears temporarily. Try again next cycle.
                    self._storage_failed()
                await asyncio.sleep(2)
        except asyncio.CancelledError: pass

    async def configure(self,cfg):
        cfg=validate_config(cfg)
        previous=self.config
        self.configuring=True;self.policy_generation+=1
        try:
            current={d['id']:d for d in cfg['destinations'] if cfg['enabled'] and d['enabled']}
            affected={d['id'] for d in previous['destinations']
                      if d['id'] not in current or route_revision(current[d['id']])!=route_revision(d)}
            tasks=[task for identity,task in self.delivery_tasks.items() if identity in affected and not task.done()]
            # Cancelling while the async client waits for auth prevents a later
            # HTTP send. Only a request already issued may remain in flight.
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.config=cfg
            self.route_since=time.time()
            # flush_lock lets any earlier persistence snapshot finish first;
            # this pass then cancels its revoked rows before save is confirmed.
            await self.flush()
            if self.storage_error:
                raise ValueError('Diagnostics storage is unavailable. The new settings were not saved; try again after fixing local storage.')
            temp=self.config_path.with_suffix('.tmp')
            temp.write_text(json.dumps(cfg,indent=2));temp.chmod(0o600);temp.replace(self.config_path)
        except Exception:
            self.config=previous;raise
        finally:
            self.configuring=False
        self.results={};self.configuration_error=False
        return {'status':'saved','message':'Saved. Only new selected events are routed. Pending deliveries for changed destinations are cancelled; accepted or in-flight data cannot be recalled.'}

    async def test(self,identity,generation=None):
        generation=self.policy_generation if generation is None else generation
        if generation!=self.policy_generation:return None
        dest=next((d for d in self.config['destinations'] if d['id']==identity),None)
        if not dest: raise ValueError('Save this destination before testing it.')
        def current():
            return generation==self.policy_generation and any(d['id']==dest['id'] and route_revision(d)==route_revision(dest) for d in self.config['destinations'])
        try:
            client=self._client(dest)
            # Sync client avoids blocking the main loop during Entra credential lookup.
            identity_result=await asyncio.to_thread(lambda: asyncio.run(client.whoami()))
            if not identity_result.get('contributor_id'): raise ValueError('The destination did not authenticate this caller.')
            if not current():return None
            payload=build_event_payload('diagnostics:probe',dest['workspace'],{'session_id':'probe-'+str(uuid.uuid4()),'synthetic':True,'timestamp':datetime.now(timezone.utc).isoformat()})
            await client.ingest(payload)
            result={'phase':'ready','message':'Authenticated; synthetic event accepted for indexing.','at':time.time()}
        except Exception as exc:
            error=self._failure(exc);result={'phase':'error','message':f"Connection test failed: {error['type']}"+(f" (HTTP {error['statusCode']})" if error.get('statusCode') else ''),'error':error,'at':time.time()}
        if not current():return None
        self.results[dest['id']]=result
        return result

    def read(self,*,sessionId=None,stream='*',before=None,limit=30):
        clauses=['stream GLOB ?'];params=[stream]
        if sessionId: clauses.append('session=?');params.append(sessionId)
        if before is not None: clauses.append('seq<?');params.append(before)
        try:
            with self._db() as db:
                rows=db.execute('SELECT * FROM records WHERE '+' AND '.join(clauses)+' ORDER BY seq DESC LIMIT ?',(*params,limit+1)).fetchall()
        except (OSError,sqlite3.Error):
            self._storage_failed()
            raise ValueError('Diagnostics storage is unavailable. Your conversations can continue; existing diagnostic files have been preserved.') from None
        items=[];size=0
        for row in rows[:limit]:
            item=dict(row);value=json.loads(item['data'])
            from .session_files import read_event
            item['data']=read_event(value)['data'] if '$event' in value else value
            if item['id'].startswith('capture-'):
                # Local CLI capture may contain full tool arguments/results.
                # Inspect only the user's chosen metadata/content projection.
                item['data']=clean(item['data'] if item['stream']=='conversation' else {k:v for k,v in item['data'].items() if k in META_KEYS})
            size+=len(json.dumps(item))
            if items and size>24000:break
            items.append(item)
        return {'items':items,'nextBefore':items[-1]['seq'] if items and len(rows)>len(items) else None,'format':'context-intelligence','schemaVersion':'1.0.0'}

    async def command(self,action,args):
        if action=='diagnostics.configure': result=await self.configure(args['config'])
        elif action=='diagnostics.test':result=await self.test(args['id'])
        elif action=='diagnostics.records':
            await self.flush();result=await asyncio.to_thread(self.read,**args)
        elif action=='diagnostics.retry':
            def retry():
                with self._db() as db:
                    return db.execute("UPDATE deliveries SET status='pending',attempts=0,next_at=0,error=NULL WHERE destination=? AND status='failed'",(args['id'],)).rowcount
            try:changed=await asyncio.to_thread(retry)
            except (OSError,sqlite3.Error):
                self._storage_failed()
                raise ValueError('Diagnostics storage is unavailable. No deliveries were queued for retry.') from None
            result={'phase':'ready','message':f'{changed} deliveries queued for retry.'}
        elif action=='diagnostics.environment':
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',args['name']):raise ValueError('Enter an environment variable name.')
            present=bool(os.environ.get(args['name']))
            result={'name':args['name'],'phase':'ready' if present else 'error','message':'Variable is available to the service.' if present else 'Variable is missing or empty in the service environment.'}
        else:raise ValueError('Unknown diagnostics action.')
        # Caller holds the service lock, so update here without publish().
        self.service.state['diagnostics'].update(await asyncio.to_thread(self._summary))
        self.service.state['diagnostics']['lastResult']={'action':action,**result}
        return result

    def runtime_event(self,kind,payload,session):
        try:self._runtime_event(kind,payload,session)
        except Exception:self.dropped+=1

    def _runtime_event(self,kind,payload,session):
        if kind in {'message.delta','assistant.delta','transcript.delta','session.naming','session.naming.progress'}:return
        if (kind=='execution.event' or kind.startswith(('tool.', 'provider.'))) and 'contextIntelligence' in session.get('runtimeReport',{}):
            # Kernel evidence comes from the mounted community hook. Do not
            # manufacture a second provider/tool event from a UI progress card.
            return
        data={k:v for k,v in payload.items() if k in META_KEYS}
        if isinstance(payload.get('error_type'),str):data['errorType']=payload['error_type'][:100]
        root=session.get('runtimeSessionId') or session['id']
        actual=payload.get('sessionId') or root
        data['appSessionId']=session['id']
        data['rootSessionId']=root
        stream='sessions'
        if kind=='execution.event':
            stream={'llm':'usage','tool':'tools','worker':'workers'}.get(payload.get('kind'),'sessions')
        elif kind.startswith('worker.'):stream='workers'
        elif 'tool' in kind:stream='tools'
        event=kind.replace('.',':',1)
        if kind=='execution.event':
            if payload.get('kind')=='llm':
                event={'running':'provider:request','retrying':'provider:retry','completed':'llm:response'}.get(payload.get('phase'),'llm:error')
                usage=data.get('usage') or {}
                data['usage']={**usage,**{dest:usage[src] for src,dest in [('inputTokens','input_tokens'),('outputTokens','output_tokens'),('totalTokens','total_tokens'),('cacheReadTokens','cache_read_input_tokens'),('cacheWriteTokens','cache_creation_input_tokens'),('costUsd','cost_usd')] if src in usage}}
            elif payload.get('kind')=='tool':
                event={'running':'tool:pre','completed':'tool:post'}.get(payload.get('phase'),'tool:error')
                data.update(tool_name=str(payload.get('label','Tool'))[:120],tool_call_id=payload.get('toolCallId'))
            elif payload.get('kind')=='worker':event='session:child'
        self.record(stream,{'event':event,'data':data},session_id=actual,workspace=session['workspace'],parent_id=root if actual!=root else None)

    async def close(self):
        self.stopping=True
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        for task in self.delivery_tasks.values():task.cancel()
        await asyncio.gather(*self.delivery_tasks.values(),return_exceptions=True)
        await self.flush()
