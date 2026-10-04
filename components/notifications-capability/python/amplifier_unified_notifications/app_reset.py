"""Private owner-made reset images; no credential value leaves this module."""
import json,time,uuid,hashlib

OWNER='notifications'
PARTS=['notifications.settings','notifications.credentials']
PRESERVED=['notification commands and delivery receipts','canonical native history','shared Amplifier settings and keys.env','client notification permissions','all unselected parts']
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def refuse(message):
    error=ValueError(message);error.known_refusal=True;raise error

class AppReset:
    def __init__(self,owner,default_server):
        self.owner=owner;self.db=owner.db;self.server=default_server
        self.db.execute("CREATE INDEX IF NOT EXISTS app_reset_pending ON app_reset_commands(json_extract(body,'$.state'),json_extract(body,'$.operation'))")
    def revision(self):return str(self.owner.public()['revision'])
    def receipt(self,command):
        row=self.db.execute('SELECT body FROM app_reset_commands WHERE id=?',(command,)).fetchone();return json.loads(row[0]) if row else None
    def load(self,args):
        row=self.db.execute('SELECT body,private FROM app_reset_previews WHERE id=?',(args.get('preparedId'),)).fetchone()
        if not row:refuse('Notification reset review unavailable')
        body=json.loads(row[0])
        if body['reviewHash']!=args.get('reviewHash'):refuse('Notification review identity changed')
        return body,json.loads(row[1])
    def applicable(self,body):return body['revision']==self.revision() and time.time()<body['expiresAt']
    def prepare(self,args):
        parts=args.get('parts')
        if not isinstance(parts,list) or not 1<=len(parts)<=2 or len(set(parts))!=len(parts) or any(p not in PARTS for p in parts) or args.get('privateContentReviewed') is not True:refuse('Explicit supported notification reset parts required')
        if 'notifications.credentials' in parts and args.get('credentialsReviewed') is not True:refuse('Private notification credential retention requires explicit review')
        revision,value=self.owner.private();original=args.get('restoreCommandId')
        if not original and parts==['notifications.credentials'] and value['enabled']:refuse('Select notification settings too before clearing credentials used by enabled delivery')
        if original:
            prior=self.receipt(original)
            if not prior or prior['state']!='succeeded' or prior['operation']!='apply' or prior['result']['postResetRevision']!=str(revision) or prior['result']['parts']!=parts:refuse('Notification state changed after original reset')
        prepared=uuid.uuid4().hex
        body={'ownerId':OWNER,'preparedId':prepared,'parts':parts,'revision':str(revision),'expiresAt':time.time()+600,'containsPrivateContent':True,'credentialsIncluded':'notifications.credentials' in parts,'coverage':'explicit-app-local-parts','preserved':PRESERVED,'omissions':['unselected product configuration','shared credentials'],'restoresCommandId':original,'items':[{'part':p,'operation':'restore' if original else 'reset-to-disabled-defaults' if p.endswith('.settings') else 'clear-topic-and-token'} for p in parts]}
        body['reviewHash']=fingerprint(body)
        selected={}
        if 'notifications.settings' in parts:selected.update({key:value[key] for key in ('enabled','server','preview')})
        if 'notifications.credentials' in parts:selected.update({key:value[key] for key in ('topic','token')})
        with self.db:self.db.execute('INSERT INTO app_reset_previews VALUES(?,?,?)',(prepared,json.dumps(body),json.dumps(selected)))
        return body
    def mutate(self,args,restore=False):
        body,before=self.load(args)
        if not self.applicable(body):refuse('Notification reset review expired or revision changed')
        revision,value=self.owner.private();parts=body['parts']
        if restore:
            prior=self.receipt(args.get('resetCommandId'))
            if not prior or prior['state']!='succeeded' or prior['operation']!='apply' or prior['result']['postResetRevision']!=str(revision) or args.get('expectedPostResetRevision')!=str(revision) or body['restoresCommandId']!=args.get('resetCommandId'):refuse('Notification settings changed after reset; retained values were not restored')
            _,original=self.load(prior['result'])
            keys=[]
            if 'notifications.settings' in parts:keys+=['enabled','server','preview']
            if 'notifications.credentials' in parts:keys+=['topic','token']
            for key in keys:value[key]=original[key]
        else:
            if body['restoresCommandId']:refuse('Restore review cannot authorize reset')
            if 'notifications.settings' in parts:value.update(enabled=False,server=self.server,preview=False)
            if 'notifications.credentials' in parts:
                # Clearing active credentials requires the separately selected
                # settings reset, so no unselected public fields change.
                value.update(topic='',token='')
        result={'ownerId':OWNER,'preparedId':body['preparedId'],'reviewHash':body['reviewHash'],'parts':parts,'postResetRevision':str(revision+1),'restored':restore,'preserved':PRESERVED,'replayed':False}
        with self.db:
            self.db.execute('UPDATE settings SET revision=?,value=? WHERE id=1',(revision+1,json.dumps(self.owner.redacted(value))))
            self.db.execute('UPDATE credentials SET topic=?,token=? WHERE id=1',(value['topic'],value['token']))
            receipt={'ownerId':OWNER,'commandId':args['commandId'],'operation':'restore' if restore else 'apply','state':'succeeded','result':result,'replayed':False}
            self.db.execute('UPDATE app_reset_commands SET body=? WHERE id=?',(json.dumps(receipt),args['commandId']))
        return receipt
    def inspect(self,args):
        if 'commandId' in args:return {'ownerId':OWNER,'receipt':self.receipt(args['commandId']),'replayed':False}
        body,_=self.load(args);return {'ownerId':OWNER,'preparedId':body['preparedId'],'reviewHash':body['reviewHash'],'revision':self.revision(),'applicable':self.applicable(body),'replayed':False}
    def perform(self,operation,args):
        fields={'prepare':{'commandId','parts','privateContentReviewed','credentialsReviewed','restoreCommandId'},'apply':{'commandId','preparedId','reviewHash'},'restore':{'commandId','preparedId','reviewHash','resetCommandId','expectedPostResetRevision'},'inspect':{'commandId','preparedId','reviewHash'}}
        if operation not in fields or not isinstance(args,dict) or set(args)-fields[operation]:refuse('Unknown notification reset operation or field')
        if operation=='inspect':return self.inspect(args)
        command=args.get('commandId')
        if not isinstance(command,str) or not 1<=len(command)<=200:refuse('Durable notification reset command required')
        signature=fingerprint([operation,args]);row=self.db.execute('SELECT signature,body FROM app_reset_commands WHERE id=?',(command,)).fetchone()
        if row:
            if row[0]!=signature:refuse('Notification reset command identity changed')
            receipt=json.loads(row[1]);return {'receipt':receipt,**receipt.get('result',{}),'replayed':False}
        if self.db.execute("SELECT 1 FROM app_reset_commands WHERE json_extract(body,'$.state')='unknown' AND json_extract(body,'$.operation') IN ('apply','restore') LIMIT 1").fetchone():
            receipt={'ownerId':OWNER,'commandId':command,'operation':operation,'state':'refused','executed':False,'reason':'Earlier notification reset remains unknown','replayed':False}
            with self.db:self.db.execute('INSERT INTO app_reset_commands VALUES(?,?,?)',(command,signature,json.dumps(receipt)))
            return {'receipt':receipt,'replayed':False}
        receipt={'ownerId':OWNER,'commandId':command,'operation':operation,'state':'unknown','replayed':False}
        with self.db:self.db.execute('INSERT INTO app_reset_commands VALUES(?,?,?)',(command,signature,json.dumps(receipt)))
        try:
            if operation=='prepare':receipt.update(state='succeeded',result=self.prepare(args))
            else:receipt=self.mutate(args,operation=='restore')
        except Exception as error:
            if not getattr(error,'known_refusal',False):raise
            receipt.update(state='refused',executed=False,reason=str(error))
        with self.db:self.db.execute('UPDATE app_reset_commands SET body=? WHERE id=?',(json.dumps(receipt),command))
        return {'receipt':receipt,**receipt.get('result',{}),'replayed':False}
