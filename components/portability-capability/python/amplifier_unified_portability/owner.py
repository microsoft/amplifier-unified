from .retention import selected, result, exists, managed_selected, add_protection
"""Durable transfer coordination. No global application object or native imports."""
import asyncio,base64,hashlib,json,os,sqlite3,stat
from pathlib import Path
from filelock import FileLock
from amplifier_operations.quiescence import DurableIntakeFence
from jsonschema import Draft202012Validator
from amplifier_portability.protocol import TransferNode,encoded
from amplifier_portability.capsule import capture_workspace,restore_workspace,read_capsule,write_capsule
from amplifier_portability.evidence import verify,decode_body,MAX_OWNER_BYTES
from amplifier_worktrees.git import snapshot,digest
from .schemas import definitions as legacy
from .payloads import ResourcePayloads, CAPABILITIES

def schema(fields,required=None):return {'type':'object','properties':fields,'required':list(fields) if required is None else required,'additionalProperties':False}
def string(n):return {'type':'string','maxLength':n}
def definitions():
    rows=legacy(schema,string)
    for _,spec in rows.values():
        if 'sessionId' in spec['properties']:spec['properties']['sessionId']['maxLength']=8192
    for name in ('portability.activate','portability.discard','portability.evidence'):
        rows[name][1]['required']=[key for key in rows[name][1]['required'] if key!='sessionId']
    rows['portability.inspect'][1]['properties'].update(limit={'type':'integer','minimum':1,'maximum':100},cursor=string(2048))
    rows['portability.review']=('Review one authenticated signed capsule, including exact omissions, before staging or source release.',schema({'sessionId':string(8192),'path':string(4000),'id':string(100)},[]))
    for name in ('portability.stage','portability.release'):
        rows[name][1]['properties']['reviewedCapsuleHash']={'type':'string','pattern':'^[a-f0-9]{64}$'}
        rows[name][1]['required'].append('reviewedCapsuleHash')
    rows['portability.receipt']=('Inspect one exact transfer receipt without replay.',schema({'sessionId':string(8192),'id':string(100)},['id']))
    rows['portability.reconcile']=('Inspect committed destination activation and recover host adoption using its original admission; never repeat a probe or native write.',schema({'id':string(100)}))
    rows['portability.command']=('Inspect the original capability command after a lost response.',schema({'commandId':string(200),'sessionId':string(8192)},['commandId']))
    rows['portability.evidence'][1]['properties']['section']={'type':'string','maxLength':100}
    rows['portability.export'][1]['properties']['includeResourcePayloads']={'type':'boolean'}
    rows['portability.stage'][1]['properties']['payloadDirectory']=string(4000)
    return {name:{'description':description,'schema':spec} for name,(description,spec) in rows.items()}

READ_ACTIONS={'portability.inspect','portability.review','portability.command','portability.receipt','portability.evidence'}

class IntakeHeld(ValueError):
    executed=False
    code='quiescence_fenced'

def validate_storage(path):
    """Adapter commands and bindings are authority, not reconstructible indexes."""
    if not any(os.path.lexists(str(path)+suffix) for suffix in ('','-wal','-shm','-journal')):return
    if not path.is_file() or any(os.path.lexists(str(path)+suffix) and not stat.S_ISREG(os.lstat(str(path)+suffix).st_mode) for suffix in ('','-wal','-shm','-journal')):raise ValueError('Portability adapter storage requires inspection; original commands and bindings must be preserved')
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            for table,columns in {'commands':'scope,id,signature,body','bindings':'transfer,uri,native,cwd,engine'}.items():
                if db.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()!=('table',):raise ValueError('Required adapter table is unavailable')
                db.execute(f'SELECT {columns} FROM {table} LIMIT 0')
    except (sqlite3.DatabaseError,ValueError):
        raise ValueError('Portability adapter storage requires inspection; original commands and bindings must be preserved') from None

class Owner:
    def __init__(self,config,host,notify):
        self.config=config;self.host=host;self.notify=notify;self.requests=set();self.closing=False
        directory=Path(config['dataDir']).resolve();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lease=FileLock(str(directory/'owner.lock'));self.lease.acquire(timeout=0)
        try:
            validate_storage(directory/'owner.sqlite3')
            self.node=TransferNode(directory,config.get('label','Amplifier Unified'));self.node.recover()
            self.intake=DurableIntakeFence(directory/'intake.sqlite')
            self.db=sqlite3.connect(self.node.directory/'owner.sqlite3');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('CREATE TABLE IF NOT EXISTS commands(scope TEXT,id TEXT,signature TEXT,body TEXT,PRIMARY KEY(scope,id)); CREATE TABLE IF NOT EXISTS bindings(transfer TEXT PRIMARY KEY,uri TEXT,native TEXT,cwd TEXT,engine TEXT); CREATE INDEX IF NOT EXISTS managed_bindings_cwd ON bindings(cwd);CREATE INDEX IF NOT EXISTS bindings_uri ON bindings(uri,transfer);')
            self.schemas=definitions();self.lock=asyncio.Lock();self.payloads=ResourcePayloads(self)
            self.roots=[Path(p).resolve(strict=True) for p in config['workspaceRoots']]
            self.exchange=Path(config['exchangeDir']).resolve();self.exchange.mkdir(parents=True,exist_ok=True,mode=0o700)
            self.stages=Path(config['stageDir']).resolve();self.stages.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not any(self.stages.is_relative_to(root) for root in self.roots):raise ValueError('Stage directory is outside configured workspace roots')
            self.db.execute("CREATE INDEX IF NOT EXISTS retention_commands ON commands(scope,json_extract(body,'$.state'))")
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            if hasattr(self,'intake'):self.intake.close()
            self.lease.release()
            raise

    def local(self,value,*,exchange=False):
        path=Path(value)
        if not path.is_absolute():raise ValueError('Absolute configured local path required')
        for part in (path,*path.parents):
            if part.is_symlink():raise ValueError('Transfer paths cannot traverse symbolic links')
        resolved=path.resolve(strict=True);roots=[self.exchange] if exchange else self.roots
        if not any(resolved.is_relative_to(root) for root in roots):raise ValueError('Transfer path is outside configured authority roots')
        return resolved
    async def inspected(self,uri):
        value=await self.host('inspectSession',{'session':uri})
        if value.get('uri')!=uri or not value.get('nativeSessionId'):raise ValueError('Selected host must expose exact native session identity')
        return value
    def bind(self,identity,uri,sid,cwd,engine):
        prior=self.db.execute('SELECT uri,native,cwd,engine FROM bindings WHERE transfer=?',(identity,)).fetchone()
        value=(uri,sid,str(cwd),engine)
        if prior and tuple(prior)!=value:raise ValueError('Transfer binding changed; inspect original receipt')
        self.db.execute('INSERT OR IGNORE INTO bindings VALUES(?,?,?,?,?)',(identity,*value));self.db.commit()
    def binding(self,identity,scope=None):
        row=self.db.execute('SELECT uri,native,cwd,engine FROM bindings WHERE transfer=?',(identity,)).fetchone()
        if not row or scope not in (None,'host',row[0]):raise ValueError('Transfer is unavailable in this authorized scope')
        return dict(zip(('session','nativeSessionId','historyHome','engineId'),row))
    async def native(self,binding,operation,transfer,**args):
        return await self.host('nativeTransfer',{**binding,'operation':operation,'args':{'transferId':transfer,**args}})
    def evidence(self,items,accept=False):
        if not isinstance(items,list) or len(items)>32:raise ValueError('Bounded explicit evidence owners required')
        if sum(row.get('bytes',MAX_OWNER_BYTES+1) for row in items)>MAX_OWNER_BYTES:raise ValueError('Aggregate owner evidence exceeds8MiB')
        owners=set()
        for row in items:
            verify(row,accept_partial=accept)
            if row['owner'] in owners:raise ValueError('Duplicate portable evidence owner')
            owners.add(row['owner'])
        return items
    def projection(self,row):
        binding=self.db.execute('SELECT uri FROM bindings WHERE transfer=?',(row['id'],)).fetchone()
        return {**row,'nativeSessionId':row['sessionId'],'sessionId':binding[0] if binding and binding[0] else row['sessionId']}
    async def inspect(self,scope,args):
        selected=await self.inspected(scope) if scope!='host' else None
        page=self.node.page(sid=selected['nativeSessionId'] if selected else None,cursor=args.get('cursor'),limit=args.get('limit',25))
        value={'host':self.node.identity,'peers':list(self.node.peers().values()),'receipts':[self.projection(row) for row in page['items']],'nextCursor':page['nextCursor'],'revision':page['revision']}
        capabilities=await self.payloads.capabilities()
        if capabilities:value['payloadCapabilities']=capabilities
        if selected:
            cwd=selected.get('executionDirectory') or selected.get('workingDirectory');self.local(cwd)
            try:
                value['source']=await asyncio.to_thread(lambda:snapshot(Path(cwd))[0])
                value['source']['expectedExecutionRevision']=selected['executionRevision']
            except ValueError as error:value['sourceError']=str(error)
            value['fenced']=self.node.fenced(selected['nativeSessionId'])
        return value
    def command(self,scope,identity):
        row=self.db.execute('SELECT signature,body FROM commands WHERE scope=? AND id=?',(scope,identity)).fetchone()
        if not row:return None
        value=json.loads(row[1]);return {**value,'state':'unknown' if value['state']=='running' else value['state'],'requestHash':row[0]}
    def save_command(self,scope,identity,signature,value):
        self.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?,?)',(scope,identity,signature,json.dumps(value)));self.db.commit()
    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            if exists(self.db,"SELECT 1 FROM commands WHERE scope IN (?,'host') AND json_extract(body,'$.state') IN ('running','unknown') LIMIT 1",(session,)):reasons.append('transfer-unsettled')
            bindings=self.db.execute('SELECT native FROM bindings WHERE uri=? LIMIT 102',(session,)).fetchall()
            if len(bindings)>101:return reasons+['transfer-coverage-overflow']
            for (native,) in bindings:
                # The index is derived; a pending canonical write is never repaired here.
                with sqlite3.connect(self.node.index.path.as_uri()+'?mode=ro',uri=True) as db:
                    self.node.index.require_ready(db)
                    if exists(db,'SELECT 1 FROM pending WHERE session=? LIMIT 1',(native,)):reasons.append('transfer-unsettled')
                    row=db.execute('SELECT phase FROM rows WHERE session=? ORDER BY generation DESC,created DESC,id DESC LIMIT 1',(native,)).fetchone()
                    if row and row[0] not in {'cancelled','discarded','active','released','rejected','expired'}:reasons.append('transfer-unsettled')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);root=args['allocation']['executionDirectory']
        hit=exists(self.db,'SELECT 1 FROM bindings WHERE cwd=? OR (cwd>=? AND cwd<?) LIMIT 1',(root,root+'/',root+'0'))
        if not hit:hit=any(exists(self.db,'SELECT 1 FROM bindings WHERE cwd=? LIMIT 1',('/'.join(root.split('/')[:i]) or '/',)) for i in range(1,len(root.split('/'))))
        return add_protection(base,args['sessions'],lambda s:['transfer-source'] if hit or exists(self.db,'SELECT 1 FROM bindings WHERE uri=? LIMIT 1',(s,)) else [])


    async def request(self,method,params):
        if method=='quiescence.retention':return self.retention_references(params)
        if method=='quiescence.managedFiles':return self.managed_references(params)
        if self.closing:raise IntakeHeld('Portability owner is closing')
        if method=='quiescence/inspect':return {'version':1,'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'calls':self.intake.calls,'background':self.intake.background}
        if method=='quiescence/acquire':return self.intake.acquire(params)
        if method=='quiescence/release':return self.intake.release(params)
        if self.intake.fence and method=='action' and params.get('operation') not in READ_ACTIONS:raise IntakeHeld('Portability owner intake is held; no transfer effect admitted')
        self.intake.calls+=1;request=asyncio.current_task();self.requests.add(request)
        effect=asyncio.create_task(self._request(method,params))
        try:return await asyncio.shield(effect)
        except asyncio.CancelledError:
            while not effect.done():
                try:await asyncio.shield(effect)
                except asyncio.CancelledError:continue
                except BaseException:break
            raise
        finally:
            self.requests.discard(request);self.intake.calls-=1
            if self.intake.calls==0:
                try:await self.notify('owner/idle',{})
                except Exception:pass

    async def _request(self,method,params):
        if method=='initialize':return {'protocolVersion':1,'quiescence':{'version':1,'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',None)==1 else {})}}
        if method=='payload/verify':return await self.payloads.verify(params)
        if method=='actions':return self.schemas
        if method=='snapshot':return await self.inspect(params.get('session','host'),{})
        if method!='action':raise ValueError('Unknown portability owner method')
        op=params.get('operation');args=params.get('args',{});scope=params.get('session','host');command=params.get('commandId')
        if op not in self.schemas:raise ValueError('Unknown portability operation')
        Draft202012Validator(self.schemas[op]['schema']).validate(args)
        if args.get('sessionId') and args['sessionId']!=scope:raise ValueError('Session selector disagrees with trusted scope')
        if op=='portability.inspect':return await self.inspect(scope,args)
        if op=='portability.review':return self.review(scope,args)
        if op=='portability.command':return {'receipt':self.command(scope,args['commandId'])}
        if op in {'portability.receipt','portability.evidence'}:
            binding=self.binding(args['id'],scope);row=self.node.get(args['id'])
            if op=='portability.receipt':return self.projection(row)
            package=read_capsule(Path(row.get('package') or row['destinationState']['package']))
            evidence=self.evidence(package['body']['payload'].get('evidence',[]),True);selected=next((item for item in evidence if item['owner']==args['section']),None)
            if selected is None:raise ValueError('Unknown evidence owner')
            text=selected['bodyJson'];offset=args.get('offset',0);limit=args.get('limit',4000)
            return {'text':text[offset:offset+limit],'nextOffset':offset+limit if offset+limit<len(text) else None,'executionAuthority':False,'sha256':selected['sha256']}
        if not isinstance(command,str) or not 1<=len(command)<=200:raise ValueError('Stable capability commandId required')
        signature=hashlib.sha256(encoded({'operation':op,'args':args})).hexdigest()
        async with self.lock:
            previous=self.command(scope,command)
            if previous:
                if previous['requestHash']!=signature:raise ValueError('Transfer command already has different arguments')
                return {**previous.get('result',{}),'commandReceipt':previous,'replayed':False}
            authorization=await self.host('authorizeTransfer',{'session':scope,'operation':op,'args':args,'commandId':command,'origin':params.get('origin'),'clientId':params.get('clientId')})
            if not isinstance(authorization,dict) or authorization.get('approved') is not True:raise ValueError('Explicit reviewed transfer approval is required')
            self.save_command(scope,command,signature,{'state':'running','commandId':command,'operation':op,'replayed':False})
            try:
                result=await getattr(self,op.split('.')[1])(scope,args,command)
                self.save_command(scope,command,signature,{'state':'completed','commandId':command,'operation':op,'result':result,'replayed':False})
                await self.notify('owner/changed',{'session':scope});return result
            except BaseException as error:
                self.save_command(scope,command,signature,{'state':'unknown','commandId':command,'operation':op,'detail':str(error)[:1000],'replayed':False});raise
    async def export(self,scope,args,command):
        selected=await self.inspected(scope);cwd=self.local(selected.get('executionDirectory') or selected['workingDirectory'])
        history=selected.get('historyHome') or selected['workingDirectory'];sid=selected['nativeSessionId']
        if args.get('includeResourcePayloads'):await self.payloads.negotiate(args['destination'])
        # Git review happens before durable host/native admission is fenced.
        workspace=await asyncio.to_thread(capture_workspace,cwd,args['sourceRevision'],args['mode'])
        row=self.node.begin(sid,args['destination'],command,args);identity=row['id']
        self.bind(identity,scope,sid,history,selected['engineId']);binding=self.binding(identity)
        try:
            await self.host('beginTransfer',{'session':scope,'commandId':command,'transferId':identity,'destination':row['destination'],'expectedExecutionRevision':args['expectedExecutionRevision']})
            await self.native(binding,'source.fence',identity)
            capture=await self.native(binding,'source.capture',identity)
            exported=await self.host('exportTransferEvidence',{'session':scope,'transferId':identity,'limitBytes':MAX_OWNER_BYTES})
            evidence=self.evidence(exported['evidence'],True)
            resource_payload=None;payload_directory=None
            if args.get('includeResourcePayloads'):resource_payload,payload_directory=await self.payloads.export(scope,identity,evidence)
            omissions=[*capture.get('omissions',[]),*exported.get('omissions',[])]
            files={}
            for name,metadata in capture['files'].items():
                chunks=[];offset=0
                while True:
                    value=await self.native(binding,'source.read',identity,name=name,offset=offset,limit=65536);chunks.append(base64.b64decode(value['data'],validate=True))
                    if value['nextOffset'] is None:break
                    offset=value['nextOffset']
                raw=b''.join(chunks)
                if len(raw)!=metadata['bytes'] or hashlib.sha256(raw).hexdigest()!=metadata['sha256']:raise ValueError('Native capture changed during transfer')
                files[name]={**metadata,'data':base64.b64encode(raw).decode()}
            if (await asyncio.to_thread(capture_workspace,cwd,args['sourceRevision'],args['mode']))!=workspace:raise ValueError('Source workspace changed during capture')
            row=self.node.prepared(identity,{**({'resourcePayload':resource_payload} if resource_payload else {}),'version':1,'originSession':scope,'nativeIdentity':sid,'native':files,'intent':capture['intent'],'origin':capture['origin'],'workspace':workspace,'evidence':evidence,'omissions':omissions,'session':{'title':selected.get('title','Imported conversation')},'engineId':selected['engineId']})
            if payload_directory:row['payloadDirectory']=payload_directory;self.node.save(row)
            return {**self.projection(row),'review':self.review(scope,{'id':identity})}
        except BaseException:self.node.unknown(identity);raise
    def review_package(self,package,local_row=None):
        if local_row is None:
            body=self.node.verify(package,kind='capsule')
            if body.get('source')!=package.get('signer') or body.get('destination')!=self.node.identity['id']:raise ValueError('Signed capsule belongs to different execution hosts')
        else:
            if package.get('signer')!=self.node.identity['id']:raise ValueError('Local capsule signer changed')
            try:self.node.key.public_key().verify(base64.b64decode(package['signature'],validate=True),encoded(package['body']))
            except Exception as error:raise ValueError('Local signed capsule authentication failed') from error
            body=package['body']
            if body.get('kind')!='capsule' or body.get('source')!=self.node.identity['id'] or body.get('id')!=local_row['id'] or digest(encoded(body))!=local_row['capsuleHash']:raise ValueError('Local capsule changed after preparation')
        payload=body['payload'];evidence=self.evidence(payload.get('evidence',[]),True)
        result={'version':1,'transferId':body['id'],'capsuleHash':digest(encoded(body)),'source':body['source'],'destination':body['destination'],'nativeSessionId':body['sessionId'],'omissions':payload.get('omissions',[]),'executionAuthority':False,
            'evidence':[{key:item[key] for key in ('owner','version','revision','bytes','sha256','disposition','omissions','executionAuthority')} for item in evidence],
            'nativeFiles':[{'name':name,'bytes':item['bytes'],'sha256':item['sha256']} for name,item in payload['native'].items()],
            'workspace':{key:payload['workspace'][key] for key in ('head','sourceRevision','mode')}}
        if payload.get('resourcePayload'):
            value=payload['resourcePayload'];plan=value['plan'];metadata=__import__('amplifier_portability.payloads',fromlist=['PayloadStore']).PayloadStore.verify_plan(plan,accept_partial=True)
            result['sourceSelection']=value['sourceSelection']
            result['resourcePayload']={**{key:metadata[key] for key in ('owner','revision','items','bytes','omitted','disposition','executionAuthority')},'planHash':plan['sha256'],'evidenceHash':value['evidenceHash']}
        if len(encoded(result))>256*1024:raise ValueError('Signed review exceeds256KiB; no omissions were silently truncated')
        return result
    def review(self,scope,args):
        if bool(args.get('path'))==bool(args.get('id')):raise ValueError('Review requires exactly one capsule path or transfer identity')
        if args.get('path'):return self.review_package(read_capsule(self.local(args['path'],exchange=True)))
        self.binding(args['id'],scope);row=self.node.get(args['id'])
        package=read_capsule(Path(row.get('package') or row['destinationState']['package']))
        return self.review_package(package,row if row['direction']=='outgoing' else None)
    async def stage(self,scope,args,command):
        package=read_capsule(self.local(args['path'],exchange=True));body=self.node.verify(package,kind='capsule');payload=body['payload'];sid=body['sessionId']
        if payload['nativeIdentity']!=sid:raise ValueError('Signed native identity disagrees with transfer')
        review=self.review_package(package)
        if args['reviewedCapsuleHash']!=review['capsuleHash']:raise ValueError('Reviewed signed capsule changed; review exact contents before staging')
        evidence=self.evidence(payload.get('evidence',[]),True)
        if payload.get('resourcePayload') and not args.get('payloadDirectory'):raise ValueError('Signed resource payload requires explicit incoming sidecar; no fallback omissions')
        repository=self.local(args['repository']);target=self.stages/body['id'];row=self.node.receive(package,args)
        if row.get('duplicate'):return self.projection(row)
        try:
            restored=await asyncio.to_thread(restore_workspace,payload['workspace'],repository,target)
            self.bind(row['id'],None,sid,target,payload['engineId']);binding=self.binding(row['id'])
            local=self.node.directory/'packages'/(row['id']+'.json');write_capsule(local,package)
            await self.native(binding,'destination.fence',row['id'])
            policy=await self.native(binding,'destination.policy',row['id']);row.update(readinessPolicy=policy['policy'],readinessPolicyHash=policy['policyHash']);self.node.save(row)
            await self.host('stageTransferEvidence',{'transferId':row['id'],'nativeSessionId':sid,'sourceHost':row['source'],'evidence':evidence,'acceptPartial':True})
            if payload.get('resourcePayload'):
                row['payloadImport']=await self.payloads.stage(package,args['payloadDirectory']);row['payloadPlanHash']=payload['resourcePayload']['plan']['sha256'];self.node.save(row)
            checks=await self.native(binding,'destination.check',row['id'])
            row=self.node.ready(row['id'],{'workspace':str(target),'package':str(local),'nativeIdentity':sid,'engineId':payload['engineId'],'checkout':restored},checks)
            receipt=self.exchange/(row['id']+'.ready.json');write_capsule(receipt,row['readyReceipt']);row['receiptPath']=str(receipt);self.node.save(row);return self.projection(row)
        except BaseException:self.node.unknown(row['id']);raise
    async def release(self,scope,args,command):
        binding=self.binding(args['id'],scope);row=self.node.get(args['id'])
        review=self.review(scope,{'id':row['id']})
        if args['reviewedCapsuleHash']!=review['capsuleHash']:raise ValueError('Reviewed signed capsule changed; review exact contents before release')
        ready=read_capsule(self.local(args['path'],exchange=True))
        body=self.node.verify(ready,kind='ready',signer=row['destination']);self.node.match(row,body)
        if row['phase']=='released':return self.projection(row)
        await self.native(binding,'source.verify',row['id'])
        package=read_capsule(Path(row['package']));workspace=package['body']['payload']['workspace'];origin=package['body']['payload']['origin']
        await asyncio.to_thread(capture_workspace,self.local(origin['executionDirectory']),workspace['sourceRevision'],workspace['mode'])
        row=self.node.releasing(row['id'],ready,args['expectedRevision'],self.node.directory/'archives'/row['id'])
        await self.host('commitTransfer',{'session':binding['session'],'commandId':command,'transferId':row['id']})
        await self.native(binding,'source.commit',row['id'])
        row=self.node.release(row['id'],ready,row['revision']);receipt=self.exchange/(row['id']+'.release.json');write_capsule(receipt,row['releaseCertificate']);row['receiptPath']=str(receipt);self.node.save(row)
        return self.projection(row)
    async def authenticated_incoming(self,row,package):
        body=self.node.verify(package,kind='capsule',signer=row['source'])
        if row['direction']!='incoming' or body['source']!=package['signer'] or body['destination']!=self.node.identity['id']:
            raise ValueError('Stored capsule execution-host binding changed')
        self.node.match(row,{**body,'capsuleHash':digest(encoded(body))})
        if body['payload']['nativeIdentity']!=row['sessionId']:
            raise ValueError('Stored capsule native identity changed')
        payload=body['payload']
        self.evidence(payload.get('evidence',[]),True)
        if payload.get('resourcePayload'):
            _,value,binding=self.payloads.binding(package)
            if row.get('payloadPlanHash')!=value['plan']['sha256']:raise ValueError('Stored payload plan changed')
            await self.payloads.verify({**binding,'capsule':package,'plan':value['plan']})
        return payload

    async def activate(self,scope,args,command):
        binding=self.binding(args['id'],scope);row=self.node.get(args['id']);certificate=read_capsule(self.local(args['path'],exchange=True))
        self.node.activating(row['id'],certificate,args['expectedRevision']);row=self.node.get(row['id'])
        if row['phase']=='active':return await self.reconcile(scope,{'id':row['id']},command)
        row['activationCommandId']=command;self.node.save(row)
        try:
            package=read_capsule(Path(row['destinationState']['package']));payload=await self.authenticated_incoming(row,package);self.evidence(payload.get('evidence',[]),True)
            await self.native(binding,'destination.check',row['id'])
            await self.native(binding,'destination.install',row['id'])
            self.node.active(row['id']);receipt=await self.native(binding,'destination.activate',row['id'])
            return await self.reconcile(scope,{'id':row['id']},command)
        except BaseException:self.node.unknown(row['id']);raise
    async def reconcile(self,scope,args,command):
        binding=self.binding(args['id'],scope);row=self.node.get(args['id'])
        if row['phase']!='active' or not row.get('activationCommandId'):raise ValueError('No committed destination activation to reconcile')
        package=read_capsule(Path(row['destinationState']['package']));payload=await self.authenticated_incoming(row,package)
        proof=await self.native(binding,'inspect',row['id']);receipt=proof.get('receipt',{})
        if proof.get('fence') is not None or receipt.get('phase')!='activated':raise ValueError('Native activation remains unresolved; no effect repeated')
        body=self.node.verify(row['releaseCertificate'],kind='release',signer=row['source']);self.node.match(row,body)
        evidence=self.evidence(payload.get('evidence',[]),True)
        adopted=await self.host('adoptTransferredSession',{'session':payload['originSession'],'commandId':row['activationCommandId'],'transferId':row['id'],'engineId':binding['engineId'],'nativeSessionId':binding['nativeSessionId'],'historyHome':binding['historyHome'],'executionDirectory':binding['historyHome'],'source':row['source'],'destination':row['destination'],'releaseHash':receipt['releaseHash'],'title':payload.get('session',{}).get('title')})
        uri=adopted['uri'];self.db.execute('UPDATE bindings SET uri=? WHERE transfer=?',(uri,row['id']));self.db.commit()
        imported=await self.host('activateTransferEvidence',{'session':uri,'transferId':row['id'],'nativeSessionId':binding['nativeSessionId'],'sourceHost':row['source'],'releaseHash':receipt['releaseHash'],'evidence':evidence,**({'payloadPlanHash':payload['resourcePayload']['plan']['sha256']} if payload.get('resourcePayload') else {})})
        row['adoption']=adopted;row['evidenceImport']=imported;self.node.save(row)
        return {**self.projection(row),'reconciled':True,'inputsReplayed':False}
    async def cancel(self,scope,args,command):
        binding=self.binding(args['id'],scope);row=self.node.cancel(args['id'],args['expectedRevision'],args['evidence'])
        await self.native(binding,'source.cancel',row['id']);await self.host('cancelTransfer',{'session':binding['session'],'commandId':command,'transferId':row['id']})
        return self.projection(row)
    async def discard(self,scope,args,command):
        self.binding(args['id'],scope);certificate=read_capsule(self.local(args['path'],exchange=True));return self.projection(self.node.discard(args['id'],certificate,args['expectedRevision']))
    async def close(self):
        if self.closing:return
        self.closing=True
        if self.requests:await asyncio.gather(*list(self.requests),return_exceptions=True)
        self.intake.close();self.db.close();self.lease.release()
