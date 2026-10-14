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
    image_identity=schema({'id':string(100),'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['id','sha256'])
    return {
        'outputs.list':('List exact saved output relationships without opening or sending anything.',schema({**common,'includeUnlinked':{'type':'boolean'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50}},['sessionId'])),
        'outputs.read':('Read bounded immutable content, provenance, lineage and local review comments. External links are references only unless version evidence is supplied.',schema({**identity,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':4000},'commentOffset':{'type':'integer','minimum':0},'commentLimit':{'type':'integer','minimum':1,'maximum':10}},['sessionId','id'])),
        'outputs.image':('Inspect an exact saved RGB/RGBA PNG snapshot up to8MB and4096px per side. UI previews it; a direct app_control call (not nested inside tool_exec) requests typed pixels for its next vision-capable model request. The receipt alone is not visual evidence. Does not read changing source files or start work.',schema({**identity,'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','id','sha256'])),
        'outputs.images':('Inspect two saved PNGs together, in the supplied order, for visual comparison. Choose distinct images with exact id and sha256, up to8MB combined and4096px per side. Call directly through app_control to replace the current image selection for this input. Both images must be available; the receipt alone is not pixels. Does not generate or start work.',schema({**common,'images':{'type':'array','minItems':2,'maxItems':2,'items':image_identity}},['sessionId','images'])),
        'outputs.attach':('Attach a file/dataset snapshot, exact saved canvas body, PR or external document reference. Does not publish, fetch remote contents or change selection. File snapshots stay within this conversation workspace.',schema({**common,**origin,'kind':{'enum':['file','dataset','canvas','pull_request','external_document']},'title':string(200),'path':string(4000),'url':string(4000),'canvasId':string(100),'version':string(500),'expectedSha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','kind','title'])),
        'outputs.attachImage':('Save an exact generated PNG from a completed image-tool receipt in this workspace. Verifies bytes and SHA-256. Edits require parentId matching the target input hash. Shares the saved image with Canvas; messageId publishes it inline at that original turn, otherwise library-only. Preserves originals and producer-reported provenance; does not generate, spend, fetch or publish externally.',schema({**common,**origin,'title':string(200),'receiptPath':string(4000)},['sessionId','title','receiptPath'])),
        'outputs.attachMedia':('Save exact GIF bytes up to64MiB or MP4 up to32MiB at an existing assistant message. Supply its saved messageId and inspected SHA-256; saving is not browser playback proof.',schema({**common,'messageId':string(200),'title':string(200),'path':string(4000),'expectedSha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},['sessionId','messageId','title','path','expectedSha256'])),
        'outputs.mediaReport':('Authenticated UI media observation, not model attestation; never attaches or starts work. Control state alone does not prove advancement; presentedFrames/mediaTime describe actual presentation callbacks.',schema({**identity,'messageId':string(200),'version':{'type':'integer','minimum':1},'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'generation':{'type':'integer','minimum':1},'sequence':{'type':'integer','minimum':1},'renderState':{'enum':['loading','ready','error']},'playback':{'enum':['paused','playing','ended']},'presentedFrames':{'type':'integer','minimum':0},'mediaTime':{'type':'number','minimum':0},'detail':string(500)},['sessionId','id','messageId','version','sha256','generation','sequence','renderState','playback'])),
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
        from .output_media import Media
        self.media=Media(self)

    def record(self,sid,identity):
        record=self.store.read(identity)
        if record['sessionId']!=sid:raise ValueError('This output belongs to another conversation.')
        return record

    def content(self,record):
        if not record.get('body'):return None
        value=self.app.state_resource(record['body']['$resource'])
        if record.get('bodyFormat') == 'canvas-image-v1':
            prefix = 'data:image/png;base64,'
            content = value.get('content')
            if not isinstance(content, str) or not content.startswith(prefix):
                raise ValueError('Saved image source is unavailable.')
            data = base64.b64decode(content[len(prefix):], validate=True)
        else:
            data=base64.b64decode(value['data'],validate=True) if value['encoding']=='base64' else value['data'].encode()
        if hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Saved output content no longer matches its evidence hash.')
        return data

    def image(self,sid,identity,expected_hash):
        self.app._session(sid, hydrate=False)
        record=self.record(sid,identity)
        if record.get('sha256')!=expected_hash or record.get('kind') not in {'file','dataset'}:
            raise ValueError('Choose the exact saved file snapshot and content hash.')
        data=self.content(record)
        if data is None:raise ValueError('This output has no saved image bytes.')
        from .voice_visual_image import validate_png
        width,height=validate_png(data,max_bytes=MAX_FILE,max_dimension=4096)
        return {'id':identity,'sha256':expected_hash,'title':record['title'],'width':width,'height':height,
            'mimeType':'image/png','bytes':len(data),'evidence':'immutable-output-snapshot','untrustedData':True,
            'imageUrl':'/api/outputs/'+identity+'/image','_image':base64.b64encode(data).decode()}

    def retained_thumbnail(self, identity, values):
        """Exact published generation read; no view admission or history lookup."""
        from .artifact_presentation import check_output, generation_binding, output_bytes
        from .canvas_versions import definition
        from .voice_visual_image import validate_png
        sid, aid, mid = (values.get(key) for key in ('sessionId', 'artifactId', 'messageId'))
        if any(not isinstance(value, str) or not value or len(value) > limit
               for value, limit in ((sid, 200), (aid, 100), (mid, 200))):
            raise ValueError('Supply the complete saved thumbnail tuple.')
        raw_version = values.get('version', '')
        if (not isinstance(raw_version, str) or not raw_version.isascii()
                or not raw_version.isdecimal() or len(raw_version) > 15
                or str(int(raw_version)) != raw_version or int(raw_version) < 1):
            raise ValueError('Choose a positive integer exact saved thumbnail version.')
        version = int(raw_version)
        self.app._session(sid, hydrate=False)
        client_id = self.app.clients.current.get()
        if client_id is not None:
            client = self.app.clients.records[client_id]
            if client.get('selectedSessionId') != sid:
                raise ValueError('Open the thumbnail owning conversation in this client.')
        row = next((row for row in self.app._state.get('canvasArtifacts', [])
                    if row['id'] == aid and row.get('sessionId') == sid), None)
        if row is None:
            raise ValueError('This thumbnail artifact is unavailable in its conversation.')
        if client_id is not None and row.get('workspaceId') != client.get('selectedWorkspaceId'):
            raise ValueError('Open the thumbnail owning workspace in this client.')
        saved = definition(row, version)
        links = [link for link in row.get('publications', [])
                 if link.get('messageId') == mid and type(link.get('version')) is int
                 and link['version'] == version]
        if (len(links) != 1 or links[0].get('outputId') != identity
                or links[0].get('chat', 'inline') != 'inline'):
            raise ValueError('This exact output version is not published in the image gallery.')
        record = self.record(sid, identity)
        marker = generation_binding(record, row, version)
        if (not record.get('linked') or record.get('kind') != 'file' or marker is None
                or record.get('origin', {}).get('messageId') != mid
                or ('imageGeneration' in links[0] and links[0]['imageGeneration'] != marker)):
            raise ValueError('The canonical saved image-generation relationship is unavailable.')
        if 'outputBinding' in saved:
            check_output(saved['outputBinding'], record, sid)
        else:
            # Pre-adapter definitions have no outputBinding. Qualify the
            # canonical join, including ambiguity, without reading other bodies.
            candidates = self.app.db.execute(
                "SELECT value FROM output_records WHERE session_id=? "
                "AND json_extract(value,'$.canvasId')=? "
                "AND json_extract(value,'$.bodyFormat')='canvas-image-v1'",
                (sid, aid)).fetchall()
            matched = [json.loads(value) for value, in candidates
                       if generation_binding(json.loads(value), row, version)]
            if len(matched) != 1 or matched[0]['id'] != identity:
                raise ValueError('The canonical saved image relationship is ambiguous or unavailable.')
        if 'sha256' in values and values['sha256'] != record.get('sha256'):
            raise ValueError('The thumbnail hash differs from its exact saved output.')
        # Only this requested byte boundary opens the existing canonical body.
        data = output_bytes(self.app.db, record)
        validate_png(data, max_bytes=MAX_FILE, max_dimension=4096)
        return data, record['sha256']

    def images(self,sid,identities):
        if (not isinstance(identities,list) or len(identities)!=2 or
                any(not isinstance(item,dict) or set(item)!={'id','sha256'} or
                    any(not isinstance(item[key],str) for key in ('id','sha256')) for item in identities)):
            raise ValueError('Choose exactly two saved image IDs and hashes.')
        if len({item['id'] for item in identities})!=2 or len({item['sha256'] for item in identities})!=2:
            raise ValueError('Choose two different saved images; duplicate IDs or hashes cannot form a comparison.')
        # The caller holds the app lock. Validate the whole ordered set before
        # returning any pixels or changing the worker's inspection selection.
        images=[self.image(sid,item['id'],item['sha256']) for item in identities]
        total=sum(image['bytes'] for image in images)
        if total>MAX_FILE:
            raise ValueError('The two saved images exceed the combined8MB limit.')
        return {'images':images,'bytes':total,'limits':{'images':2,'maxBytes':MAX_FILE,'maxSide':4096},
                'imageDelivery':'Both saved images are available. A direct agent inspection requests pixels together; this receipt is not pixels.'}

    def validate_canvas_adapter(self, row, sid):
        """First-copy preflight; malformed retained adapters are explicit skips."""
        from .artifact_presentation import check_output, validate_definition
        from .output_media import validate_media_reference
        relevant = row.get('kind') in {'gif', 'mp4'} or row.get('outputBinding') or any(
            isinstance(v, dict) and (v.get('kind') in {'gif', 'mp4'} or v.get('outputBinding'))
            for v in row.get('versions', []) or [])
        if not relevant:
            return
        versions, links = row.get('versions', []), row.get('publications', [])
        if (not isinstance(versions, list) or any(not isinstance(v, dict) for v in versions)
                or not isinstance(links, list) or any(not isinstance(link, dict) for link in links)):
            raise ValueError('Retained adapter versions and publications must be saved definitions.')
        identities = [v.get('version') for v in versions]
        if (type(row.get('revision')) is not int or row['revision'] < 1
                or any(type(v) is not int or v < 1 for v in identities)
                or len(set(identities)) != len(identities)
                or any(type(link.get('version')) is not int or link['version'] not in identities for link in links)):
            raise ValueError('Retained adapter has an invalid or missing saved version.')
        for entry in [row, *versions]:
            kind = entry.get('kind', row.get('kind'))
            if entry.get('outputBinding'):
                binding = entry['outputBinding']
                record = self.record(sid, binding['outputId'])
                check_output(binding, record, sid)
                body = self.app.state_resource(entry['body']['$resource'])
                source = self.app.state_resource(record['canvasSource']['$resource']) if record.get('canvasSource') else None
                validate_definition(entry, row, record, body, source)
            if kind not in {'gif', 'mp4'}:
                continue
            if 'mediaReference' in entry or not entry.get('body'):
                raise ValueError('Retained media adapter requires one exact saved body.')
            values = []
            for key in ('body', 'contentResource'):
                if entry.get(key):
                    body = self.app.state_resource(entry[key]['$resource'])
                    if not isinstance(body, dict) or set(body) != {'mediaReference'}:
                        raise ValueError('Retained media adapter has an invalid saved body.')
                    reference = body['mediaReference']
                    if not isinstance(reference, dict) or not isinstance(reference.get('item'), dict):
                        raise ValueError('Retained media adapter has a malformed reference.')
                    record = self.record(sid, reference.get('item', {}).get('id'))
                    validate_media_reference(reference, record, sid, kind=kind)
                    values.append(body)
            if any(value != values[0] for value in values):
                raise ValueError('Retained media adapter has conflicting saved bodies.')

    def fork(self,source_id,target,canvas_mapping=None,*,media_origins=None,artifact_origins=None,adapter_skips=None):
        kept={row['id'] for row in target.get('messages',[]) if row.get('id')}
        rows=self.app.db.execute('SELECT value FROM output_records WHERE session_id=? ORDER BY created',(source_id,)).fetchall()
        mapping={}
        originals = [json.loads(saved) for saved, in rows]
        skipped = list(adapter_skips or [])
        invalid_canvas = {item['canvasId'] for item in skipped if 'canvasId' in item}
        originals_by_id = {row['id']: row for row in originals}
        published_library_outputs = set()
        copied_canvases = {value: key for key, value in (canvas_mapping or {}).items()}
        source_canvases = {row['id']: row for row in self.app.state.get('canvasArtifacts', [])
                           if row.get('sessionId') == source_id}
        # Canvas has already applied the canonical cutoff. Only its surviving,
        # exact admitted publications can bring an originally unanchored output.
        # Do not fabricate an origin or carry arbitrary library/future records.
        from .artifact_presentation import output_binding, generation_binding, check_output, output_bytes

        def library_dependencies(record):
            """Follow only exact retained snapshot references, never the library."""
            seen, required = set(), set()
            while record.get('canvasSource'):
                if record['id'] in seen:
                    raise ValueError('Cyclic retained output snapshot dependency.')
                seen.add(record['id'])
                body = self.app.state_resource(record['canvasSource']['$resource'])
                if json.loads(output_bytes(self.app.db, record)) != body:
                    raise ValueError('Output snapshot differs from its admitted source.')
                if not isinstance(body, dict) or 'outputReference' not in body:
                    break
                if set(body) != {'outputReference'}:
                    raise ValueError('Ambiguous output snapshot dependency.')
                binding = body['outputReference']
                dependency = originals_by_id.get(binding.get('outputId')) if isinstance(binding, dict) else None
                if dependency is None or not dependency.get('linked'):
                    raise ValueError('Output snapshot dependency is unavailable.')
                check_output(binding, dependency, source_id)
                # Anchored dependencies still go through the normal cutoff and
                # assistant-media guards. Only null provenance needs this route.
                if dependency.get('origin', {}).get('messageId') is None:
                    required.add(dependency['id'])
                record = dependency
            return required

        for row in self.app.state.get('canvasArtifacts', []):
            if row.get('sessionId') != target['id'] or row.get('id') not in copied_canvases:
                continue
            source_row = source_canvases[copied_canvases[row['id']]]
            for link in row.get('publications', []):
                record = originals_by_id.get(link.get('outputId'))
                if not record or not record.get('linked') or link.get('messageId') not in kept:
                    continue
                entry = next((v for v in row.get('versions', []) if v.get('version') == link.get('version')), None)
                if entry and (entry.get('outputBinding') == output_binding(record)
                              or ('outputBinding' not in entry and generation_binding(
                                  record, source_row, link['version']))):
                    try:
                        dependencies = library_dependencies(record)
                    except (ValueError, KeyError, TypeError, OSError) as exc:
                        skipped.append({'outputId': record['id'], 'reason': str(exc)})
                        continue
                    published_library_outputs.update(dependencies)
                    if record.get('origin', {}).get('messageId') is None:
                        published_library_outputs.add(record['id'])

        def clone(original, value):
            value['parentId']=mapping.get(original.get('parentId'))
            value['evidenceIds']=[mapping[key] for key in original.get('evidenceIds',[]) if key in mapping]
            value.pop('canvasId', None)
            value.pop('presentationArtifact', None)
            if canvas_mapping and original.get('canvasId') in canvas_mapping:
                value['canvasId'] = canvas_mapping[original['canvasId']]
            saved=self.store.create(target['id'],value)
            mapping[original['id']]=saved['id']
            for comment in self.store.comments(original['id']):
                self.store.comment(saved['id'],{**comment,'forkSourceCommentId':comment['id']})

        pending = []
        # Media mappings must exist before validating adapters, independently
        # of legacy creation order. No dependent output reference is published
        # until its Canvas mapping has survived the checks below.
        for original in originals:
            if not original.get('linked'):
                continue
            message_id = original.get('origin', {}).get('messageId')
            if original.get('kind') == 'media':
                if media_origins is not None:
                    # Exact transcript-owner preflight, including old windows
                    # and inserted receipt/bundle index shifts. No text search.
                    message_id = media_origins.get(message_id)
                    if message_id is None:
                        continue
                elif not any(row.get('id') == message_id and row.get('role') == 'assistant'
                             and not row.get('observation') and not row.get('observation_id')
                             for row in target.get('messages', [])):
                    continue
            else:
                message_id = artifact_origins.get(message_id) if artifact_origins is not None else message_id
                if message_id not in kept and original['id'] not in published_library_outputs:
                    continue
            value={key:copy.deepcopy(value) for key,value in original.items() if key not in {'id','sessionId','createdAt','revision','version','linked','parentId','evidenceIds'}}
            value['forkSource']={'sessionId':source_id,'outputId':original['id'],'sha256':original.get('sha256')}
            value['origin'] = {**value.get('origin', {}), 'messageId': message_id}
            if original.get('kind') == 'media':
                value['origin'] = {**value['origin'], 'messageId': message_id}
                clone(original, value)
            else:
                pending.append((original, value))
        # Canvas cutoff filtering ran first. Qualify every retained GIF version
        # against surviving output IDs, not equal text or source pointers.
        if canvas_mapping:
            for source_canvas, target_canvas in list(canvas_mapping.items()):
                row = next((r for r in self.app.state.get('canvasArtifacts', [])
                            if r['id'] == target_canvas and r.get('sessionId') == target['id']), None)
                if not row or row.get('kind') not in {'gif', 'mp4'}:
                    continue
                rewritten = copy.deepcopy(row)
                try:
                    versions = rewritten.get('versions', [])
                    if not isinstance(versions, list) or any(not isinstance(entry, dict) for entry in versions):
                        raise ValueError('GIF adapter versions must be saved definitions.')
                    for entry in [rewritten, *versions]:
                        if 'mediaReference' in entry or not entry.get('body'):
                            raise ValueError('GIF adapter requires an exact saved body.')
                        references = []
                        for key in ('body', 'contentResource'):
                            if entry.get(key):
                                body = self.fork_media_body(entry[key], source_id, target['id'], mapping,
                                                            kind=entry.get('kind', rewritten['kind']))
                                if body is None:
                                    raise ValueError('GIF adapter has no surviving owning output.')
                                references.append(body['$resource'])
                                entry[key] = body
                        if len(set(references)) != 1:
                            raise ValueError('GIF adapter has conflicting saved references.')
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    self.app.state['canvasArtifacts'].remove(row)
                    canvas_mapping.pop(source_canvas)
                    skipped.append({'canvasId': source_canvas, 'reason': str(exc)})
                else:
                    row.update(rewritten)

        for original, value in pending:
            if original.get('canvasId') in invalid_canvas:
                skipped.append({'outputId': original['id'], 'reason': 'Its retained Canvas adapter is invalid.'})
                continue
            if original.get('canvasKind') in {'gif', 'mp4'}:
                try:
                    # Validate both original immutable snapshot and source
                    # adapter before creating any target output relationship.
                    source = original.get('canvasSource')
                    if (not source or json.loads(self.content(original)) !=
                            self.app.state_resource(source['$resource'])):
                        raise ValueError('GIF output snapshot differs from its source adapter.')
                    remapped = self.fork_media_body(source, source_id, target['id'], mapping,
                                                    kind=original['canvasKind'])
                    if remapped is None:
                        raise ValueError('GIF output adapter has no surviving owning output.')
                    if original.get('canvasId') in invalid_canvas:
                        raise ValueError('GIF output Canvas adapter did not survive fork validation.')
                    from .resource_files import put
                    body = self.app.state_resource(remapped['$resource'])
                    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
                    value.update(canvasSource=remapped,
                                 body=put(self.app.db, {'encoding': 'utf-8', 'data': data.decode()}),
                                 sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    skipped.append({'outputId': original['id'], 'reason': str(exc)})
                    continue
            elif original.get('canvasSource'):
                try:
                    source = original['canvasSource']
                    body = self.app.state_resource(source['$resource'])
                    if 'outputReference' in body:
                        from .artifact_presentation import check_output, output_binding
                        binding = body['outputReference']
                        check_output(binding, self.record(source_id, binding['outputId']), source_id)
                        if binding['outputId'] not in mapping:
                            raise ValueError('Output snapshot has no surviving owning output.')
                        from .resource_files import put
                        remapped = {'outputReference': output_binding(self.record(target['id'], mapping[binding['outputId']]))}
                        data = json.dumps(remapped, ensure_ascii=False, sort_keys=True).encode()
                        value.update(canvasSource=put(self.app.db, remapped),
                                     body=put(self.app.db, {'encoding': 'utf-8', 'data': data.decode()}),
                                     sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    skipped.append({'outputId': original['id'], 'reason': str(exc)})
                    continue
            clone(original, value)
        # Exact output and Canvas versions are different namespaces. Rebind
        # definitions and publications only after owning relationships exist.
        if canvas_mapping:
            from .artifact_presentation import output_binding
            from .resource_files import put
            for source_canvas, target_canvas in list(canvas_mapping.items()):
                row = next(r for r in self.app.state['canvasArtifacts'] if r['id'] == target_canvas)
                failed = False
                for entry in [row, *row.get('versions', [])]:
                    binding = entry.get('outputBinding')
                    if not binding:
                        continue
                    identity = mapping.get(binding['outputId'])
                    if identity is None:
                        failed = True
                        break
                    entry['outputBinding'] = output_binding(self.record(target['id'], identity))
                    for key in ('body', 'contentResource'):
                        if not entry.get(key):
                            continue
                        body = self.app.state_resource(entry[key]['$resource'])
                        if isinstance(body, dict) and 'outputReference' in body:
                            old_binding = body['outputReference']
                            nested_id = mapping.get(old_binding['outputId'])
                            if nested_id is None:
                                failed = True
                                break
                            entry[key] = put(self.app.db, {'outputReference': output_binding(
                                self.record(target['id'], nested_id))})
                if failed:
                    self.app.state['canvasArtifacts'].remove(row)
                    canvas_mapping.pop(source_canvas)
                    skipped.append({'canvasId': source_canvas, 'reason': 'No surviving exact output binding.'})
                    continue
                links = []
                for link in row.get('publications', []):
                    old = link.get('outputId')
                    if old and old not in mapping:
                        continue
                    if old:
                        link['outputId'] = mapping[old]
                    if link.get('imageGeneration', {}).get('parentId'):
                        link['imageGeneration']['parentId'] = mapping.get(link['imageGeneration']['parentId'])
                    links.append(link)
                row['publications'] = links
        for source_output, target_output in mapping.items():
            original = next(r for r in originals if r['id'] == source_output)
            mapped = original.get('presentationArtifact')
            if not mapped or not canvas_mapping or mapped.get('id') not in canvas_mapping:
                continue
            target_canvas = canvas_mapping[mapped['id']]
            canvas = next(r for r in self.app.state['canvasArtifacts'] if r['id'] == target_canvas)
            record = self.record(target['id'], target_output)
            from .artifact_presentation import output_binding
            if any(entry.get('version') == mapped.get('version')
                   and entry.get('outputBinding') == output_binding(record)
                   for entry in canvas.get('versions', [])):
                record['presentationArtifact'] = {'id': target_canvas, 'version': mapped['version'],
                                                   'outputVersion': record['version']}
                self.app.db.execute('UPDATE output_records SET value=? WHERE id=?', (json.dumps(record), target_output))
        return skipped

    def fork_media_body(self, body, source_id, target_id, mapping, *, kind='gif'):
        """Remap only a compact GIF adapter; never clone its media bytes."""
        from .output_media import validate_media_reference
        from .resource_files import put
        if not body:
            return None
        value = self.app.state_resource(body['$resource'])
        if not isinstance(value, dict) or set(value) != {'mediaReference'}:
            return None
        reference = value['mediaReference']
        if not isinstance(reference, dict) or not isinstance(reference.get('item'), dict):
            return None
        source_output = reference['item'].get('id')
        if source_output not in mapping:
            return None
        original = self.record(source_id, source_output)
        validate_media_reference(reference, original, source_id, kind=kind)
        clone = self.record(target_id, mapping[source_output])
        fields = ('id', 'sessionId', 'title', 'filename', 'version', 'sha256', 'bytes', 'media')
        remapped = {'item': {key: copy.deepcopy(clone[key]) for key in fields},
                    'messageId': clone['origin']['messageId']}
        validate_media_reference(remapped, clone, target_id, kind=kind)
        return put(self.app.db, {'mediaReference': remapped})

    async def dispatch(self,action,args,origin,command_id,*,image_identity=None):
        from .service import AppError
        try:
            if action == 'outputs.attachMedia':
                return await self.media.attach(args, origin, command_id)
            if action == 'outputs.mediaReport':
                return await self.media.observe(args, origin)
            if action == 'outputs.read':
                async with self.app.lock:
                    record = self.record(args['sessionId'], args['id'])
                    media = record.get('kind') == 'media'
                if media:
                    # Metadata-only indexed validation; never hydrate history
                    # or decode a media body for the agent's observation read.
                    return {'accepted': True, 'result': await self.media.metadata(
                        args['sessionId'], args['id'], args)}
            async with self.app.lock:
                session=copy.deepcopy(self.app._session(args['sessionId']))
                if action=='outputs.list':
                    return {'accepted':True,'result':self.store.list(session['id'],include_unlinked=args.get('includeUnlinked',False),offset=args.get('offset',0),limit=args.get('limit',20))}
                if action=='outputs.image':
                    result=self.image(session['id'],args['id'],args['sha256']);result.pop('_image')
                    result['imageDelivery']='Agent requests receive typed pixels at the next supported model boundary; this receipt is not pixels.'
                    return {'accepted':True,'result':result}
                if action=='outputs.images':
                    result=self.images(session['id'],args['images'])
                    for image in result['images']:image.pop('_image')
                    return {'accepted':True,'result':result}
                if action=='outputs.read':
                    record=self.record(session['id'],args['id'])
                    data=self.content(record)
                    comments=self.store.comments(record['id']);start=args.get('commentOffset',0);count=args.get('commentLimit',5)
                    result={**record,'comments':comments[start:start+count],'nextCommentOffset':start+count if start+count<len(comments) else None}
                    if data is not None:
                        result['downloadUrl']='/api/outputs/'+record['id']+'/content'
                        try:
                            text=data.decode('utf-8');offset=args.get('offset',0);limit=args.get('limit',4000)
                            result.update(text=text[offset:offset+limit],offset=offset,nextOffset=offset+limit if offset+limit<len(text) else None)
                        except UnicodeDecodeError:result['binary']=True
                    return {'accepted':True,'result':result}
                portability = getattr(self.app, 'portability', None)
                transfer_context = portability.write_context(session['id']) if portability else None
                request=[action,args,origin];identity=command_id or uuid.uuid4().hex
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                for reference in args.get('evidenceIds',[]):self.record(session['id'],reference)
                if args.get('parentId'):self.record(session['id'],args['parentId'])
                if args.get('messageId') and not any(row.get('id')==args['messageId'] for row in session.get('messages',[])):
                    raise ValueError('Choose an original message visible in this conversation; no turn was inferred.')
            data=None
            value={'kind':args.get('kind','writing' if action=='outputs.write' else 'file' if action=='outputs.attachImage' else 'git_review'),
                'title':args.get('title') or 'Saved Git review','origin':{'source':origin,'messageId':args.get('messageId')},
                'parentId':args.get('parentId'),'evidenceIds':args.get('evidenceIds',[])}
            workspace=session.get('workingDirectory') or session.get('workspace')
            if action=='outputs.attachImage':
                from .image_receipts import read_image_receipt
                data,path,name,receipt=await asyncio.to_thread(read_image_receipt,workspace,args['receiptPath'])
                if image_identity and any(receipt.get(key) != image_identity[key]
                        for key in ('requestId', 'requestHash', 'operation')):
                    raise ValueError('The completed image receipt changed before it could be displayed.')
                if receipt['operation']=='edit':
                    if image_identity and not args.get('parentId'):
                        existing=self.app.db.execute("SELECT id FROM output_records WHERE session_id=? AND json_extract(value,'$.kind')='file' AND json_extract(value,'$.sha256')=? ORDER BY created DESC LIMIT 1",
                            (session['id'],receipt['inputs'][0]['sha256'])).fetchone()
                        if existing:
                            args={**args,'parentId':existing[0]}
                        else:
                            original=receipt['inputs'][0]
                            parent_result=await self.dispatch('outputs.attach',{'sessionId':session['id'],
                                'kind':'file','title':'Original image','path':original['path'],
                                'expectedSha256':original['sha256']},origin,identity+':parent')
                            args={**args,'parentId':parent_result['result']['id']}
                        value['parentId']=args['parentId']
                    if not args.get('parentId'):
                        raise ValueError('Attach the original image first, then supply its parentId for this edit.')
                    parent=self.record(session['id'],args['parentId'])
                    if parent.get('kind')!='file' or parent.get('sha256')!=receipt['inputs'][0]['sha256']:
                        raise ValueError('The saved parent does not match the exact edit input hash.')
                elif args.get('parentId'):
                    raise ValueError('A generated image has no edit parent.')
                value.update(path=path,filename=name,versionEvidence='snapshot',imageGeneration=receipt)
            elif action=='outputs.attach':
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
                        value.update(canvasId=canvas['id'],canvasVersion=canvas.get('revision', 1),
                                     canvasKind=canvas['kind'],canvasSource=copy.deepcopy(canvas['body']),filename='canvas.json',versionEvidence='snapshot')
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
                current = self.app._session(session['id'])
                # File/Git reads above can outlive a task transfer. Recheck
                # authority under the same lock that commits its source fence.
                if portability and portability.write_context(session['id']) != transfer_context:
                    raise ValueError('The task moved while reading the output; inspect and retry.')
                if current.get('executionRevision', 0) != session.get('executionRevision', 0):
                    raise ValueError('The task execution folder changed while reading the output; inspect and retry.')
                if args.get('messageId') and not any(row.get('id') == args['messageId'] for row in current.get('messages', [])):
                    raise ValueError('The original message changed while saving the output. Refresh this conversation.')
                previous=self.store.receipt(identity,request)
                if previous:return {'accepted':True,'result':previous}
                if action=='outputs.attachImage':
                    # A model may also explicitly attach the same receipt after
                    # runtime delivery. Keep one result for that exact origin.
                    existing=self.app.db.execute("SELECT value FROM output_records WHERE session_id=? AND json_extract(value,'$.imageGeneration.receiptSha256')=? AND json_extract(value,'$.origin.messageId') IS ? AND json_extract(value,'$.linked')=1 LIMIT 1",
                        (session['id'],receipt['receiptSha256'],args.get('messageId'))).fetchone()
                    if existing:
                        result=json.loads(existing[0]);self.store.remember(identity,request,result)
                        return {'accepted':True,'result':result}
                if action in {'outputs.unlink','outputs.relink'}:
                    linked_record = self.record(session['id'],args['id'])
                    if linked_record.get('kind') == 'media':
                        self.media.authority(current, origin)
                    result=self.store.link(args['id'],args['expectedRevision'],action=='outputs.relink')
                    if action == 'outputs.unlink':
                        for canvas in self.app.state.get('canvasArtifacts', []):
                            if canvas.get('sessionId') == session['id']:
                                for publication in canvas.get('publications', []):
                                    if publication.get('outputId') == result['id']:
                                        publication['chat'] = 'none'
                                        publication.pop('imageGeneration', None)
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
                        if action == 'outputs.attachImage':
                            body = {'content': 'data:image/png;base64,' + base64.b64encode(data).decode()}
                            value.update(bodyFormat='canvas-image-v1', canvasId=uuid.uuid4().hex)
                        else:
                            try:body={'encoding':'utf-8','data':data.decode('utf-8')}
                            except UnicodeDecodeError:body={'encoding':'base64','data':base64.b64encode(data).decode()}
                        value.update(body=put(self.app.db,body),sha256=digest,bytes=len(data))
                    result=self.store.create(session['id'],value)
                    if action == 'outputs.attachImage':
                        from .canvas_library import image_output
                        image_output(self.app.state, current, result)
                self.store.remember(identity,request,result)
                if action in {'outputs.unlink','outputs.relink'} and result.get('kind') == 'media':
                    current['mediaRevision'] = current.get('mediaRevision', 0) + 1
                    self.app._publish_changes(sessions={current['id']}, globals={'canvasArtifacts'})
                elif action == 'outputs.attachImage':
                    self.app._publish_changes(globals={'canvasArtifacts'})
                elif action == 'outputs.unlink':
                    self.app._publish_changes(globals={'canvasArtifacts'})
                else:
                    self.app._save_changes()
                return {'accepted':True,'result':result}
        except (ValueError,KeyError,OSError) as exc:
            raise AppError(str(exc),409) from exc
