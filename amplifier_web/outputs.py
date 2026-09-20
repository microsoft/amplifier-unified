"""Typed output links and immutable evidence on existing private resources."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from urllib.parse import urlsplit

from amplifier_outputs import OutputRegistry
from amplifier_outputs.git_review import anchors, snapshot as git_snapshot

MAX_FILE = 8*1024*1024
VARIANTS = ['standard','document','email','chat_message','social_post']


def definitions(schema,string):
    common={'sessionId':string(200)}
    identity={**common,'id':string(100)}
    origin={'messageId':string(200),'parentId':string(100),'evidenceIds':{'type':'array','maxItems':20,'items':string(100)}}
    writing={'content':string(100000),'variant':{'enum':VARIANTS},'subject':string(500)}
    return {
        'outputs.list':('List exact saved output relationships without opening or sending anything.',schema({**common,'includeUnlinked':{'type':'boolean'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50}},['sessionId'])),
        'outputs.read':('Read bounded immutable content, provenance, lineage and local review comments. External links are references only unless version evidence is supplied.',schema({**identity,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':4000},'commentOffset':{'type':'integer','minimum':0},'commentLimit':{'type':'integer','minimum':1,'maximum':10}},['sessionId','id'])),
        'outputs.attach':('Attach a file/dataset snapshot, exact saved canvas body, PR or external document reference. Does not publish, fetch remote contents or change selection. File snapshots stay within this conversation workspace.',schema({**common,**origin,'kind':{'enum':['file','dataset','canvas','pull_request','external_document']},'title':string(200),'path':string(4000),'url':string(4000),'canvasId':string(100),'version':string(500),'expectedSha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','kind','title'])),
        'outputs.write':('Save a reusable writing output or an immutable next version. Does not send, publish or overwrite any original.',schema({**common,**origin,**writing,'title':string(200)},['sessionId','title','content','variant'])),
        'outputs.review':('Snapshot a bounded read-only local Git diff for review. No changes applied; untracked and binary content excluded. Branch mode resolves existing base and HEAD commits.',schema({**common,**origin,'mode':{'enum':['unstaged','staged','branch']},'base':string(200),'path':string(4000),'title':string(200)},['sessionId','mode'])),
        'outputs.unlink':('Unlink an output from the active list. The underlying object, snapshot and comments are retained.',schema({**identity,'expectedRevision':{'type':'integer','minimum':1}},['sessionId','id','expectedRevision'])),
        'outputs.relink':('Restore a previously unlinked output relationship.',schema({**identity,'expectedRevision':{'type':'integer','minimum':1}},['sessionId','id','expectedRevision'])),
        'outputs.comment':('Save a local review comment on an exact saved version. It is not posted to a remote PR. Diff locations must match a displayed saved hunk.',schema({**identity,'body':{**string(8000),'minLength':1},'path':string(4000),'side':{'enum':['left','right']},'line':{'type':'integer','minimum':1}},['sessionId','id','body'])),
    }


def file_bytes(root,value):
    root=Path(root).resolve()
    path=Path(value).expanduser()
    path=(path if path.is_absolute() else root/path).resolve()
    try:relative=path.relative_to(root)
    except ValueError:raise ValueError('Output files must stay inside this conversation workspace.') from None
    if not relative.parts:raise ValueError('Choose a file.')
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        fd=os.open(relative.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_FILE:
                raise ValueError('Choose a regular output file up to8MB.')
            data=stream.read(MAX_FILE+1)
            after=os.fstat(stream.fileno())
            if len(data)>MAX_FILE or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
                raise ValueError('The output changed while reading. Attach it again deliberately.')
        return data,str(path),relative.name
    finally:os.close(directory)


class Outputs:
    def __init__(self,app):
        self.app=app
        self.store=OutputRegistry(app.db)

    def record(self,sid,identity):
        record=self.store.read(identity)
        if record['sessionId']!=sid:raise ValueError('This output belongs to another conversation.')
        return record

    def content(self,record):
        if not record.get('body'):return None
        value=self.app.state_resource(record['body']['$resource'])
        data=base64.b64decode(value['data'],validate=True) if value['encoding']=='base64' else value['data'].encode()
        if hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Saved output content no longer matches its evidence hash.')
        return data

    def fork(self,source_id,target):
        kept={row['id'] for row in target.get('messages',[]) if row.get('id')}
        rows=self.app.db.execute('SELECT value FROM output_records WHERE session_id=? ORDER BY created',(source_id,)).fetchall()
        mapping={}
        for saved, in rows:
            original=json.loads(saved)
            if not original.get('linked') or original.get('origin',{}).get('messageId') not in kept:
                continue
            value={key:copy.deepcopy(value) for key,value in original.items() if key not in {'id','sessionId','createdAt','revision','version','linked','parentId','evidenceIds'}}
            value['forkSource']={'sessionId':source_id,'outputId':original['id'],'sha256':original.get('sha256')}
            value['parentId']=mapping.get(original.get('parentId'))
            value['evidenceIds']=[mapping[key] for key in original.get('evidenceIds',[]) if key in mapping]
            clone=self.store.create(target['id'],value);mapping[original['id']]=clone['id']
            for comment in self.store.comments(original['id']):
                self.store.comment(clone['id'],{**comment,'forkSourceCommentId':comment['id']})

    async def dispatch(self,action,args,origin,command_id):
        from .service import AppError
        try:
            async with self.app.lock:
                session=copy.deepcopy(self.app._session(args['sessionId']))
                if action=='outputs.list':
                    return {'accepted':True,'result':self.store.list(session['id'],include_unlinked=args.get('includeUnlinked',False),offset=args.get('offset',0),limit=args.get('limit',20))}
                if action=='outputs.read':
                    record=self.record(session['id'],args['id']);data=self.content(record)
                    comments=self.store.comments(record['id']);start=args.get('commentOffset',0);count=args.get('commentLimit',5)
                    result={**record,'comments':comments[start:start+count],'nextCommentOffset':start+count if start+count<len(comments) else None}
                    if data is not None:
                        result['downloadUrl']='/api/outputs/'+record['id']+'/content'
                        try:
                            text=data.decode('utf-8');offset=args.get('offset',0);limit=args.get('limit',4000)
                            result.update(text=text[offset:offset+limit],offset=offset,nextOffset=offset+limit if offset+limit<len(text) else None)
                        except UnicodeDecodeError:result['binary']=True
                    return {'accepted':True,'result':result}
                request=[action,args,origin];identity=command_id or uuid.uuid4().hex
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                for reference in args.get('evidenceIds',[]):self.record(session['id'],reference)
                if args.get('parentId'):self.record(session['id'],args['parentId'])
                if args.get('messageId') and not any(row.get('id')==args['messageId'] for row in session.get('messages',[])):
                    raise ValueError('Choose an original message visible in this conversation; no turn was inferred.')
            data=None
            value={'kind':args.get('kind','writing' if action=='outputs.write' else 'git_review'),
                'title':args.get('title') or 'Saved Git review','origin':{'source':origin,'messageId':args.get('messageId')},
                'parentId':args.get('parentId'),'evidenceIds':args.get('evidenceIds',[])}
            workspace=session.get('workingDirectory') or session.get('workspace')
            if action=='outputs.attach':
                kind=args['kind']
                expected_field='path' if kind in {'file','dataset'} else 'canvasId' if kind=='canvas' else 'url'
                if not args.get(expected_field) or any(args.get(key) for key in {'path','url','canvasId'}-{expected_field}):
                    raise ValueError('Provide exactly the location matching this output kind.')
                if kind in {'file','dataset'}:
                    data,path,name=await asyncio.to_thread(file_bytes,workspace,args['path'])
                    value.update(path=path,filename=name,versionEvidence='snapshot')
                elif kind=='canvas':
                    async with self.app.lock:
                        canvas=next((r for r in self.app.state.get('canvasArtifacts',[]) if r['id']==args['canvasId'] and r.get('sessionId')==session['id']),None)
                        if not canvas:raise ValueError('This saved canvas is unavailable in the conversation.')
                        body=self.app.state_resource(canvas['body']['$resource'])
                        data=json.dumps(body,ensure_ascii=False,sort_keys=True).encode()
                        value.update(canvasId=canvas['id'],canvasKind=canvas['kind'],canvasSource=copy.deepcopy(canvas['body']),filename='canvas.json',versionEvidence='snapshot')
                else:
                    parsed=urlsplit(args['url'])
                    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<32 for c in args['url']):
                        raise ValueError('Choose an HTTP(S) link without credentials or control characters.')
                    value.update(url=args['url'],externalVersion=args.get('version'),versionEvidence='user_supplied_reference' if args.get('version') else 'unversioned_reference')
            elif action=='outputs.write':
                if not args['content'].strip():raise ValueError('Writing content is empty.')
                data=args['content'].encode();value.update(variant=args['variant'],subject=args.get('subject'),filename='writing.md',versionEvidence='snapshot')
            elif action=='outputs.review':
                path=args.get('path')
                if path and (Path(path).is_absolute() or '..' in Path(path).parts):raise ValueError('Review paths must be relative to the conversation workspace.')
                review=await git_snapshot(workspace,args['mode'],args.get('base'),path)
                data=review.pop('text').encode();value.update(review=review,filename='review.diff',versionEvidence='snapshot')
            async with self.app.lock:
                self.app._session(session['id'])
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                if action in {'outputs.unlink','outputs.relink'}:
                    self.record(session['id'],args['id'])
                    result=self.store.link(args['id'],args['expectedRevision'],action=='outputs.relink')
                elif action=='outputs.comment':
                    record=self.record(session['id'],args['id'])
                    location={key:args[key] for key in ('path','side','line') if key in args}
                    if location:
                        if len(location)!=3 or record['kind']!='git_review' or (args.get('path'),args.get('side'),args.get('line')) not in anchors(self.content(record).decode()):
                            raise ValueError('Choose a line in this exact saved diff.')
                    result=self.store.comment(record['id'],{'body':args['body'],**location,'source':origin,'sha256':record.get('sha256'),'externalPosted':False})
                else:
                    if data is not None:
                        digest=hashlib.sha256(data).hexdigest()
                        if args.get('expectedSha256') and digest!=args['expectedSha256']:
                            raise ValueError('File contents changed from the expected version.')
                        from .resource_files import put
                        try:body={'encoding':'utf-8','data':data.decode('utf-8')}
                        except UnicodeDecodeError:body={'encoding':'base64','data':base64.b64encode(data).decode()}
                        value.update(body=put(self.app.db,body),sha256=digest,bytes=len(data))
                    result=self.store.create(session['id'],value)
                self.store.remember(identity,request,result)
                self.app._save()
                return {'accepted':True,'result':result}
        except (ValueError,KeyError,OSError) as exc:
            raise AppError(str(exc),409) from exc
