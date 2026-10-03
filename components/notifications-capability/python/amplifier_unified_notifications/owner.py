from .retention import selected, result, exists, managed_selected, add_protection
"""Opt-in ntfy delivery policy. No native runtime, filesystem picker or generic HTTP API."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
import aiohttp
from amplifier_operations.quiescence import DurableIntakeFence

READS={'notifications.get','notifications.receipt'}
def token(value,name,limit=200):
    if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):raise ValueError('Invalid '+name)
    return value

def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def valid(value):
    for key in ('enabled','preview'):
        if type(value[key]) is not bool:raise ValueError('Notification switches must be boolean')
    server=token(value['server'],'HTTPS notification server',2048);parsed=urlsplit(server)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Use an HTTPS notification server without credentials, query or fragment')
    try:parsed.port
    except ValueError:raise ValueError('Invalid notification server port') from None
    if value['topic'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value['topic']):raise ValueError('Invalid private notification topic')
    if value['token']:token(value['token'],'private access token',8192)
    if value['enabled'] and not value['topic']:raise ValueError('Configure a private topic before enabling delivery')
    return value

async def post_ntfy(value,title,message):
    """Exactly one POST of fixed ntfy JSON. Redirects and response reflection are forbidden."""
    headers={'Authorization':'Bearer '+value['token']} if value['token'] else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
        async with client.post(value['server'].rstrip('/'),headers=headers,json={'topic':value['topic'],'title':title,'message':message},allow_redirects=False) as response:
            # HTTP success establishes server acceptance only, not a device display receipt.
            return response.status

class Owner:
    def __init__(self,config,changed=None,idle=None,transport=post_ntfy):
        directory=Path(token(config.get('stateDirectory'),'owner directory',4096))
        if not directory.is_absolute():raise ValueError('Private owner directory must be absolute')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory=directory.resolve();self.directory=directory
        self.lease=sqlite3.connect(directory/'owner-lock.sqlite',timeout=0)
        try:self.lease.executescript('PRAGMA journal_mode=DELETE;CREATE TABLE IF NOT EXISTS lease(id INTEGER);BEGIN EXCLUSIVE;')
        except BaseException:self.lease.close();raise
        try:
            self.db=sqlite3.connect(directory/'notifications.sqlite');self.db.row_factory=sqlite3.Row
            self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
              CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER,value TEXT);
              CREATE TABLE IF NOT EXISTS credentials(id INTEGER PRIMARY KEY CHECK(id=1),topic TEXT,token TEXT);
              CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,signature TEXT,receipt TEXT);
              CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,session TEXT,signature TEXT,status TEXT,value TEXT,updated REAL);
              CREATE INDEX IF NOT EXISTS delivery_order ON deliveries(updated DESC,id);
              CREATE INDEX IF NOT EXISTS delivery_status ON deliveries(status);''')
            defaults=valid({'enabled':False,'server':config.get('defaultServer','https://ntfy.sh'),'topic':'','token':'','preview':False})
            if not self.db.execute('SELECT 1 FROM settings WHERE id=1').fetchone():
                legacy=config.get('legacySettingsPath')
                if legacy:
                    source=Path(token(legacy,'legacy settings file',4096))
                    if not source.is_absolute():raise ValueError('Legacy settings path must be explicit and absolute')
                    if source.exists():
                        with source.open('rb') as stream:payload=stream.read(16385)
                        if len(payload)>16384:raise ValueError('Legacy settings exceed16KiB; explicit review required')
                        try:
                            old=json.loads(payload)
                            for key in ('server','topic','token','preview'):
                                if key in old:defaults[key]=old[key]
                            defaults['enabled']=old.get('push',False);valid(defaults)
                        except (ValueError,TypeError,KeyError):raise ValueError('Legacy notification settings require explicit correction') from None
                self.db.execute('INSERT INTO settings VALUES(1,0,?)',(json.dumps(self.redacted(defaults)),))
                self.db.execute('INSERT INTO credentials VALUES(1,?,?)',(defaults['topic'],defaults['token']))
            self.db.execute("UPDATE deliveries SET status='unknown',value=json_set(value,'$.status','unknown','$.reason','Owner ended before a confirmed boundary; no delivery replayed') WHERE status IN ('accepted','dispatching')");self.db.commit()
            os.chmod(directory/'notifications.sqlite',0o600)
            self.intake=DurableIntakeFence(directory/'intake.sqlite')
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            self.lease.close();raise
        self.transport=transport;self.changed=changed;self.idle=idle;self.closed=False;self.tasks=set();self.semaphore=asyncio.Semaphore(4)

        self.db.execute('CREATE INDEX IF NOT EXISTS retention_deliveries ON deliveries(session,status)')

    async def notice(self,callback):
        if callback:
            try:await callback()
            except Exception:pass

    @staticmethod
    def redacted(value):
        return {**{k:value[k] for k in ('enabled','server','preview')},'topicConfigured':bool(value['topic']),'tokenConfigured':bool(value['token'])}

    def private(self):
        row=self.public();credentials=self.db.execute('SELECT topic,token FROM credentials WHERE id=1').fetchone()
        return row['revision'],{**{k:row[k] for k in ('enabled','server','preview')},'topic':credentials['topic'],'token':credentials['token']}

    def public(self):
        row=self.db.execute('SELECT * FROM settings WHERE id=1').fetchone();return {'revision':row['revision'],**json.loads(row['value'])}

    def exact(self,command):
        token(command,'command ID');row=self.db.execute('SELECT receipt FROM commands WHERE id=?',(command,)).fetchone()
        if row:return json.loads(row[0])
        row=self.db.execute('SELECT value FROM deliveries WHERE id=?',(command,)).fetchone()
        if row:return json.loads(row[0])
        raise ValueError('Notification receipt unavailable')

    def save(self,args,command):
        token(command,'command ID');fingerprint=signature(['notifications.save',args]);prior=self.db.execute('SELECT * FROM commands WHERE id=?',(command,)).fetchone()
        if prior:
            if prior['signature']!=fingerprint:raise ValueError('Notification command identity changed')
            return {**json.loads(prior['receipt']),'replayed':False}
        revision,value=self.private();patch=args.get('patch')
        try:
            if type(args.get('expectedRevision')) is not int or args['expectedRevision']!=revision:raise ValueError('Notification settings changed; refresh before saving')
            if not isinstance(patch,dict) or set(patch)-{'enabled','server','topic','token','preview','desktop','clearTopic','clearToken'}:raise ValueError('Unknown notification setting')
            for key in ('desktop','clearTopic','clearToken'):
                if key in patch and type(patch[key]) is not bool:raise ValueError('Notification switches must be boolean')
            for key in ('topic','token'):
                if key in patch and not isinstance(patch[key],str):raise ValueError('Invalid private notification credential')
                if patch.get('clear'+key.title()):value[key]=''
                elif patch.get(key):value[key]=patch[key]
            for key in ('enabled','server','preview'):
                if key in patch:value[key]=patch[key]
            valid(value)
        except (ValueError,TypeError) as error:
            receipt={'commandId':command,'status':'rejected','executed':False,'reason':str(error),'updatedAt':time.time()}
            with self.db:self.db.execute('INSERT INTO commands VALUES(?,?,?)',(command,fingerprint,json.dumps(receipt)))
            return receipt
        receipt={'commandId':command,'status':'completed','revision':revision+1,'updatedAt':time.time()}
        with self.db:
            self.db.execute('UPDATE settings SET revision=?,value=? WHERE id=1',(revision+1,json.dumps(self.redacted(value))))
            self.db.execute('UPDATE credentials SET topic=?,token=? WHERE id=1',(value['topic'],value['token']))
            self.db.execute('INSERT INTO commands VALUES(?,?,?)',(command,fingerprint,json.dumps(receipt)))
        return receipt

    def persist_delivery(self,value):
        value['updatedAt']=time.time()
        with self.db:self.db.execute('UPDATE deliveries SET status=?,value=?,updated=? WHERE id=?',(value['status'],json.dumps(value),value['updatedAt'],value['commandId']))

    async def enqueue(self,args):
        if set(args)-{'session','eventId','title','text','kind'}:raise ValueError('Unknown completion fields')
        sid=token(args.get('session'),'session',512);event=token(args.get('eventId'),'event identity',200);kind=args.get('kind')
        if kind not in ('response','schedule-attention'):raise ValueError('Trusted completion kind required')
        title=args.get('title') or 'Amplifier'
        if not isinstance(title,str) or len(title)>8192:raise ValueError('Invalid notification title')
        text=args.get('text') or ''
        if not isinstance(text,str) or len(text)>50000:raise ValueError('Invalid bounded completion preview')
        identity='notification:'+signature([sid,event]);fingerprint=signature(args);prior=self.db.execute('SELECT * FROM deliveries WHERE id=?',(identity,)).fetchone()
        if prior:
            if prior['signature']!=fingerprint:raise ValueError('Completion identity changed')
            return {**json.loads(prior['value']),'replayed':False}
        if len(self.tasks)>=32:return {'accepted':False,'executed':False,'reason':'Notification delivery capacity reached'}
        settings=self.public();revision=settings['revision'];status='accepted' if settings['enabled'] else 'disabled'
        value=self.private()[1] if settings['enabled'] else None
        receipt={'commandId':identity,'session':sid,'eventId':event,'kind':kind,'settingsRevision':revision,'status':status,'accepted':status=='accepted','updatedAt':time.time()}
        if status=='disabled':receipt['executed']=False
        with self.db:self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(identity,sid,fingerprint,status,json.dumps(receipt),receipt['updatedAt']))
        if status=='accepted':
            self.intake.background+=1
            task=asyncio.create_task(self.deliver(receipt,value,title[:200],text[:1000] if value['preview'] else 'Your Amplifier response is ready.'));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        return receipt

    async def deliver(self,receipt,value,title,message):
        try:
            async with self.semaphore:
                receipt={**receipt,'status':'dispatching'};receipt.pop('executed',None);self.persist_delivery(receipt)
                status=await self.transport(value,title,message or 'Response ready')
                if type(status) is not int:raise ValueError('No HTTP delivery boundary')
                receipt.update(status='server-accepted' if 200<=status<300 else 'rejected',httpStatus=status,deviceDelivery='unverified')
        except BaseException:
            receipt={**receipt,'status':'unknown','reason':'Delivery outcome unconfirmed; no retry or replay'}
        finally:
            self.persist_delivery(receipt);self.intake.background-=1;await self.notice(self.changed);await self.notice(self.idle)

    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            if exists(self.db,"SELECT 1 FROM deliveries WHERE session=? AND status IN ('accepted','dispatching','unknown') LIMIT 1",(session,)):reasons.append('notification-delivery')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);sessions=args['sessions']
        return base


    async def request(self,method,args):
        if method=='quiescence.retention':return self.retention_references(args)
        if method=='quiescence.managedFiles':return self.managed_references(args)
        if self.closed:raise ValueError('Notifications owner is closed')
        if method=='initialize':return {'protocolVersion':1,'quiescence':{'version':1,'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})}}
        if method=='quiescence/inspect':return {'intakeClosed':bool(self.intake.fence),'fence':self.intake.fence,'calls':self.intake.calls,'background':self.intake.background}
        if method=='quiescence/acquire':return self.intake.acquire(args)
        if method=='quiescence/release':
            try:return self.intake.release(args)
            except ValueError as error:
                error.known_refusal=True
                raise
        operation=args.get('operation') if method=='action' else None
        passive=method=='snapshot' or operation in READS
        if self.intake.fence and not passive:return {'accepted':False,'executed':False,'reason':'Notification intake is held'}
        self.intake.calls+=1
        try:
            if method=='snapshot':return self.public()
            if method=='completion':return await self.enqueue(args)
            if method!='action':raise ValueError('Unknown private notification method')
            inner=args.get('args',{})
            if not isinstance(inner,dict):raise ValueError('Notification arguments must be object')
            if operation=='notifications.get' and not inner:return self.public()
            if operation=='notifications.receipt' and set(inner)=={'commandId'}:return self.exact(inner['commandId'])
            if operation=='notifications.save' and set(inner)=={'patch','expectedRevision'}:
                result=self.save(inner,args.get('commandId'));await self.notice(self.changed);return result
            raise ValueError('Unknown notification action or arguments')
        finally:self.intake.calls-=1;await self.notice(self.idle)

    async def close(self):
        if self.closed:return
        self.closed=True
        await asyncio.gather(*list(self.tasks),return_exceptions=True)
        self.intake.close();self.db.close();self.lease.close()
