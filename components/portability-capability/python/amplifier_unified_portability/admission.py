"""Exact original admission evidence in the existing owner commands journal.

This private launcher port accepts separately authenticated abort proofs. It never
reacquires an original fence or converts a generic release assertion into abort.
"""
import hashlib,json

CONTEXT=('commandId','fenceId','instanceId','dataScope','purpose')
BINDING=CONTEXT[:4]
PROOF=set(CONTEXT)|{'kind','verified','receiptId'}
RECEIPT=set(BINDING)|{'ownerId','status','receiptId'}
SCOPE='portability.admission.aggregate'
OWN='portability.admission.owner'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def token(value):
    if not isinstance(value,str) or not 1<=len(value)<=200 or any(ord(c)<32 for c in value):raise ValueError('Bounded admission identity required')
    return value

def context(value):
    if not isinstance(value,dict):raise ValueError('Exact admission mapping required')
    result={k:token(value.get(k)) for k in CONTEXT}
    if result['purpose']!='distribution-update' or 'serviceIdentity' in value:raise ValueError('Distribution admission context required')
    return result

def proof(value):
    ctx=context(value);p=value.get('proof')
    if not isinstance(p,dict) or set(p)!=PROOF or p.get('kind')!='distribution-admission-abort' or p.get('verified') is not True or any(p.get(k)!=ctx[k] for k in CONTEXT):raise ValueError('Distinct authenticated admission-abort proof required')
    token(p['receiptId']);return ctx,json.loads(canonical(p))

def receipt(value,ctx,owner):
    if not isinstance(value,dict) or set(value)!=RECEIPT or value.get('ownerId')!=owner or any(value.get(k)!=ctx[k] for k in BINDING) or value.get('status') not in {'released','not-acquired'}:raise ValueError('Exact original subowner abort receipt required')
    token(value['receiptId']);return json.loads(canonical(value))

class AdmissionJournal:
    def __init__(self,owner):self.owner=owner
    def load(self,scope,ctx):
        row=self.owner.db.execute('SELECT signature,body FROM commands WHERE scope=? AND id=?',(scope,ctx['fenceId'])).fetchone()
        if not row:return None
        body=json.loads(row[1])
        if row[0]!=digest(ctx) or body.get('context')!=ctx or body.get('version')!=1:raise ValueError('Original admission journal identity differs')
        return body
    def save(self,scope,ctx,body):self.owner.save_command(scope,ctx['fenceId'],digest(ctx),body)
    def busy(self):
        if self.owner.intake.calls or self.owner.intake.background or self.owner.requests or self.owner.closing:raise ValueError('Portability work is active; admission abort remains closed')
    def aggregate(self,ctx):
        body=self.load(SCOPE,ctx)
        if not body or not isinstance(body.get('owners'),list) or not 1<=len(body['owners'])<=65 or body['owners'][0].get('ownerId')!='portability-owner':raise ValueError('Complete original aggregate admission evidence required')
        ids=[token(row.get('ownerId')) for row in body['owners']]
        if len(set(ids))!=len(ids):raise ValueError('Distinct original subowners required')
        return body
    def begin(self,value):
        ctx=context(value);ids=value.get('owners')
        if not isinstance(ids,list) or not 1<=len(ids)<=65 or ids[0]!='portability-owner':raise ValueError('Complete ordered aggregate owner plan required')
        for identity in ids:token(identity)
        if len(set(ids))!=len(ids):raise ValueError('Distinct original aggregate owner plan required')
        old=self.load(SCOPE,ctx)
        if old:
            if [r['ownerId'] for r in old['owners']]!=ids:raise ValueError('Original aggregate owner plan changed')
            return {'started':False,'journal':old}
        if self.owner.intake.fence or self.owner.intake.db.execute('SELECT 1 FROM releases WHERE fence=?',(ctx['fenceId'],)).fetchone() or self.load(OWN,ctx):raise ValueError('Existing owner authority cannot become a new aggregate attempt')
        body={'version':1,'context':ctx,'state':'running','phase':'acquiring','owners':[{'ownerId':identity,'phase':'not-entered','preflight':'not-entered'} for identity in ids]}
        self.save(SCOPE,ctx,body);return {'started':True,'journal':body}
    def release_plan(self,value):
        ctx=context(value);body=self.load(SCOPE,ctx)
        if body is not None:return {'legacy':False,'journal':self.aggregate(ctx)}
        if self.load(OWN,ctx) is not None:raise ValueError('Original v2 owner admission lacks complete aggregate evidence')
        row=self.owner.intake.db.execute('SELECT value FROM releases WHERE fence=?',(ctx['fenceId'],)).fetchone()
        previous=json.loads(row[0]) if row else None
        if self.owner.intake.fence==ctx or previous and previous.get('context')==ctx and previous.get('outcome') in {'unchanged','ready'} and previous.get('kind')!='distribution-admission-abort':return {'legacy':True}
        raise ValueError('Original matching legacy intake authority required for ordinary release')
    def record(self,value):
        ctx=context(value);body=self.aggregate(ctx)
        if body['phase']!='acquiring':raise ValueError('Original aggregate admission is no longer acquiring')
        row=next((r for r in body['owners'] if r['ownerId']==value.get('ownerId')),None)
        if row is None:raise ValueError('Unplanned aggregate subowner')
        stage=value.get('stage');evidence=value.get('evidence',{})
        if len(canonical(evidence).encode())>16384:raise ValueError('Bounded selected admission evidence required')
        if stage in {'preflight-entering','preflight-ready','preflight-refused','preflight-unknown'}:
            if row['phase']!='not-entered':raise ValueError('Preflight cannot follow owner acquisition')
            row['preflight']=stage;row['preflightEvidence']=evidence
        elif stage=='entering':
            if row['phase']!='not-entered':raise ValueError('Original owner acquisition cannot be repeated')
            row['phase']=stage
        elif stage in {'acquired','refused','unknown','released'}:
            allowed={'entering','acquired','unknown'} if stage=='released' else {'entering'}
            if row['phase'] not in allowed:raise ValueError('Original owner admission transition differs')
            if stage=='refused' and (evidence.get('executed') is not False or evidence.get('intakeClosed') is not False):raise ValueError('No-effect original refusal evidence required')
            row['phase']=stage;row['releaseEvidence' if stage=='released' else 'evidence']=evidence
        else:raise ValueError('Unknown original admission transition')
        self.save(SCOPE,ctx,body);return body
    def finish(self,value):
        ctx=context(value);body=self.aggregate(ctx)
        if body['phase']!='acquiring':raise ValueError('Original admission already settled')
        status=value.get('status')
        if status not in {'held','refused','unknown'}:raise ValueError('Original aggregate result required')
        if status=='refused' and any(r['phase'] in {'entering','acquired','unknown'} for r in body['owners']):raise ValueError('Unsettled original subowner cannot become a known refusal')
        if status=='held' and any(r['phase']!='acquired' for r in body['owners']):raise ValueError('Incomplete aggregate acquisition')
        body['phase']=status;body['state']='unknown' if status=='unknown' else status
        self.save(SCOPE,ctx,body);return body
    def acquire_owner(self,value):
        ctx=context(value);old=self.load(OWN,ctx)
        if old:
            if old.get('phase')=='refused':return old['result']
            if old.get('abort') or old.get('phase')=='released':raise ValueError('Original owner admission cannot acquire again')
            if self.owner.intake.fence!=ctx:raise ValueError('Original owner acquisition outcome is unresolved')
            if old.get('result'):return old['result']
        else:
            old={'version':1,'context':ctx,'state':'running','phase':'entering'};self.save(OWN,ctx,old)
        body=self.load(SCOPE,ctx)
        if body and body['phase']!='acquiring':raise ValueError('Aggregate admission no longer permits acquisition')
        result=self.owner.intake.acquire(ctx)
        if result.get('acquired') is True:old['phase']='acquired'
        elif result.get('executed') is False and not self.owner.intake.fence:old['phase']='refused';result={**result,'intakeClosed':False}
        else:raise ValueError('Owner acquisition is uncertain')
        result={**result,'ownerId':'portability-owner',**{k:ctx[k] for k in BINDING},'receiptId':'portability-acquire:'+digest([ctx,result])}
        old.update(state=old['phase'],result=result);self.save(OWN,ctx,old);return result
    def release_owner(self,value):
        ctx=context(value);result=self.owner.intake.release(value)
        body=self.load(OWN,ctx)
        if body and result.get('released') is True:
            body.update(phase='released',state='released',release=result)
            self.save(OWN,ctx,body)
        return result
    def abort_begin(self,value):
        ctx,p=proof(value);body=self.aggregate(ctx)
        if body.get('abortProof') not in (None,p):raise ValueError('Original admission abort proof changed')
        if body.get('phase')=='aborted':return body
        self.busy()
        body['abortProof']=p;body['phase']='aborting';body['state']='unknown'
        for row in body['owners'][1:]:
            if row['phase']=='not-entered' or row['phase']=='refused' and row.get('evidence',{}).get('executed') is False and row['evidence'].get('intakeClosed') is False:
                result={'ownerId':row['ownerId'],**{k:ctx[k] for k in BINDING},'status':'not-acquired','receiptId':'portability-not-entered:'+digest([ctx,p,row])}
                row.setdefault('abortReceipt',result)
        self.save(SCOPE,ctx,body);return body
    def abort_record(self,value):
        ctx,p=proof(value);self.busy();body=self.aggregate(ctx)
        if body.get('phase')!='aborting' or body.get('abortProof')!=p:raise ValueError('Exact original abort intent required')
        result=value.get('receipt');identity=result.get('ownerId') if isinstance(result,dict) else None
        row=next((r for r in body['owners'][1:] if r['ownerId']==identity),None)
        if row is None:raise ValueError('Original native subowner required')
        result=receipt(result,ctx,identity)
        if row.get('abortReceipt') not in (None,result):raise ValueError('Original subowner abort receipt changed')
        row['abortReceipt']=result;self.save(SCOPE,ctx,body);return body
    def abort_owner(self,ctx,p,*,not_entered=False):
        intake=self.owner.intake
        row=intake.db.execute('SELECT value FROM releases WHERE fence=?',(ctx['fenceId'],)).fetchone()
        previous=json.loads(row[0]) if row else None
        if previous and previous.get('kind')=='distribution-admission-abort':
            if previous.get('context')!=ctx or previous.get('proof')!=p or intake.fence==ctx:raise ValueError('Owner abort receipt changed or selected intake differs')
            return receipt(previous['result'],ctx,'portability-owner')
        self.busy()
        original=self.load(OWN,ctx)
        if original and original['phase'] in {'entering','acquired'} and intake.fence==ctx:status='released'
        elif original and original['phase']=='refused' and original.get('result',{}).get('acquired') is False and original['result'].get('executed') is False and intake.fence!=ctx:status='not-acquired'
        elif original and original.get('result',{}).get('acquired') is True and previous and previous.get('context')==ctx and previous.get('outcome')=='unchanged' and previous.get('proof')=={'kind':'admission-refused'} and intake.fence!=ctx:status='released'
        elif not_entered and original is None and intake.fence!=ctx:status='not-acquired'
        else:raise ValueError('Durable original owner acquisition or refusal evidence required')
        result={'ownerId':'portability-owner',**{k:ctx[k] for k in BINDING},'status':status,'receiptId':'portability-owner-abort:'+digest([ctx,p,status])}
        record={'kind':'distribution-admission-abort','context':ctx,'proof':p,'result':result}
        with intake.db:
            intake.db.execute('INSERT OR REPLACE INTO releases VALUES(?,?)',(ctx['fenceId'],canonical(record)))
            if intake.fence==ctx:intake.db.execute('DELETE FROM fence WHERE id=1')
        if intake.fence==ctx:intake.fence=None;intake._live_fence=None
        return result
    def abort_complete(self,value):
        ctx,p=proof(value);body=self.aggregate(ctx)
        if body.get('abortProof')!=p:raise ValueError('Exact original abort proof required')
        if body['phase']=='aborted':return body['result']
        self.busy()
        if body['phase']!='aborting' or any('abortReceipt' not in row for row in body['owners'][1:]):raise ValueError('Complete original subowner settlement evidence required')
        own=self.abort_owner(ctx,p,not_entered=body['owners'][0]['phase']=='not-entered')
        body['owners'][0]['abortReceipt']=own
        rows=[receipt(row['abortReceipt'],ctx,row['ownerId']) for row in body['owners']]
        status='released' if any(row['status']=='released' for row in rows) else 'not-acquired'
        result={'ownerId':'portability',**{k:ctx[k] for k in BINDING},'status':status,'receiptId':'portability-aggregate-abort:'+digest([ctx,p,rows])}
        body.update(phase='aborted',state='aborted',result=result);self.save(SCOPE,ctx,body);return result
    def dispatch(self,operation,value):
        extra={'begin':{'owners'},'inspect':set(),'releasePlan':set(),'record':{'ownerId','stage','evidence'},'finish':{'status'},'abortBegin':{'proof'},'abortRecord':{'proof','receipt'},'abortComplete':{'proof'}}.get(operation)
        if extra is None or not isinstance(value,dict) or set(value)!=set(CONTEXT)|extra:raise ValueError('Exact private original admission selectors required')
        method={'begin':self.begin,'inspect':lambda args:self.aggregate(context(args)),'releasePlan':self.release_plan,'record':self.record,'finish':self.finish,'abortBegin':self.abort_begin,'abortRecord':self.abort_record,'abortComplete':self.abort_complete}.get(operation)
        if method is None:raise ValueError('Unknown private admission journal operation')
        return method(value)
