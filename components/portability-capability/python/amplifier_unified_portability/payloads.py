"""Optional signed historical resource payload composition; no execution replay."""
import asyncio
import base64
import hashlib
from pathlib import Path

from amplifier_portability.capsule import read_capsule
from amplifier_portability.evidence import canonical_bytes
from amplifier_portability.payloads import PayloadStore, CHUNK_BYTES, MAX_TOTAL_BYTES
from amplifier_portability.protocol import encoded
from amplifier_worktrees.git import digest

CAPABILITIES = {'version': 1, 'owner': 'unified.resources', 'maxRecords': 500,
                'maxIdCodeUnits': 200, 'chunkBytes': CHUNK_BYTES,
                'maxBodyBytes': 64 * 1024 * 1024, 'maxTotalBytes': MAX_TOTAL_BYTES}


def selected_rows(store, plan):
    body = store.verify_plan(plan, accept_partial=True)
    if body['owner'] != 'unified.resources' or body['items'] > 500:
        raise ValueError('Resource payload owner or negotiated count incompatible')
    rows, cursor, total, omitted, seen = [], None, 0, 0, set()
    while True:
        page = store.page(plan, cursor=cursor, accept_partial=True)
        for row in page['items']:
            if len(row['id'].encode('utf-16-le')) // 2 > 200 or row['id'] in seen:
                raise ValueError('Resource identity exceeds negotiated limit or is duplicated')
            seen.add(row['id']); rows.append(row)
            total += row.get('bytes', 0); omitted += row['disposition'] == 'omitted'
            if total > body['bytes'] or total > MAX_TOTAL_BYTES:
                raise ValueError('Resource payload declared byte budget exceeded')
        cursor = page['nextCursor']
        if cursor is None:
            break
    if total != body['bytes'] or omitted != body['omitted'] or len(rows) != body['items']:
        raise ValueError('Resource payload metadata aggregate mismatch')
    return body, rows


class ResourcePayloads:
    def __init__(self, owner):
        self.owner = owner

    def store(self, identity):
        self.owner.node.path(identity)
        return PayloadStore(self.owner.node.directory / 'payloads' / identity)

    async def capabilities(self):
        try:value = await self.owner.host('payloadCapabilities', {})
        except ValueError:return None
        return CAPABILITIES if value == CAPABILITIES else None

    async def negotiate(self, destination):
        if await self.capabilities() != CAPABILITIES or self.owner.node.peers().get(destination, {}).get('payloadCapabilities') != CAPABILITIES:
            raise ValueError('Detached resource payload support must be explicitly negotiated on both paired hosts')

    async def export(self, scope, identity, evidence):
        item = next((v for v in evidence if v['owner'] == 'unified.resources'), None)
        if item is None:
            raise ValueError('Resource payload export requires exact owner evidence')
        metadata, cursor, revision, coverage, omissions = [], None, None, 'complete', []
        while True:
            page = await self.owner.host('readTransferAttachmentMetadata', {'session': scope, **({'expectedRevision':revision} if revision is not None else {}), **({'cursor':cursor} if cursor is not None else {}), 'limit':25,'maxBytes':CHUNK_BYTES})
            if page.get('owner') != 'unified.resources' or type(page.get('version')) is not int or page['version'] != 1 or page.get('session') != scope or not isinstance(page.get('items'), list):
                raise ValueError('Exact resource metadata source page required')
            if revision is None:
                revision = page['revision']
            if page['revision'] != revision or page.get('coverage') not in {'complete','partial'} or not isinstance(page.get('omissions'),list) or len(page['items'])>25:
                raise ValueError('Resource metadata revision/coverage changed during selection')
            for omission in page['omissions']:
                if omission not in omissions:omissions.append(omission)
            metadata.extend(page['items'])
            if len(metadata) > 500:
                raise ValueError('Resource selection exceeds negotiated count; no truncation')
            cursor = page['nextCursor']
            if cursor is None:break
        coverage='partial' if omissions else 'complete'
        selection = {'version': 1, 'session': scope, 'revision': revision, 'items': metadata, 'coverage': coverage, 'omissions': omissions}
        self.validate_selection(selection)
        selection['metadataSha256'] = hashlib.sha256(canonical_bytes(selection)).hexdigest()
        directory = self.owner.exchange / (identity + '.payloads')
        store = PayloadStore(directory)
        captures = self.owner.node.directory / 'payload-captures' / identity
        captures.mkdir(parents=True, exist_ok=False)
        entries, total = [], 0
        # Refuse incompatible metadata before any body copying. Never shorten ids.
        for meta in metadata:
            resource = meta.get('id')
            if not isinstance(resource, str) or not resource or len(resource.encode('utf-16-le')) // 2 > 200:
                raise ValueError('Resource identity exceeds negotiated limit')
            if meta.get('status') == 'committed':
                size = meta.get('size')
                if type(size) is not int or not 0 <= size <= CAPABILITIES['maxBodyBytes']:
                    raise ValueError('Resource body exceeds negotiated limit')
                total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Resource selection exceeds negotiated byte limit')
        for meta in metadata:
            resource = meta['id']
            if meta.get('status') != 'committed':
                entries.append({'id': resource, 'reason': 'Unfinished attachment retained on source; no body copied'})
                continue
            path = captures / hashlib.sha256(resource.encode()).hexdigest()
            whole, offset = hashlib.sha256(), 0
            with path.open('xb') as stream:
                while offset < meta['size']:
                    size = min(CHUNK_BYTES, meta['size'] - offset)
                    value = await self.owner.host('readTransferPayloadSource', {'session': scope, 'transferId': identity, 'resourceId': resource, 'resourceUri': meta['resourceUri'], 'size': meta['size'], 'sha256': meta['sha256'], 'offset': offset, 'maxBytes': size})
                    if value.get('encoding') != 'base64':
                        raise ValueError('Exact resource byte encoding required')
                    raw = base64.b64decode(value['data'], validate=True)
                    if len(raw) != size or base64.b64encode(raw).decode() != value['data']:
                        raise ValueError('Resource payload page size/encoding changed')
                    whole.update(raw); stream.write(raw); offset += len(raw)
            if whole.hexdigest() != meta['sha256']:
                raise ValueError('Resource payload digest changed; source retained')
            entries.append({'id': resource, 'path': path})
        plan = await asyncio.to_thread(store.build, 'unified.resources', item['revision'], entries)
        await asyncio.to_thread(store.audit, plan, accept_partial=True)
        for entry in entries:
            if 'path' in entry:
                entry['path'].unlink()
        captures.rmdir()
        checked=await self.owner.host('readTransferAttachmentMetadata', {'session': scope, 'expectedRevision': revision, 'limit': 1, 'maxBytes': CHUNK_BYTES})
        if checked.get('revision')!=revision or checked.get('session')!=scope:raise ValueError('Resource metadata revision changed during body capture')
        return {'version': 1, 'plan': plan, 'sourceSelection': selection, 'evidenceHash': item['sha256'], 'ownerRevision': item['revision']}, str(directory)

    @staticmethod
    def validate_selection(selection):
        from amplifier_portability.capsule import validate_public
        from urllib.parse import urlparse, parse_qs
        validate_public(selection)
        if type(selection.get('version')) is not int or selection['version'] != 1 or not isinstance(selection.get('session'), str) or not isinstance(selection.get('revision'), (str, int)) or isinstance(selection.get('revision'), bool) or selection.get('coverage') not in {'complete', 'partial'} or not isinstance(selection.get('omissions'), list) or not isinstance(selection.get('items'), list) or len(selection['items']) > 500:
            raise ValueError('Invalid bounded source selection')
        if (selection['coverage'] == 'complete') != (not selection['omissions']):
            raise ValueError('Source selection coverage must preserve explicit omissions')
        total, seen = 0, set()
        for meta in selection['items']:
            if not isinstance(meta, dict) or set(meta) != {'id','name','contentType','size','sha256','etag','resourceUri','status'} or meta['status'] != 'committed' or not isinstance(meta['id'], str) or not meta['id'] or len(meta['id'].encode('utf-16-le')) // 2 > 200 or meta['id'] in seen:
                raise ValueError('Exact unique committed source metadata required')
            seen.add(meta['id'])
            if type(meta['size']) is not int or not 0 <= meta['size'] <= CAPABILITIES['maxBodyBytes'] or not isinstance(meta['sha256'], str) or len(meta['sha256']) != 64 or any(c not in '0123456789abcdef' for c in meta['sha256']) or meta['etag'] != '"sha256:' + meta['sha256'] + '"':
                raise ValueError('Source metadata size/digest mismatch')
            uri = urlparse(meta['resourceUri'])
            if uri.scheme != 'amplifier-attachment' or uri.netloc != meta['id'] or uri.path != '/body' or parse_qs(uri.query) != {'session': [selection['session']]}:
                raise ValueError('Source resource URI scope/identity mismatch')
            total += meta['size']
        if total > MAX_TOTAL_BYTES:raise ValueError('Source selection exceeds negotiated byte limit')

    @staticmethod
    def match_selection(selection, rows):
        descriptors = {meta['id']: meta for meta in selection['items']}
        if len(descriptors) != len(rows):raise ValueError('Payload manifest differs from signed source selection')
        for row in rows:
            meta = descriptors.get(row['id'])
            if meta is None or row['disposition'] != 'included' or row['bytes'] != meta['size'] or row['sha256'] != meta['sha256']:
                raise ValueError('Payload body mapping differs from signed source selection')

    def binding(self, package):
        owner = self.owner
        body = owner.node.verify(package, kind='capsule')
        if body['source'] != package['signer'] or body['destination'] != owner.node.identity['id']:
            raise ValueError('Payload capsule source/destination authentication mismatch')
        value = body['payload'].get('resourcePayload')
        if not isinstance(value, dict) or set(value) != {'version', 'plan', 'sourceSelection', 'evidenceHash', 'ownerRevision'} or type(value['version']) is not int or value['version'] != 1:
            raise ValueError('Exact signed resource payload binding required')
        evidence = next((v for v in owner.evidence(body['payload']['evidence'], True) if v['owner'] == 'unified.resources'), None)
        if evidence is None or value['evidenceHash'] != evidence['sha256'] or value['ownerRevision'] != evidence['revision']:
            raise ValueError('Signed resource evidence hash/revision mismatch')
        selection=value['sourceSelection']
        if not isinstance(selection,dict) or set(selection)!={'version','session','revision','items','coverage','omissions','metadataSha256'}:raise ValueError('Exact signed source selection required')
        clean={k:v for k,v in selection.items() if k!='metadataSha256'};self.validate_selection(clean)
        if selection['session']!=body['payload']['originSession'] or selection['metadataSha256']!=hashlib.sha256(canonical_bytes(clean)).hexdigest():raise ValueError('Signed resource source-selection scope/digest mismatch')
        plan = value['plan']; metadata = PayloadStore.verify_plan(plan, accept_partial=True)
        if metadata['owner'] != 'unified.resources' or metadata['revision'] != value['ownerRevision']:
            raise ValueError('Signed payload plan owner/revision mismatch')
        return body, value, {'owner': 'unified.resources', 'transferId': body['id'],
                'sourceHost': body['source'], 'destinationHost': body['destination'],
                'ownerRevision': value['ownerRevision'], 'evidenceHash': value['evidenceHash'], 'planHash': plan['sha256']}

    async def stage(self, package, directory):
        if await self.capabilities() != CAPABILITIES:
            raise ValueError('Detached payload receiver is not configured')
        body, value, binding = self.binding(package)
        incoming = PayloadStore(self.owner.local(directory, exchange=True))
        store = self.store(body['id'])
        plan = value['plan']
        for page in PayloadStore.verify_plan(plan, accept_partial=True)['pages']:
            store.put(incoming.read(page['sha256'], page['bytes']), page['sha256'])
        _, rows = selected_rows(store, plan)
        self.match_selection(value['sourceSelection'], rows)
        for row in rows:
            for chunk in row.get('chunks', []):
                store.put(incoming.read(chunk['sha256'], chunk['bytes']), chunk['sha256'])
        await asyncio.to_thread(store.audit, plan, accept_partial=True)
        sources, materialized = [], set()
        for row in rows:
            if row['disposition'] != 'included':
                continue
            path = self.owner.node.directory / 'payloads' / body['id'] / (row['sha256'] + '.body')
            if row['sha256'] not in materialized:
                with path.open('xb') as stream:
                    for chunk in row['chunks']:
                        stream.write(store.read(chunk['sha256'], chunk['bytes']))
                materialized.add(row['sha256'])
            sources.append({'id': row['id'], 'path': str(path)})
        return await self.owner.host('stageTransferPayloads', {**binding, 'capsule': package,
                'plan': plan, 'bodies': sources})

    async def verify(self, args):
        body, value, binding = self.binding(args['capsule'])
        row = self.owner.node.get(body['id'])
        if row['direction'] != 'incoming' or row['capsuleHash'] != digest(encoded(body)):
            raise ValueError('Payload verifier requires the exact stored incoming capsule')
        self.owner.node.match(row, {**body,'capsuleHash':digest(encoded(body))})
        stored = read_capsule(self.owner.node.directory / 'packages' / (body['id'] + '.json'))
        if encoded(stored) != encoded(args['capsule']):
            raise ValueError('Payload verifier capsule differs from staged evidence')
        if args.get('plan') != value['plan'] or any(args.get(key) != val for key, val in binding.items() if key != 'owner'):
            raise ValueError('Payload verifier exact request binding mismatch')
        store = self.store(body['id'])
        _, rows = selected_rows(store, value['plan'])
        self.match_selection(value['sourceSelection'], rows)
        await asyncio.to_thread(store.audit, value['plan'], accept_partial=True)
        return {**binding, 'bodies': [{'id': r['id'], 'size': r['bytes'], 'sha256': r['sha256']}
                for r in rows if r['disposition'] == 'included']}
