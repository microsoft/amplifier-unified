"""Read-only, bounded observations over conversation-owned surfaces.

State is authoritative in the canvas store. Browser evidence is untrusted,
view-bound and expires on restart. Nothing here starts a model turn.
"""
import asyncio
import base64
import copy
import hashlib
import json
import struct
import time
import uuid

from .canvas_apps import fail


def compact(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def revision(row):
    return f"{row['app']['revision']}:{row['app']['stateRevision']}"


def summary(state, budget=900):
    """Counts are exact; large values have field references, never guessed prose."""
    result = {}
    for key, value in state.items():
        if len(key) > 100:
            continue
        if isinstance(value, list):
            item = {'count': len(value), 'readField': key}
        elif isinstance(value, dict):
            item = {'fields': len(value), 'readField': key}
        elif isinstance(value, str) and len(value) > 100:
            item = {'characters': len(value), 'readField': key}
        else:
            item = value
        if len(compact({**result, key: item})) > budget:
            break
        result[key] = item
    return result


class SurfaceContext:
    def __init__(self, service):
        self.service = service
        self.observations = {}  # latest per view; pixels never enter app snapshots
        self.checkpoints = {}

    async def checkpoint(self, sid):
        binding = self.bind_input(sid)
        client_id = binding.get('clientId')
        if not binding['targets'] or not client_id:
            return binding
        identity = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        targets = {(t['viewId'], t['surfaceId']) for t in binding['targets']}
        self.checkpoints[identity] = (client_id, future, targets)
        async with self.service.lock:
            client = self.service.clients.records[client_id]
            client.setdefault('deviceCommands', []).append({'id': identity, 'type': 'canvas.checkpoint', 'sessionId': sid,
                                                           'createdAt': time.time(), 'clientId': client_id, 'origin': 'host'})
            self.service._publish()
        try:
            await asyncio.wait_for(future, 1.2)
        except TimeoutError:
            pass
        finally:
            self.checkpoints.pop(identity, None)
        return binding

    def bind_input(self, session_id):
        service = self.service
        client_id = service.clients.current.get()
        client = service.clients.records.get(client_id, {})
        targets = []
        if client.get('selectedSessionId') == session_id and client.get('canvas', {}).get('open'):
            for view in ('primary', 'secondary'):
                try:
                    row, pref = service.canvas_views.resolve(view)
                except Exception:
                    continue
                if row.get('sessionId') == session_id and row.get('app'):
                    targets.append({'surfaceId': row['id'], 'viewId': view,
                                    'generation': pref['generation'], 'resourceRevision': service.canvas_views.revision(row)})
        return {'clientId': client_id, 'targets': targets}

    def observe(self, args):
        service = self.service
        row, pref = service.canvas_views.target(args)
        if not row.get('app'):
            fail('This view is not an interactive surface.')
        if args['revision'] != revision(row):
            fail('This observation describes an older surface state.', 409)
        image = args.get('image')
        if image:
            try:
                data = base64.b64decode(image, validate=True)
                if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 350_000:
                    raise ValueError()
                width, height = struct.unpack('>II', data[16:24])
                if not 1 <= width <= 1024 or not 1 <= height <= 1024:
                    raise ValueError()
            except (ValueError, TypeError, struct.error):
                fail('Use a PNG drawing observation up to 1024 pixels and 350 KB.')
        key = (service.clients.current.get(), args['viewId'], row['id'])
        old = self.observations.get(key, {})
        if args['editVersion'] < old.get('editVersion', 0) and args['generation'] == old.get('generation'):
            fail('A newer local edit has already been observed.', 409)
        digest = hashlib.sha256(image.encode()).hexdigest() if image else None
        image_ref = old.get('image') if digest and digest == old.get('digest') else image
        value = {k: args[k] for k in ('revision', 'generation', 'editVersion', 'pending', 'status')}
        value.update(image=image_ref, digest=digest, capturedAt=time.time(), instance=service.instance_id,
                     text=args.get('text', '')[:1800], controls=copy.deepcopy(args.get('controls', [])[:16]),
                     reason=args.get('reason', '')[:200])
        self.observations[key] = value
        checkpoint = self.checkpoints.get(args.get('checkpointId'))
        if checkpoint and checkpoint[0] == key[0] and not checkpoint[1].done():
            checkpoint[2].discard((key[1], key[2]))
            if not checkpoint[2]:
                checkpoint[1].set_result(True)
        # Evidence is a cache, never an unbounded image history.
        while len(self.observations) > 64:
            self.observations.pop(next(iter(self.observations)))
        return {'status': 'observed', 'revision': value['revision'], 'imageAvailable': bool(image)}

    def _rows(self, sid):
        return {r['id']: r for r in self.service._state.get('canvasArtifacts', []) if r.get('app') and r.get('sessionId') == sid}

    def manifest(self, sid, bindings):
        rows = self._rows(sid)
        targets = []
        for binding in bindings[:8]:
            client_id = binding.get('clientId')
            for target in binding.get('targets', [])[:2]:
                if target.get('surfaceId') in rows:
                    targets.append({**target, 'clientId': client_id})
        # Detached inputs still get conversation-owned state, without inventing a view.
        if not bindings or all(b.get('clientId') is None for b in bindings):
            targets = [{'surfaceId': key} for key in list(rows)[-2:]]
        result, seen = [], set()
        for target in targets:
            key = (target.get('clientId'), target.get('viewId'), target['surfaceId'])
            if key in seen:
                continue
            seen.add(key)
            row = rows[target['surfaceId']]
            client = self.service.clients.records.get(key[0], {})
            pref = client.get('canvasViews', {}).get('preferences', {}).get(f'{key[1]}:{key[2]}', {})
            active_id = client.get('canvas', {}).get('id') if key[1] == 'primary' else client.get('canvasViews', {}).get('secondary')
            visible = bool(client.get('canvas', {}).get('open') and active_id == key[2] and pref.get('generation') == target.get('generation') and self.service.canvas_views.revision(row) == target.get('resourceRevision'))
            observation = self.observations.get(key, {}) if visible else {}
            valid = observation.get('revision') == revision(row) and observation.get('generation') == pref.get('generation') and observation.get('instance') == self.service.instance_id and time.time() - observation.get('capturedAt', 0) < 120
            valid = valid and (not pref.get('dirty') or observation.get('pending') and observation.get('editVersion') == pref.get('editVersion'))
            pending = bool(pref.get('dirty') or valid and observation.get('pending'))
            item = {**target, 'title': row['title'][:80], 'revision': revision(row), 'facts': summary(row['app']['state']),
                    'stateFields': [k for k in row['app']['state'] if len(k) <= 80][:16], 'stateFieldCount': len(row['app']['state']),
                    'pendingLocalEdits': pending, 'view': 'visible' if visible else 'unavailable',
                    'viewObservation': 'current' if valid else 'stale-or-unavailable',
                    'renderStatus': pref.get('activation', {}).get('status', 'unknown'),
                    'image': {'available': bool(valid and observation.get('image')), 'evidence': 'local-pending' if pending else 'browser-canvas',
                              **({'digest': observation['digest']} if valid and observation.get('image') else {})},
                    'read': {'operation': 'context.read', 'surfaceId': row['id'], 'revision': revision(row)}}
            if valid:
                item['editVersion'] = observation['editVersion']
            result.append(item)
            if len(result) >= 3:
                break
        return {'surfaces': result, 'scope': 'originating-input', 'ambiguousViews': len({(b.get('clientId'), compact(b.get('targets', []))) for b in bindings}) > 1}

    def read(self, sid, args, bindings):
        row = self._rows(sid).get(args.get('surfaceId'))
        if not row:
            fail('This surface is unavailable in the calling conversation.', 404)
        current = revision(row)
        if args.get('revision', current) != current:
            fail(f'That observation revision is unavailable. Current revision is {current}; read it explicitly.', 409)
        representation = args.get('representation', 'summary')
        if representation not in {'summary', 'state', 'image', 'view'}:
            fail('Choose summary, state, image or view.')
        result = {'surfaceId': row['id'], 'revision': current, 'representation': representation, 'evidence': 'saved-state'}
        if representation == 'summary':
            result['data'] = summary(row['app']['state'], 2500)
        elif representation == 'state':
            fields = args.get('fields', list(row['app']['state']))
            if not isinstance(fields, list) or len(fields) > 32 or any(not isinstance(k, str) or k not in row['app']['state'] for k in fields):
                fail('Choose up to 32 existing top-level state fields.')
            value = {k: copy.deepcopy(row['app']['state'][k]) for k in fields}
            if len(compact(value)) > 24000:
                fail('This read exceeds 24 KB. Choose fewer fields or use the paged state reader.')
            result.update(data=value, fields=fields)
        else:
            manifest = self.manifest(sid, bindings)
            candidates = [v for v in manifest['surfaces'] if v['surfaceId'] == row['id'] and v.get('clientId') and
                          (not args.get('viewId') or v.get('viewId') == args['viewId'])]
            if len(candidates) != 1:
                fail('No single originating view is available. Specify a viewId when input bindings are ambiguous.', 409)
            view = candidates[0]
            observation = self.observations.get((view['clientId'], view['viewId'], row['id']), {})
            if view['view'] != 'visible' or view['viewObservation'] != 'current':
                fail('The current browser observation is unavailable. Saved state remains readable.', 409)
            result.update(clientId=view['clientId'], viewId=view['viewId'], generation=view['generation'],
                          editVersion=observation['editVersion'], capturedAt=observation['capturedAt'],
                          pendingLocalEdits=view['pendingLocalEdits'], evidence=view['image']['evidence'])
            if representation == 'image':
                if not view['image']['available']:
                    fail('This view has no current readable drawing image. Read state or view for its limits.', 409)
                result.update(digest=observation['digest'], _image=observation['image'])
            else:
                result['data'] = {'text': observation['text'], 'controls': observation['controls'], 'status': observation['status'], 'reason': observation['reason']}
        return result
