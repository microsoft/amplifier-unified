"""Conversation publishing policy over independent public publishing APIs."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from filelock import FileLock
from jsonschema import Draft202012Validator
from amplifier_publishing import Publisher, PublishingError
from amplifier_operations.quiescence import DurableIntakeFence
from amplifier_publishing.remote import canonical, digest, service_identity
from .targets import PublishingTargets
from .schemas import definitions as original_definitions

READS={'publishing.list','publishing.logs','publishing.status','publishing.receipt','publishing.release','publishing.target.list','publishing.command'}
def schema(fields,required=None):return {'type':'object','properties':fields,'required':list(fields) if required is None else required,'additionalProperties':False}
def string(n):return {'type':'string','maxLength':n}
def definitions():
    values=original_definitions(schema,string)
    scope={'sessionId':string(8192),'targetId':string(100)}
    values['publishing.command']=('Inspect a durable target configuration command without repeating its effects.',schema({'sessionId':string(8192),'commandId':string(200)}))
    values['publishing.receipt']=('Inspect the exact admitted request without replay, even when it is outside a visible page.',schema({**scope,'requestId':string(200)},['sessionId','requestId']))
    values['publishing.release']=('Read one immutable release and its complete manifest on the inspected target.',schema({**scope,'releaseId':string(100)},['sessionId','releaseId']))
    page={'limit':{'type':'integer','minimum':1,'maximum':100},'cursor':string(2048)}
    values['publishing.list'][1]['properties'].update(**page,collection={'enum':['sites','releases','receipts']},siteId=string(100))
    values['publishing.logs'][1]['properties'].update(page)
    for _,spec in values.values():spec['properties']['sessionId']['maxLength']=8192
    return {key:{'description':description,'schema':spec} for key,(description,spec) in values.items()}

def plain(value,sid,uri):
    if isinstance(value,list):return [plain(row,sid,uri) for row in value]
    if isinstance(value,dict):return {key:(uri if key=='sessionId' and row==sid else plain(row,sid,uri)) for key,row in value.items()}
    return value

class Owner:
    def __init__(self,config,host,notify,client_factory=None):
        root=Path(config['dataDir']).expanduser()
        if root.is_symlink():raise ValueError('Publishing owner storage cannot be a symlink')
        root=root.resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lease=FileLock(str(root/'owner.lock'));self.lease.acquire(timeout=0)
        self.intake=DurableIntakeFence(root/'intake.sqlite3');self.awaiting_idle=False;self.closed=False;self.jobs=set()
        self.db=sqlite3.connect(root/'admission.sqlite3');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY,uri TEXT UNIQUE NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS builds(session TEXT,request TEXT,signature TEXT,source TEXT,PRIMARY KEY(session,request))')
        self.db.execute('CREATE TABLE IF NOT EXISTS approvals(session TEXT,request TEXT,signature TEXT,body TEXT,PRIMARY KEY(session,request))');self.db.commit()
        self.db.execute('CREATE TABLE IF NOT EXISTS commands(session TEXT,id TEXT,signature TEXT,body TEXT,PRIMARY KEY(session,id))')
        self.db.execute("CREATE INDEX IF NOT EXISTS commands_state ON commands(json_extract(body,'$.state'))")
        for sid,identity,signature,body in self.db.execute("SELECT session,id,signature,body FROM commands WHERE json_extract(body,'$.state')='running'").fetchall():
            receipt=json.loads(body);receipt.update(state='unknown',error={'code':'unknown_outcome','message':'Target command outcome was not saved; no work was replayed'});self.db.execute('UPDATE commands SET body=? WHERE session=? AND id=?',(canonical(receipt),sid,identity))
        self.db.commit()
        self.root=root;self.host=host;self.notify=notify;self.schemas=definitions();self.lock=asyncio.Lock();self.store=None;self.captures={}
        self.targets=PublishingTargets(self.db,self.target,**({'client_factory':client_factory} if client_factory else {}))
    @staticmethod
    def target():return {'id':'loopback','kind':'loopback','label':'This server','accessPolicy':'loopback-only','detail':'URLs are reachable only on this server.'}
    def publisher(self):
        if self.store is None:self.store=Publisher(self.root/'publishing')
        return self.store
    def capture(self,identity):
        identity=service_identity(identity)
        if identity not in self.captures:
            while len(self.captures)>=4:self.captures.pop(next(iter(self.captures))).close()
            self.captures[identity]=Publisher(self.root/'captures'/identity)
        return self.captures[identity]
    def scope(self,uri,*,create=True):
        if not isinstance(uri,str) or not uri.startswith('ahp-session:/') or len(uri)>8192:raise ValueError('Trusted session URI required')
        sid='ahp-'+hashlib.sha256(uri.encode()).hexdigest()
        if create:self.db.execute('INSERT OR IGNORE INTO scopes VALUES (?,?)',(sid,uri));self.db.commit()
        prior=self.db.execute('SELECT uri FROM scopes WHERE id=?',(sid,)).fetchone()
        if prior and prior[0]!=uri:raise ValueError('Scope collision')
        return sid
    async def inspect(self,uri):
        session=await self.host('inspectSession',{'session':uri})
        if session.get('uri')!=uri:raise ValueError('Host returned another session')
        return session
    def source(self,session,value):
        cwd=session.get('executionDirectory') or session.get('workingDirectory')
        if not cwd or not Path(cwd).is_absolute():raise ValueError('Host did not supply a local execution directory')
        if session.get('relocationBlocked') or session.get('configurationBusy') or session.get('deleting'):raise ValueError('Wait for the selected workspace transition to finish')
        relative=Path(value)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Choose output inside the selected execution directory')
        workspace=Path(cwd).resolve(strict=True);source=workspace/relative;current=source
        while current!=workspace:
            if current.is_symlink():raise ValueError('Source paths cannot contain symbolic links')
            current=current.parent
        if not source.resolve().is_relative_to(workspace):raise ValueError('Source escapes execution directory')
        return source
    def build_source(self,sid,args,session,store):
        signature=canonical({k:v for k,v in args.items() if k!='targetId'});rid=args['requestId']
        previous=self.db.execute('SELECT signature,source FROM builds WHERE session=? AND request=?',(sid,rid)).fetchone()
        if previous:
            if previous[0]!=signature:raise PublishingError('request_conflict','Build request changed')
            if not store.receipt(rid,sid):raise PublishingError('unknown_outcome','Build admission exists without a receipt; capture will not be replayed')
            return Path(previous[1])
        if store.receipt(rid,sid):raise PublishingError('request_conflict','Request belongs to another operation')
        source=self.source(session,args['sourcePath']);self.db.execute('INSERT INTO builds VALUES (?,?,?,?)',(sid,rid,signature,str(source)));self.db.commit();return source
    async def approve(self,uri,sid,operation,args,params):
        if operation not in {'publishing.deploy','publishing.rollback'}:return
        signature=digest({'operation':operation,'args':args});rid=args['requestId']
        previous=self.db.execute('SELECT signature FROM approvals WHERE session=? AND request=?',(sid,rid)).fetchone()
        if previous:
            if previous[0]!=signature:raise PublishingError('request_conflict','Approved publication arguments changed')
            return
        if params.get('origin')=='ui':evidence={'approved':True,'kind':'explicit-ui','commandId':params['commandId'],'clientId':params.get('clientId')}
        else:
            evidence=await self.host('authorizePublication',{'session':uri,'operation':operation,'args':plain(args,sid,uri),'commandId':params['commandId'],'clientId':params.get('clientId')})
            if not isinstance(evidence,dict) or evidence.get('approved') is not True or not isinstance(evidence.get('approvalId'),str) or not evidence['approvalId']:raise PublishingError('approval_required','Explicit publication approval was not granted')
        self.db.execute('INSERT INTO approvals VALUES (?,?,?,?)',(sid,rid,signature,canonical(evidence)));self.db.commit()
    async def receipt(self,sid,rid,target):
        binding=self.targets.lookup_request(sid,rid)
        if binding and binding['targetId']!=target['id']:raise PublishingError('request_conflict','Request belongs to another target')
        if target['id']=='loopback':
            receipt=await asyncio.to_thread(self.publisher().receipt,rid,sid)
            if receipt:return receipt
            build=self.db.execute('SELECT signature FROM builds WHERE session=? AND request=?',(sid,rid)).fetchone()
            if build:
                args=json.loads(build[0]);return {'id':digest([sid,rid]),'requestId':rid,'sessionId':sid,'siteId':args['siteId'],'action':'build','state':'unknown','error':{'code':'unknown_outcome','message':'Capture admission exists without a conclusive receipt; no work was replayed'}}
            if binding:return {**self.targets._receipt(binding),'state':'unknown','error':{'code':'unknown_outcome','message':'Request was bound without a publisher receipt; explicit exact retry may inspect admission'}}
            return None
        if binding:
            if binding['state'] in {'unknown','running'}:
                try:await self.targets.reconcile(sid,rid)
                except PublishingError:pass
                binding=self.targets.lookup_request(sid,rid)
            try:self.targets._validate_cached_result(binding)
            except PublishingError:binding=self.targets.lookup_request(sid,rid)
            return self.targets._receipt(binding)
        client=self.targets._client(target);receipt=await asyncio.to_thread(client.request,{'method':'receipt','sessionId':sid,'requestId':rid});client.validate_result('receipt',receipt);return receipt
    async def page(self,sid,target,collection,limit=25,cursor=None,site=None):
        if target['id']=='loopback':page=await asyncio.to_thread(self.publisher().page,sid,collection=collection,limit=limit,cursor=cursor,site_id=site)
        else:page=await asyncio.to_thread(self.targets._client(target).request,{'method':'page','sessionId':sid,'collection':collection,'limit':limit,**({'cursor':cursor} if cursor else {}),**({'siteId':site} if site else {})})
        if collection!='receipts':return page
        # Merge bounded service page with locally admitted requests absent from its ledger.
        after=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))[1] if cursor else ''
        query="SELECT request FROM publishing_target_requests WHERE session=? AND json_extract(body,'$.targetId')=? AND request>?";values=[sid,target['id'],after]
        if site:query+=" AND json_extract(body,'$.siteId')=?";values.append(site)
        rows=self.db.execute(query+' ORDER BY request LIMIT ?',(*values,limit+1)).fetchall();known={row['requestId']:row for row in page['items']};ids=sorted(set(known)|{row[0] for row in rows});selected=ids[:limit];items=[]
        for rid in selected:
            row=await self.receipt(sid,rid,target) if self.targets.lookup_request(sid,rid) else known[rid]
            if target['id']!='loopback' and rid in known and not self.targets.lookup_request(sid,rid):self.targets._client(target).validate_result('receipt',row,summary=True)
            if row:items.append(Publisher.summary(row))
        more=len(ids)>limit or bool(page['nextCursor'])
        next_cursor=base64.urlsafe_b64encode(canonical([[sid,collection,site],selected[-1]]).encode()).decode().rstrip('=') if more and selected else None
        return {'items':items,'nextCursor':next_cursor,'collection':collection}
    async def read(self,sid,operation,args):
        target=self.targets.resolve(sid,args.get('targetId'));remote=target['id']!='loopback'
        if operation=='publishing.receipt':return await self.receipt(sid,args['requestId'],target)
        if operation=='publishing.release':
            return await asyncio.to_thread(self.targets._client(target).request,{'method':'release','sessionId':sid,'releaseId':args['releaseId']}) if remote else await asyncio.to_thread(self.publisher().release,args['releaseId'],sid)
        if operation=='publishing.status':
            return await asyncio.to_thread(self.targets._client(target).request,{'method':'status','sessionId':sid,'siteId':args['siteId']}) if remote else await asyncio.to_thread(self.publisher().status,args['siteId'],sid)
        collection='receipts' if operation=='publishing.logs' else args.get('collection')
        if collection:return {**await self.page(sid,target,collection,args.get('limit',25),args.get('cursor'),args.get('siteId')),'target':target}
        result={**self.targets.list(sid),'target':target,'pages':{}}
        for name in ('sites','releases','receipts'):
            page=await self.page(sid,target,name,args.get('limit',25),site=args.get('siteId'));result[name]=page['items'];result['pages'][name]={'nextCursor':page['nextCursor']}
        return result
    def command_receipt(self,sid,identity):
        row=self.db.execute('SELECT body FROM commands WHERE session=? AND id=?',(sid,identity)).fetchone()
        return json.loads(row[0]) if row else None
    async def target_command(self,sid,name,args,params):
        identity=params.get('commandId')
        if not isinstance(identity,str) or not 1<=len(identity)<=200:raise ValueError('Bounded stable commandId required')
        signature=digest({'operation':name,'args':args,'origin':params['origin']})
        row=self.db.execute('SELECT signature,body FROM commands WHERE session=? AND id=?',(sid,identity)).fetchone()
        if row:
            if row[0]!=signature:raise PublishingError('request_conflict','Target commandId was already used with different arguments or actor')
            receipt=json.loads(row[1])
        else:
            receipt={'commandId':identity,'sessionId':sid,'operation':name,'state':'running','result':None,'error':None}
            self.db.execute('INSERT INTO commands VALUES (?,?,?,?)',(sid,identity,signature,canonical(receipt)));self.db.commit()
            try:receipt.update(state='succeeded',result=await self.targets.dispatch(name,args))
            except Exception as exc:
                code=getattr(exc,'code','invalid_argument');receipt.update(state='unknown' if code=='unknown_outcome' else 'failed',error={'code':code,'message':str(exc)[:2000]})
            self.db.execute('UPDATE commands SET body=? WHERE session=? AND id=?',(canonical(receipt),sid,identity));self.db.commit()
        if receipt['state']=='succeeded':return receipt['result']
        error=receipt.get('error') or {'code':'unknown_outcome','message':'Target command is not settled; no work was replayed'}
        raise PublishingError('unknown_outcome' if receipt['state'] in {'running','unknown'} else error['code'],error['message'],receipt=receipt)

    async def dispatch(self,uri,sid,name,args,params):
        if name=='publishing.command':return self.command_receipt(sid,args['commandId'])
        if name=='publishing.target.list':return await self.targets.dispatch(name,args)
        if name.startswith('publishing.target.'):return await self.target_command(sid,name,args,params)
        if name in READS:return await self.read(sid,name,args)
        await self.approve(uri,sid,name,args,params)
        binding=self.targets.bind_request(sid,name,args)
        if binding['targetId']!='loopback':
            if name!='publishing.build':return await self.targets.dispatch_remote(name,args,binding)
            handled,result=await self.targets.resume_request(binding)
            if handled:return result
            try:
                store=self.capture(binding['serviceId']);session=params.get('workspace') or await self.inspect(uri);source=self.build_source(sid,args,session,store)
                release=await asyncio.to_thread(store.build,source,site_id=args['siteId'],session_id=sid,request_id=args['requestId'])
                exported=await asyncio.to_thread(store.export_release,release['id'],sid)
            except ValueError as exc:self.targets.capture_failed(binding,exc);raise
            return await self.targets.import_release(args,exported,binding)
        store=self.publisher();kw={'session_id':sid,'request_id':args['requestId']}
        if name!='publishing.build' and self.db.execute('SELECT 1 FROM builds WHERE session=? AND request=?',(sid,args['requestId'])).fetchone():raise PublishingError('request_conflict','Request belongs to a captured build')
        if name=='publishing.build':
            source=self.build_source(sid,args,params.get('workspace') or await self.inspect(uri),store);return await asyncio.to_thread(store.build,source,site_id=args['siteId'],**kw)
        if name=='publishing.review':return await asyncio.to_thread(store.review,args['releaseId'],note=args['note'],**kw)
        if name=='publishing.preview':return await asyncio.to_thread(store.preview,args['releaseId'],**kw)
        if name in {'publishing.deploy','publishing.rollback'}:return await asyncio.to_thread(getattr(store,name.split('.')[-1]),args['releaseId'],site_id=args['siteId'],expected_revision=args['expectedRevision'],**kw)
        if name in {'publishing.stop','publishing.remove'}:return await asyncio.to_thread(getattr(store,name.split('.')[-1]),args['siteId'],expected_revision=args['expectedRevision'],**kw)
        raise ValueError('Unknown publishing action')
    def listener_count(self):
        stores=([self.store] if self.store else [])+list(self.captures.values())
        return sum(store.inspect_lifetime()['listeners'] for store in stores)
    async def maybe_idle(self):
        if self.awaiting_idle and not self.closed and not self.intake.calls and not self.listener_count():
            self.awaiting_idle=False
            await self.notify('owner/idle',{})
    async def request(self,method,params):
        if method=='quiescence.acquire':
            # Calls include queued work and threads until actual completion.
            listeners=0 if self.intake.calls else self.listener_count()
            value=self.intake.acquire(params,pending=listeners)
            if not value['acquired']:
                self.awaiting_idle=True
                if listeners:value['reason']='Local publishing listeners are still serving; explicitly stop their sites before restart'
            return value
        if method=='quiescence.release':return self.intake.release(params)
        if method=='quiescence.inspect':return {'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'activeRequests':self.intake.calls,'listeners':None if self.intake.calls else self.listener_count()}
        # Other nominal reads reconcile remote receipts or refresh listener
        # projections. Only exact configuration-command reads are passive here.
        passive=method in {'initialize','actions'} or method=='action' and params.get('operation')=='publishing.command'
        if self.closed:raise ValueError('Publishing owner is closing')
        if self.intake.fence and not passive:raise ValueError('Publishing intake is closed; no operation was admitted')
        if not passive:self.intake.calls+=1
        async def run():
            try:return await self._request(method,params)
            finally:
                if not passive:
                    self.intake.calls-=1
                    await self.maybe_idle()
        task=asyncio.create_task(run());self.jobs.add(task)
        try:
            # Cancelling the RPC waiter cannot stop a running worker thread.
            # Keep the operation owned and joined until its receipt settles.
            try:return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.shield(task)
                raise
        finally:self.jobs.discard(task) if task.done() else task.add_done_callback(self.jobs.discard)
    async def _request(self,method,params):
        if method=='initialize':return {'protocolVersion':1}
        if method=='actions':return self.schemas
        if method not in {'action','snapshot'}:raise ValueError('Unknown publishing owner method')
        uri=params['session'];await self.inspect(uri);sid=self.scope(uri,create=not (method=='action' and params.get('operation')=='publishing.command'))
        async with self.lock:
            if method=='snapshot':return {'publishing':{uri:plain(await self.read(sid,'publishing.list',{'limit':10}),sid,uri)}}
            name=params['operation'];spec=self.schemas.get(name)
            if not spec:raise ValueError('Unknown publishing action')
            args=params.get('args',{})
            if args.get('sessionId',uri)!=uri:raise ValueError('Session argument differs from trusted context')
            args={**args,'sessionId':uri};Draft202012Validator(spec['schema']).validate(args)
            if params.get('origin') not in {'ui','agent'}:raise ValueError('Trusted origin required')
            if name not in READS and not params.get('commandId'):raise ValueError('Stable commandId required')
            args['sessionId']=sid
            try:return plain(await self.dispatch(uri,sid,name,args,params),sid,uri)
            except PublishingError as exc:
                if exc.receipt:exc.receipt=plain(exc.receipt,sid,uri)
                raise
            finally:
                if name not in READS:await self.notify('owner/changed',{'session':uri})
    async def close(self):
        if self.closed:return
        self.closed=True
        await asyncio.gather(*list(self.jobs),return_exceptions=True)
        async with self.lock:
            if self.store:await asyncio.to_thread(self.store.close)
            for store in self.captures.values():await asyncio.to_thread(store.close)
            self.db.close();self.intake.close();self.lease.release()
