"""Bounded explicit-target coordination; execution and catalogs stay with owners."""
import asyncio,hashlib,json,sqlite3,uuid
from pathlib import Path
from filelock import FileLock
from jsonschema import Draft202012Validator
from amplifier_operations.quiescence import DurableIntakeFence
from amplifier_operations.coordination import ChangeSignal,delivery,decode_cursor
ACTIVE={'working','running','starting','ready','queued','pending','stopping'}
ATTENTION={'error','failed','interrupted','cancelled','stopped','unknown'}
def string(n):return {'type':'string','minLength':1,'maxLength':n}
def schema(fields,required=None):return {'type':'object','properties':fields,'required':list(fields) if required is None else required,'additionalProperties':False}
def definitions():
    target={'sessionId':string(8192),'workerId':string(200)}
    return {name:{'description':description,'schema':shape} for name,(description,shape) in {
      'coordination.list':('List a bounded page of indexed conversations, or the actual saved workers of one explicit conversation. Never selects a chat or starts an agent.',schema({**target,'cursor':string(2048),'limit':{'type':'integer','minimum':1,'maximum':100}},[])),
      'coordination.wait':('Wait on at most eight explicit targets for stable report receipts or attention. Carry each nextCursor after consuming results. Missing coverage is explicit; never runs work.',schema({'targets':{'type':'array','minItems':1,'maxItems':8,'items':schema({**target,'afterCursor':string(2048)},['sessionId'])},'waitMs':{'type':'integer','minimum':0,'maximum':60000},'maxBytes':{'type':'integer','minimum':4096,'maximum':65536}},['targets'])),
      'coordination.followup':('Send an explicit follow-up to a conversation or resident persistent worker. Agents may target only their calling conversation and its workers. Preserves private client selection and drafts.',schema({**target,'text':string(100000)},['sessionId','text'])),
      'coordination.interrupt':('Request interruption of one explicit conversation or resident worker. Acceptance does not prove completion or undo effects.',schema(target,['sessionId'])),
      'coordination.command':('Read a durable coordination command after a lost response; never resends it.',schema({'commandId':string(200)})),
    }.items()}
class Owner:
    def __init__(self,config,host,notify):
        directory=Path(config['dataDir']).resolve();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lease=FileLock(str(directory/'owner.lock'));self.lease.acquire(timeout=0)
        self.intake=DurableIntakeFence(directory/'intake.sqlite3')
        self.db=sqlite3.connect(directory/'commands.sqlite3');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL');self.db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT,body TEXT)');self.db.commit()
        self.host=host;self.notify=notify;self.waits={};self.awaiting_idle=False;self.schemas=definitions();self.lock=asyncio.Lock()
    @staticmethod
    def identity(target):return json.dumps([target['sessionId'],target.get('workerId')],separators=(',',':'))
    async def snapshot(self,target,client,require_results=True):
        sid=target['sessionId'];wid=target.get('workerId');identity=self.identity(target)
        seq=decode_cursor(target['afterCursor'],identity)[0] if target.get('afterCursor') else 0
        parent=await self.host('readCoordinationSession',{'session':sid,'args':{'afterSequence':max(0,seq-1) if not wid else 0,'limit':32 if not wid and require_results else 1,'clientId':client}})
        if require_results and not parent.get('available',True) and not wid:raise ValueError('Conversation results unavailable: '+str(parent.get('coverage','not-indexed')))
        if wid:
            saved=await self.host('readCoordinationWorkers',{'session':sid,'args':{'workerId':wid,'afterSequence':max(0,seq-1),'resultLimit':32}})
            if not saved.get('available'):raise ValueError('Worker observations are not indexed; no runtime was started')
            item=saved['item'];results=saved['results'];latest=saved['latestSequence']
        else:item=parent;results=parent.get('results',[]);latest=parent.get('latestSequence',0)
        return self.format(target,parent,item,results,latest)
    def format(self,target,parent,item,results,latest):
        wid=target.get('workerId');identity=self.identity(target)
        status=item.get('status','unknown');approval=parent.get('approvalIds',[]);questions=parent.get('questionIds');task=parent.get('task') or {}
        omissions=parent.get('omissions',[]);attention_unknown=questions is None or not parent.get('attentionComplete',False)
        signal={'status':'waiting' if status in ACTIVE else status,'approvalIds':approval,'questionIds':questions,'taskStatus':task.get('status'),'taskRevision':task.get('revision'),'interruptionRevision':parent.get('interruptionRevision',0),'attentionUnknown':attention_unknown}
        return {'identity':identity,'target':{k:target[k] for k in ('sessionId','workerId') if k in target},'kind':'worker' if wid else 'conversation','title':item.get('agent') or item.get('title','Conversation'),'status':status,'runtimeSessionId':item.get('sessionId') if wid else parent.get('nativeSessionId'),'parentSessionId':item.get('parentSessionId'),'runId':item.get('runId'),'callId':item.get('callId'),'operationId':'worker:'+wid if wid else None,'taskId':item.get('taskId') if wid else task.get('id'),'taskQuestionIds':task.get('questionIds',[])[:64],'task':{k:task[k] for k in ('id','status','revision','questionIds') if k in task},'attention':bool(approval or questions or status in ATTENTION or task.get('status')=='blocked'),'attentionUnknown':attention_unknown,'approvalIds':approval[:64],'questionIds':questions[:64] if questions is not None else None,'omissions':omissions,'coverage':parent.get('coverage'),'attentionCoverage':parent.get('attentionCoverage'),'activeTurnId':parent.get('activeTurnId'),'canFollowup':bool(item.get('canFollowup',False)),'canInterrupt':bool(item.get('canInterrupt',False)),'results':results,'latestSequence':latest,'signal':signal,'wakeable':status not in ACTIVE or bool(approval or questions)}
    async def listing(self,args,client):
        if not args.get('sessionId'):
            page=await self.host('listCoordinationSessions',{'args':{k:v for k,v in args.items() if k in {'cursor','limit'}}|{'clientId':client}})
            return {**page,'items':[self.metadata(self.format({'sessionId':row['uri']},row,row,[],0)) for row in page.get('items',[])],'workersNotLoaded':True}
        sid=args['sessionId'];parent=await self.snapshot({'sessionId':sid},client,False)
        page=await self.host('readCoordinationWorkers',{'session':sid,'args':{k:v for k,v in args.items() if k in {'workerId','cursor','limit'}}})
        item=self.metadata(parent)
        workers=[page['item']] if 'item' in page else page.get('items',[])
        rows=[self.metadata(self.format({'sessionId':sid,'workerId':worker['workerId']},parent,worker,[],worker.get('latestSequence',0))) for worker in workers]
        return {'items':([item] if not args.get('cursor') and not args.get('workerId') else [])+rows,'nextCursor':page.get('nextCursor'),'workersAvailable':page.get('available',False),'coverage':'selected-native-worker-index'}
    @staticmethod
    def metadata(value):return {k:v for k,v in value.items() if k not in {'results','signal','wakeable','identity'}}
    async def refresh(self,token):
        entry=self.waits.get(token)
        if not entry:return
        async with entry['lock']:
            snapshots=[];errors=[];capacity=asyncio.Semaphore(4)
            async def selected(target):
                async with capacity:
                    try:snapshots.append(await asyncio.wait_for(self.snapshot(target,entry['client']),10))
                    except Exception as error:errors.append({'target':{k:target[k] for k in ('sessionId','workerId') if k in target},'error':str(error)[:1000] or type(error).__name__})
            await asyncio.gather(*(selected(target) for target in entry['targets']))
            snapshots.sort(key=lambda value:next(i for i,target in enumerate(entry['targets']) if value['identity']==self.identity(target)))
            entry.update(snapshots=snapshots,errors=errors);entry['signal'].notify()
    async def wait(self,args,client):
        targets=args['targets'];identities=[self.identity(t) for t in targets]
        if len(set(identities))!=len(identities):raise ValueError('Wait targets must be distinct')
        if len(self.waits)>=32:raise ValueError('Scoped wait capacity reached')
        token=str(uuid.uuid4());entry={'targets':targets,'client':client,'snapshots':[],'errors':[],'signal':ChangeSignal(),'lock':asyncio.Lock()};self.waits[token]=entry
        try:
            await self.host('watch',{'token':token,'sessions':list(dict.fromkeys(t['sessionId'] for t in targets)),'clientId':client})
            await self.refresh(token)
            cursors={self.identity(t):t.get('afterCursor') for t in targets};budget=args.get('maxBytes',32768)//len(targets)
            def read():
                values=[delivery(s,cursors[s['identity']],max_bytes=budget) for s in entry['snapshots']]
                return {'targets':values,'errors':entry['errors'],'changed':bool(entry['errors'] or any(v['changed'] for v in values))}
            return await entry['signal'].wait(read,wait_ms=args.get('waitMs',0))
        finally:
            self.waits.pop(token,None);await self.host('unwatch',{'token':token})
    def receipt(self,command):
        row=self.db.execute('SELECT signature,body FROM commands WHERE id=?',(command,)).fetchone()
        if not row:return None
        value=json.loads(row[1]);return {**value,'requestHash':row[0],**({'status':'unknown'} if value['status']=='dispatching' else {})}
    def save(self,command,signature,value):
        self.db.execute('INSERT OR REPLACE INTO commands VALUES(?,?,?)',(command,signature,json.dumps(value)));self.db.commit()
    async def request(self,method,params):
        if method=='quiescence.acquire':
            value=self.intake.acquire(params)
            if not value['acquired']:self.awaiting_idle=True
            return value
        if method=='quiescence.release':return self.intake.release(params)
        if method=='quiescence.inspect':return {'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'activeRequests':self.intake.calls}
        passive=method in {'initialize','actions','snapshot','changed'} or method=='action' and params.get('operation') in {'coordination.list','coordination.wait','coordination.command'}
        if self.intake.fence and not passive:raise ValueError('Coordination intake is closed; no new control was admitted')
        if not passive:self.intake.calls+=1
        try:return await self._request(method,params)
        finally:
            if not passive:
                self.intake.calls-=1
                if self.awaiting_idle and self.intake.calls==0:
                    self.awaiting_idle=False
                    await self.notify('owner/idle',{})
    async def _request(self,method,params):
        if method=='initialize':return {'protocolVersion':1,'quiescence':{'version':1,'heldIntake':True,'durableRelease':True,**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})}}
        if method=='actions':return self.schemas
        if method=='changed':await self.refresh(params['token']);return {}
        if method=='snapshot':return await self.listing({},params['clientId'])
        if method!='action':raise ValueError('Unknown coordination method')
        op=params['operation'];args=params.get('args',{})
        if op not in self.schemas:raise ValueError('Unadvertised coordination operation')
        Draft202012Validator(self.schemas[op]['schema']).validate(args);client=params['clientId']
        if op=='coordination.list':return await self.listing(args,client)
        if op=='coordination.wait':return await self.wait(args,client)
        if op=='coordination.command':
            receipt=self.receipt(args['commandId'])
            if receipt:
                # Receipts contain no prose but still require current access to their original target.
                await self.host('readCoordinationSession',{'session':receipt['target']['sessionId'],'args':{'limit':1,'clientId':client}})
            return {'receipt':receipt}
        caller=params.get('callerSession');origin=params.get('origin')
        if origin!='ui' and args['sessionId']!=caller:raise ValueError('Only explicit user actions may message or interrupt another conversation')
        command=params.get('commandId')
        if not isinstance(command,str) or not 1<=len(command)<=200:raise ValueError('Stable bounded coordination command required')
        sig=hashlib.sha256(json.dumps({'op':op,'args':args,'origin':origin,'caller':caller,'client':client},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        async with self.lock:
            saved=self.receipt(command)
            if saved:
                if saved['requestHash']!=sig:raise ValueError('Coordination command identity conflicts')
                return {'receipt':saved,'replayed':False}
            if op=='coordination.followup' and not args['text'].strip():raise ValueError('Enter a follow-up message')
            # Authorization is validated through the public selected snapshot before reserving effects.
            await self.snapshot({'sessionId':args['sessionId'],**({'workerId':args['workerId']} if args.get('workerId') else {})},client,False)
            row={'commandId':command,'status':'dispatching','operation':op,'target':{k:args[k] for k in ('sessionId','workerId') if k in args},'replayed':False};self.save(command,sig,row)
            try:
                payload={'session':args['sessionId'],'commandId':'coordination:'+hashlib.sha256(command.encode()).hexdigest(),'origin':origin,'clientId':client,**({'text':args['text']} if 'text' in args else {})}
                if args.get('workerId'):
                    workerargs={'workerId':args['workerId'],**({'inputId':payload['commandId'],'text':args['text']} if op=='coordination.followup' else {'commandId':payload['commandId']})}
                    result=await self.host('controlCoordinationWorker',{'session':args['sessionId'],'operation':'worker.followup' if op=='coordination.followup' else 'worker.interrupt','args':workerargs,'clientId':client})
                else:result=await self.host('controlCoordinationSession',{**payload,'operation':op.split('.')[1]})
                unknown=result.get('disposition')=='unknown' or result.get('delivery')=='unknown' or (result.get('receipt') or {}).get('status')=='unknown'
                row={**row,'status':'unknown' if unknown else 'accepted' if result.get('accepted') else 'rejected','result':result};self.save(command,sig,row);return {'receipt':row,'result':result,'replayed':False}
            except BaseException:self.save(command,sig,{**row,'status':'unknown'});raise
    async def close(self):self.db.close();self.intake.close();self.lease.release()
