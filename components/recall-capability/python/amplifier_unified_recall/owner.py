from .retention import selected, result, exists, managed_selected, add_protection
"""Scoped Recall authority and opt-in personalization, independent of the engine."""
import asyncio
import copy
import json
from pathlib import Path
import time
from filelock import FileLock
from jsonschema import Draft202012Validator
from amplifier_recall import RecallStore
from amplifier_operations.quiescence import DurableIntakeFence
from amplifier_recall.store import digest
from .schemas import actions
from .personalization import Personalization
from .consolidation_policy import build_request, verified_candidates, relevant
from .evidence import is_typed_text, verify


def human(row):
    return (row.get('role')=='user' and row.get('inputOrigin') in {'user','ui','voice'}
        and row.get('provenance',{}).get('source')=='host-admission'
        and row.get('provenance',{}).get('complete') is True
        and not row.get('questionId') and not row.get('scheduledRunId')
        and isinstance(row.get('text'),str) and is_typed_text(row['text']))


class Owner:
    def __init__(self,config,host,notify):
        self.host,self.notify=host,notify;directory=Path(config['dataDir']);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lease=FileLock(directory/'owner.lock');self.lease.acquire(timeout=0)
        self.intake=DurableIntakeFence(directory/'intake.sqlite3');self.awaiting_idle=False
        self.store=RecallStore(directory/'recall.sqlite3',retain_versions=50,max_text_characters=8000)
        self.policy=Personalization(self.store);self.tasks={};self.changed=asyncio.Event();self.closed=False
        with self.store.db:
            self.store.db.executescript('CREATE TABLE IF NOT EXISTS recall_progress(scope TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS memory_delivery(session TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS memory_commands(id TEXT PRIMARY KEY,session TEXT,operation TEXT,fingerprint TEXT,value TEXT);CREATE TABLE IF NOT EXISTS recall_admissions(id TEXT PRIMARY KEY,fingerprint TEXT,value TEXT);')
            self.store.db.execute("UPDATE recall_progress SET value=json_set(value,'$.status','interrupted') WHERE json_extract(value,'$.status')='indexing'")
        self.schemas=actions()

        self.store.db.execute("CREATE INDEX IF NOT EXISTS retention_memory ON memory_commands(session,json_extract(value,'$.state'))")

    async def call(self,method,**args):return await self.host(method,args)
    async def session(self,sid):
        value=await self.call('inspectSession',session=sid)
        return {**value,'id':sid,'workspace':value.get('historyHome') or value.get('workingDirectory') or value.get('workspace') or ''}
    @staticmethod
    def scopes(session):return [('task',session['id']),('workspace',session['workspace']),('global','')]
    def progress(self,sid):
        row=self.store.db.execute('SELECT value FROM recall_progress WHERE scope=?',(sid,)).fetchone()
        return json.loads(row[0]) if row else {'revision':0,'status':'not_checked','indexed':0,'total':None,'errorCount':0,'notice':'Derived text is reference data, never authorization. No background source scan has run.'}
    async def publish(self,sid,**changes):
        value={**self.progress(sid),**changes};value['revision']+=1
        with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO recall_progress VALUES(?,?)',(sid,json.dumps(value)))
        event,self.changed=self.changed,asyncio.Event();event.set();await self.notify('owner/changed',{'session':sid})
    async def maybe_idle(self):
        if self.awaiting_idle and not self.closed and not self.intake.calls and not self.tasks:
            self.awaiting_idle=False
            await self.notify('owner/idle',{})
    def start(self,key,work):
        if self.intake.fence:raise ValueError('Recall intake is closed; no background work was admitted')
        if key in self.tasks:return False
        if self.closed or len(self.tasks)>=4:raise ValueError('Recall background capacity reached')
        async def guarded():
            try:await work()
            except asyncio.CancelledError:raise
            except Exception as error:
                if key.startswith('memory:'):self.policy.activity(key[7:],{'status':'failed','reason':str(error)[:200]})
                else:await self.publish(key[6:],status='failed',error=str(error)[:200])
        task=asyncio.create_task(guarded());self.tasks[key]=task;task.add_done_callback(lambda finished:self.tasks.pop(key,None));task.add_done_callback(lambda finished:asyncio.create_task(self.maybe_idle()));return True
    async def index_source(self,metadata):
        source=await self.call('inspectRecallSource',id=metadata['id']);revision=source['revision']
        if self.store.signature(source['id'])==digest(revision):self.store.set_available(source['id'],True);return False
        token=self.store.begin_source({key:source[key] for key in ('id','title','workspace','kind','parentId') if key in source},digest(revision),revision)
        try:
            cursor=None
            while True:
                page=await self.call('readRecallSource',id=source['id'],input={'limit':50,'expectedRevision':revision,**({'cursor':cursor} if cursor else {})})
                if page['revision']!=revision or len(page['rows'])>1000 or len(json.dumps(page).encode())>2097152:raise ValueError('Source revision or page bound changed')
                for offset in range(0,len(page['rows']),100):self.store.append_source(token,page['rows'][offset:offset+100])
                cursor=page.get('nextCursor')
                if not cursor:break
            latest=await self.call('inspectRecallSource',id=source['id'])
            if latest['revision']!=revision:raise ValueError('Source changed during indexing')
            self.store.commit_source(token,expected_revision=revision);return True
        except BaseException:self.store.discard_source(token);raise
    async def refresh(self,sid,scope):
        count=updated=errors=0;details=[];cursor=None
        await self.publish(sid,status='indexing',scope=scope,total=None,indexed=0,processed=0,errorCount=0,errors=[])
        try:
            while True:
                page=await self.call('listRecallSources',session=sid,scope=scope,limit=25,**({'cursor':cursor} if cursor else {}))
                if len(page['items'])>25:raise ValueError('Catalog page exceeded bound')
                for source in page['items']:
                    try:updated+=await self.index_source(source)
                    except Exception as error:
                        errors+=1;self.store.set_available(source['id'],False)
                        if len(details)<25:details.append({'sessionId':source['id'],'reason':str(error)[:200]})
                    count+=1
                    await self.publish(sid,processed=count,indexed=count-errors,updated=updated,errorCount=errors,errors=details,coverage=page.get('coverage'))
                cursor=page.get('nextCursor')
                if not cursor:break
            await self.publish(sid,status='partial' if errors else 'ready',total=count,checkedAt=time.time())
        except asyncio.CancelledError:await self.publish(sid,status='interrupted');raise
        except Exception as error:await self.publish(sid,status='failed',error=str(error)[:200])
    def status(self,session):
        workspace=session['workspace'];row=self.store.db.execute('SELECT value FROM memory_delivery WHERE session=?',(session['id'],)).fetchone()
        return {'settings':self.policy.settings(workspace),'running':'memory:'+workspace in self.tasks,'attempts':self.policy.attempts(workspace),'activity':self.policy.activity(workspace),'lastContext':json.loads(row[0]) if row else None,'limits':{'inputCharacters':16000,'outputTokens':4096,'memoriesPerCall':8},'notice':'Contribution and use are independently opt-in. Interrupted attempts never replay.'}
    async def authorization(self,session,args,origin):
        provenance={'origin':origin,'sessionId':session['id']}
        if origin!='ui':
            row=await self.call('readUserMessage',session=session['id'],messageId=args.get('authorizationMessageId'))
            if not human(row):raise ValueError('An original attributable host-admitted human request is required')
            provenance.update(authorizationMessageId=row['id'],authorizationSha256=digest(row['text']))
        return provenance
    async def verified_source(self,note,config):
        source=note.get('source') or {}
        if source.get('kind')!='attributed-user':return None
        if source['sessionId'] in config['excludedSessions']:raise ValueError('Memory source was withdrawn')
        session=await self.session(source['sessionId'])
        if session['workspace']!=config['workspace']:raise ValueError('Memory source workspace changed')
        row=await self.call('readUserMessage',session=source['sessionId'],messageId=source['messageId'])
        if not human(row) or digest(row['text'])!=source['sha256']:raise ValueError('Memory evidence changed or is unavailable')
        return {**source,'text':row['text'],'verifiedAt':time.time()}
    async def human_rows(self,sid):
        context=await self.call('readSessionContext',session=sid,limit=24);rows=[];size=2
        for candidate in reversed(context.get('messages',[])):
            if candidate.get('role')!='user':continue
            try:row=await self.call('readUserMessage',session=sid,messageId=candidate['id'])
            except Exception:continue
            if not human(row):continue
            cost=len(json.dumps(row['text'],ensure_ascii=False))+2
            if size+cost>16000:continue
            rows.insert(0,row);size+=cost
        return rows,digest([(row['id'],row['text']) for row in rows])
    async def consolidate_source(self,session):
        workspace=session['workspace'];config=self.policy.settings(workspace);sid=session['id'];identity=None
        if not config['contribute'] or sid in config['excludedSessions'] or session.get('status')=='working' or session.get('activeTurnId') or session.get('relocationFence'):return
        try:
            rows,signature=await self.human_rows(sid)
            if not rows:return
            page=self.store.list_memories([('workspace',workspace)],limit=100)
            if page['nextOffset'] is not None:raise ValueError('Known reference comparison exceeds its complete 100-note bound; curate notes before consolidation')
            known=[]
            for note in page['items']:
                if note.get('supersededBy'):continue
                try:await self.verified_source(note,config)
                except ValueError:continue
                known.append(note)
            prompt=build_request([row['text'] for row in rows],policy='workspace',known=known)
            identity=self.policy.claim(workspace,sid,signature,config['maxCallsPerDay'])
            if identity is None:return
            result=await self.call('nativeControlExisting',session=sid,operation='memory.consolidate',args={'prompt':prompt})
            if result.get('interrupted'):raise ValueError('Foreground work interrupted memory; no automatic replay')
            candidates=verified_candidates(result['text'],[row['text'] for row in rows],known=known)
            if self.policy.settings(workspace)['revision']!=config['revision'] or (await self.human_rows(sid))[1]!=signature:raise ValueError('Source or consent changed before saving')
            for note in known:
                if self.store.memory(note['id'])['revision']!=note['revision']:raise ValueError('Known reference changed')
                await self.verified_source(note,config)
            saved=[]
            with self.store.atomic():
                for candidate in candidates:
                    row=next(row for row in reversed(rows) if verify(candidate['quote'],[row['text']]))
                    source={'sessionId':sid,'messageId':row['id'],'sha256':digest(row['text']),'sourceRevision':signature,'quote':candidate['quote'],'kind':'attributed-user','messageCreatedAt':row.get('createdAt')}
                    superseded=[note for note in known if note['id'] in candidate['supersedes']]
                    # Host timestamps are ISO strings; compare exact timestamps only.
                    from datetime import datetime
                    def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if isinstance(value,str) else float(value or 0)
                    if any(stamp(source['messageCreatedAt'])<=stamp((note.get('source') or {}).get('messageCreatedAt') or note['updatedAt']) for note in superseded):raise ValueError('A correction requires newer human evidence')
                    note=self.policy.save(workspace,sid,source,candidate,identity)
                    if note:
                        saved.append({'id':note['id'],'revision':note['revision']})
                        for prior in superseded:self.store.mutate('memory.update',{'id':prior['id'],'expectedRevision':prior['revision'],'text':prior['text'],'supersededBy':note['id']},command_id='supersede:'+note['id']+':'+prior['id'],provenance={'origin':'consolidation','sessionId':sid})
            self.policy.finish(identity,{'status':'completed','saved':saved,'provider':result.get('provider'),'model':result.get('model')});self.policy.activity(workspace,{'status':'completed','saved':len(saved)})
        except asyncio.CancelledError:
            if identity:self.policy.finish(identity,{'status':'unknown','reason':'Owner stopped; no automatic replay'})
            raise
        except Exception as error:
            if identity:self.policy.finish(identity,{'status':'unknown','reason':str(error)[:200]})
            self.policy.activity(workspace,{'status':'skipped' if not identity else 'unknown','reason':str(error)[:200]})
        finally:await self.notify('owner/changed',{'session':sid})
    async def consolidate(self,session,source=None):
        if source:
            selected=await self.session(source)
            if selected['workspace']!=session['workspace']:raise ValueError('Consolidation source must belong to this workspace')
            await self.consolidate_source(selected);return
        cursor=None;examined=0;limit=self.policy.settings(session['workspace'])['maxCallsPerDay']
        def spent():return self.store.db.execute('SELECT COUNT(*) FROM memory_attempts WHERE workspace=? AND created>=?',(session['workspace'],time.time()//86400*86400)).fetchone()[0]
        while examined<100 and spent()<limit:
            page=await self.call('listRecallSources',session=session['id'],scope='workspace',limit=25,**({'cursor':cursor} if cursor else {}))
            for row in page['items']:
                await self.consolidate_source(await self.session(row['id']));examined+=1
                if examined>=100 or spent()>=limit:break
            cursor=page.get('nextCursor')
            if not cursor:break
        if cursor and examined>=100:self.policy.activity(session['workspace'],{'status':'partial','reason':'Reviewed100 source candidates; choose a specific older conversation to continue'})
    async def context(self,session,expected=None):
        config=self.policy.settings(session['workspace']);result={'items':[],'enabled':config['use'],'settingsRevision':config['revision']}
        if config['use'] and session['id'] not in config['excludedSessions']:
            rows,_=await self.human_rows(session['id']);query=rows[-1]['text'] if rows else ''
            page=self.store.list_memories(self.scopes(session),limit=100)
            if page['nextOffset'] is not None:result['coverage']='More than100 scoped references; context omitted pending explicit curation'
            else:
                available=[]
                for note in relevant([row for row in page['items'] if not row.get('supersededBy')],query):
                    try:await self.verified_source(note,config)
                    except ValueError:continue
                    item={key:note[key] for key in ('id','revision','scope','text','provenance','matchTerms','retrievalReason') if key in note};item['source']={key:value for key,value in (note.get('source') or {}).items() if key!='quote'}
                    if len(json.dumps([*available,item],ensure_ascii=False))<=6000:available.append(item)
                result['items']=available
        if expected==result:
            receipt={'at':time.time(),'items':[{key:row[key] for key in ('id','revision','matchTerms','retrievalReason')} for row in result['items']],'settingsRevision':config['revision']}
            with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO memory_delivery VALUES(?,?)',(session['id'],json.dumps(receipt)))
        return result
    async def dispatch(self,action,args,session,origin,command_id):
        sid=session['id'];workspace=session['workspace']
        if action=='recall.status':return self.progress(sid)
        if action=='recall.refresh':self.start('index:'+sid,lambda:self.refresh(sid,args.get('scope','workspace')));await asyncio.sleep(0);return self.progress(sid)
        if action=='recall.wait':
            if args.get('afterRevision')==self.progress(sid)['revision']:
                try:await asyncio.wait_for(self.changed.wait(),args.get('waitMs',30000)/1000)
                except TimeoutError:pass
            return self.progress(sid)
        if action=='recall.search':
            if args.get('includeChildren') or args.get('includeInternal'):raise ValueError('This host has not advertised child/internal Recall inventory')
            scope=args.get('scope','workspace');result=self.store.search_scope(args['query'],session=sid if scope=='task' else None,workspace=workspace if scope=='workspace' else None,offset=args.get('offset',0),limit=args.get('limit',20),cursor=args.get('cursor'))
            visible=[]
            for row in result['items']:
                try:
                    page=await self.call('listRecallSources',session=row['sessionId'],scope='task',limit=1)
                    if not page['items']:continue
                    source=page['items'][0]
                    if scope=='workspace' and source['workspace']!=workspace:continue
                    visible.append(row)
                except Exception:self.store.set_available(row['sessionId'],False)
            return {**result,'items':visible,'coverage':self.progress(sid),'scope':scope,'sourceFreshness':'indexed_snapshot; verify with recall.read'}
        if action=='recall.read':
            source=await self.call('inspectRecallSource',id=args['sourceSessionId'])
            if self.store.signature(source['id'])!=digest(source['revision']):raise ValueError('Source changed since indexing; refresh before reading')
            result=self.store.message(source['id'],args['messageId'],args.get('offset',0),args.get('limit',4000))
            if result['sourceRevision']!=args['sourceRevision']:raise ValueError('Requested source revision changed')
            return {**result,'verifiedAt':time.time()}
        if action=='memory.list':
            scopes=self.scopes(session);scope=args.get('scope','available');scopes=None if scope=='all' else scopes if scope=='available' else [pair for pair in scopes if pair[0]==scope]
            return self.store.list_memories(scopes,args.get('offset',0),args.get('limit',20))
        if action in {'memory.read','memory.source'}:
            note=self.store.memory(args['id'])
            if not args.get('allScopes') and (note['scope'],note['target']) not in self.scopes(session):raise ValueError('Memory is outside selected scope')
            if action=='memory.source':return await self.verified_source(note,self.policy.settings(workspace))
            return {**note,'versions':self.store.versions(note['id']),'historyLimit':50}
        if action=='memory.command':
            row=self.store.db.execute('SELECT session,value FROM memory_commands WHERE id=?',(args['commandId'],)).fetchone()
            if not row:return None
            if row[0]!=sid:raise ValueError('Command receipt belongs to another conversation')
            result=json.loads(row[1]);saved=self.store.db.execute('SELECT result FROM memory_receipts WHERE id=?',(args['commandId'],)).fetchone()
            if saved and result['state']=='unknown':result.update(state='succeeded',result=json.loads(saved[0]))
            return result
        if action=='memory.status':return self.status(session)
        if action=='memory.context':return await self.context(session)
        if action=='memory.configure':
            self.policy.configure(workspace,args,await self.authorization(session,args,origin),command_id);return self.status(session)
        if action=='memory.consolidate':
            if not self.policy.settings(workspace)['contribute']:raise ValueError('Workspace memory contribution is not enabled')
            fingerprint=digest([action,args,origin]);prior=self.store.db.execute('SELECT fingerprint FROM recall_admissions WHERE id=?',(command_id,)).fetchone()
            if prior:
                if prior[0]!=fingerprint:raise ValueError('Command identity changed')
                return {**self.status(session),'duplicate':True}
            with self.store.db:self.store.db.execute('INSERT INTO recall_admissions VALUES(?,?,?)',(command_id,fingerprint,'{}'))
            self.start('memory:'+workspace,lambda:self.consolidate(session,args.get('sourceSessionId')));return self.status(session)
        provenance=await self.authorization(session,args,origin);values=copy.deepcopy(args)
        previous=self.store.receipt(command_id,digest([action,args,provenance]))
        if previous is not None:return previous
        if action=='memory.create':values['target']=dict(self.scopes(session))[args['scope']]
        else:
            current=self.store.memory(args['id'])
            if not args.get('allScopes') and (current['scope'],current['target']) not in self.scopes(session):raise ValueError('Memory is outside selected scope')
            if 'scope' in args and args['scope']!=current['scope']:raise ValueError('Create a new note to change scope')
        if args.get('source'):
            source=args['source'];stamp=await self.call('inspectRecallSource',id=source['sessionId'])
            if self.store.signature(source['sessionId'])!=digest(stamp['revision']):raise ValueError('Memory source changed since indexing')
            evidence=self.store.message(source['sessionId'],source['messageId'],0,1)
            if source.get('sourceRevision') is not None and source['sourceRevision']!=evidence['sourceRevision']:raise ValueError('Memory evidence revision changed')
            values['source']={key:evidence[key] for key in ('sessionId','messageId','sourceRevision','sha256')}
        result=self.store.mutate(action,values,command_id=command_id,provenance=provenance,request_fingerprint=digest([action,args,provenance]))
        if action!='memory.create':self.policy.suppress(current)
        return result
    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            if exists(self.store.db,"SELECT 1 FROM memory_commands WHERE session=? AND json_extract(value,'$.state')='unknown' LIMIT 1",(session,)):reasons.append('memory-unsettled')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);sessions=args['sessions']
        return base


    async def request(self,method,params):
        if method=='quiescence.retention':return self.retention_references(params)
        if method=='quiescence.managedFiles':return self.managed_references(params)
        if method=='quiescence.acquire':
            value=self.intake.acquire(params,pending=len(self.tasks))
            if not value['acquired']:self.awaiting_idle=True
            return value
        if method=='quiescence.release':return self.intake.release(params)
        if method=='quiescence.inspect':return {'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'activeRequests':self.intake.calls,'background':len(self.tasks)}
        passive=method in {'initialize','actions','snapshot'} or method=='action' and params.get('operation') in {'recall.status','recall.wait','recall.read','memory.status','memory.list','memory.read','memory.command'}
        if self.intake.fence and not passive:raise ValueError('Recall intake is closed; no write or background work was admitted')
        if not passive:self.intake.calls+=1
        try:return await self._request(method,params)
        finally:
            if not passive:
                self.intake.calls-=1
                await self.maybe_idle()
    async def _request(self,method,params):
        if method=='initialize':return {'protocolVersion':1,'quiescence':{'version':1,'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})}}
        if method=='actions':return self.schemas
        session=await self.session(params['session'])
        if method=='snapshot':return {'recall':{session['id']:{'coverage':self.progress(session['id']),'memory':self.status(session)}}}
        if method=='context':return await self.context(session,params.get('expected'))
        if method=='idle':
            if self.policy.settings(session['workspace'])['contribute']:self.start('memory:'+session['workspace'],lambda:self.consolidate_source(session))
            return {'accepted':True}
        if method!='action':raise ValueError('Unknown Recall owner method')
        action=params['operation'];args=params.get('args',{})
        if action not in self.schemas:raise ValueError('Unadvertised Recall action')
        if args.get('sessionId',session['id'])!=session['id']:raise ValueError('Session differs from trusted scope')
        args={**args,'sessionId':session['id']};Draft202012Validator(self.schemas[action]['parameters']).validate(args)
        identity=params['commandId'];origin=params.get('origin','ui');journal=action in {'memory.create','memory.update','memory.delete','memory.configure'}
        if journal:
            fingerprint=digest([action,args,origin]);old=self.store.db.execute('SELECT session,fingerprint,value FROM memory_commands WHERE id=?',(identity,)).fetchone()
            if old:
                if old[0]!=session['id'] or old[1]!=fingerprint:raise ValueError('Memory command identity changed')
                saved=json.loads(old[2])
                if saved['state']=='succeeded':return saved['result']
                raise ValueError('Previous memory command requires receipt inspection; nothing was repeated')
            with self.store.db:self.store.db.execute('INSERT INTO memory_commands VALUES(?,?,?,?,?)',(identity,session['id'],action,fingerprint,json.dumps({'commandId':identity,'operation':action,'state':'unknown'})))
        try:result=await self.dispatch(action,args,session,origin,identity)
        except Exception as error:
            if journal:
                with self.store.db:self.store.db.execute('UPDATE memory_commands SET value=? WHERE id=?',(json.dumps({'commandId':identity,'operation':action,'state':'failed','error':str(error)[:500]}),identity))
            raise
        if journal:
            with self.store.db:self.store.db.execute('UPDATE memory_commands SET value=? WHERE id=?',(json.dumps({'commandId':identity,'operation':action,'state':'succeeded','result':result}),identity))
        if journal or action in {'recall.refresh','memory.consolidate'}:await self.notify('owner/changed',{'session':session['id']})
        return result
    async def close(self):
        self.closed=True
        for task in list(self.tasks.values()):task.cancel()
        await asyncio.gather(*list(self.tasks.values()),return_exceptions=True);self.store.close();self.intake.close();self.lease.release()
