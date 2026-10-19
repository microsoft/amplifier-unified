"""Durable promises in existing host receipts; execution retains its own owners.

Current records are bounded. Source/correction receipts are immutable and paged.
No catalog/detail/context read hydrates history or initializes a worker.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import time
import uuid

from amplifier_operations.coordination import fingerprint

RECORD = 'workspace.work.record'
EVENT = 'workspace.work.event'
VERSION = 'workspace.work.version'
CONTEXT_BYTES = 12 * 1024
SOURCE_CHARS = 131072


def definitions(schema, string):
    scope = {'workspaceId': string(100)}
    actor = {'sessionId': string(200)}
    target = {**scope, 'id': string(200)}
    page = {'after': string(2048), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}
    source = schema({'sessionId': string(200), 'messageId': string(200)}, ['sessionId', 'messageId'])
    criteria = {'type': 'array', 'maxItems': 32, 'items': schema({
        'id': {**string(100), 'minLength': 1}, 'text': {**string(1000), 'minLength': 1}}, ['id', 'text'])}
    content = {'title': {**string(200), 'minLength': 1},
               'interpretation': string(4000), 'criteria': criteria,
               'constraints': {'type': 'array', 'maxItems': 32, 'items': {**string(1000), 'minLength': 1}},
               'nextAction': string(1000), 'blockedReason': string(1000)}
    return {
        'workspace.work.list': ('Read bounded durable promises without starting models or workers. Coverage is not a complete historical inventory.', schema({**scope, **page,
            'filter': {'enum': ['accepted', 'all', 'proposed', 'closed', 'attention']},
            'query': string(200)}, ['workspaceId'])),
        'workspace.work.read': ('Read one durable promise and its retained original source. No history hydration, execution or permission change.', schema({**target,
            'sourceOffset': {'type': 'integer', 'minimum': 0},
            'sourceLimit': {'type': 'integer', 'minimum': 1, 'maximum': 8000}}, ['workspaceId', 'id'])),
        'workspace.work.history': ('Read immutable change receipts for one promise. A saved report never grants execution authority.', schema({**target, **page}, ['workspaceId', 'id'])),
        'workspace.work.source': ('Read the exact retained original or correction source in bounded text pages, without loading its chat.', schema({**target,
            'sourceId': string(200), 'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8000}}, ['workspaceId', 'id', 'sourceId'])),
        'workspace.work.context': ('Read the calling root\'s bounded current Work summary and explicit overflow links. Missing detail is not permission.', schema({**actor, **page}, ['sessionId'])),
        'workspace.work.create': ('Capture a promise with an exact original source before commissioning work. Does not create a worker, goal or assignment. Proven human origin is required for accepted scope.', schema({**scope, **actor, **content,
            'source': source, 'intentKey': {**string(100), 'minLength': 1},
            'commitment': {'enum': ['accepted', 'proposed']}},
            ['workspaceId', 'sessionId', 'source', 'intentKey', 'title', 'commitment', 'criteria'])),
        'workspace.work.revise': ('Owner revises a promise at its exact record/promise revisions. Scope changes require a new retained source; old source and evidence remain.', schema({**target, **actor, **content,
            'source': source, 'expectedRevision': {'type': 'integer', 'minimum': 1},
            'expectedPromiseRevision': {'type': 'integer', 'minimum': 1}},
            ['workspaceId', 'sessionId', 'id', 'expectedRevision', 'expectedPromiseRevision'])),
    }


def _encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()


def _decode(value):
    try:
        return json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
    except (ValueError, UnicodeError, TypeError):
        return None


class WorkspaceWork:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.receipts = service.collaboration
        # Expression indexes are derived; canonical receipts survive index loss.
        self.db.execute("""CREATE INDEX IF NOT EXISTS work_current_scope ON commands(
            json_extract(receipt,'$.result.workspaceId'),
            json_extract(receipt,'$.result.disposition'),
            json_extract(receipt,'$.result.commitment'), id)
            WHERE json_extract(receipt,'$.commandAction')='workspace.work.record'""")
        self.db.execute("""CREATE INDEX IF NOT EXISTS work_source_intent ON commands(
            json_extract(receipt,'$.result.sourceKey'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.work.record'""")
        self.db.execute("""CREATE INDEX IF NOT EXISTS work_history_scope ON commands(
            json_extract(receipt,'$.workItemId'), json_extract(receipt,'$.sequence'))
            WHERE json_extract(receipt,'$.commandAction')='workspace.work.event'""")
        self.db.commit()

    def error(self, message, status=409):
        self.receipts.error(message, status)

    def workspace(self, args, origin, caller):
        from .owned_history import metadata
        sid = caller if origin not in {'ui', 'user'} else args.get('sessionId')
        owner = metadata(self.service, sid) if sid else None
        if args.get('workspaceId'):
            workspace = next((w for w in self.service.state['workspaces'] if w['id'] == args['workspaceId']), None)
        else:
            workspace = next((w for w in self.service.state['workspaces'] if owner and w['path'] == owner['workspace']), None)
        if not workspace:
            self.error('Work requires an available registered workspace.', 404)
        if origin not in {'ui', 'user'} and (not owner or owner['workspace'] != workspace['path']):
            self.error('Work is outside the calling workspace.', 403)
        return workspace

    def actor(self, args, origin, caller, workspace):
        actor = self.receipts.source(args, origin, caller, hydrate=False)
        if args['sessionId'] != actor['id'] or actor.get('workspace') != workspace['path']:
            self.error('Work mutations belong to the calling root and workspace.', 403)
        if origin not in {'ui', 'user'}:
            self.receipts.active_binding(actor)
            task = actor.get('task') or self.service.coordination.task(actor['id'])
            if actor.get('status') in {'stopping', 'stopped'} or task.get('status') in {'paused', 'blocked', 'completed'}:
                self.error('The task is stopped or inactive; Work cannot resume it.', 403)
            blocked = self.service.assignments.blocked(session=actor)
            if blocked:
                self.error(blocked, 403)
        return actor

    def load(self, identity, workspace):
        row = self.receipts.receipt(identity)
        if row.get('commandAction') != RECORD or row['result'].get('workspaceId') != workspace['id']:
            self.error('Work item is unavailable in this workspace.', 404)
        return copy.deepcopy(row['result'])

    def version(self, workspace):
        row = self.db.execute('SELECT receipt FROM commands WHERE id=?', ('work-version:' + workspace['id'],)).fetchone()
        return json.loads(row[0])['result']['revision'] if row else 0

    def advance(self, workspace):
        identity = 'work-version:' + workspace['id']
        version = self.version(workspace)
        receipt = {'commandAction': VERSION, 'result': {'revision': version + 1}}
        if version:
            self.receipts.save(identity, receipt)
        else:
            self.receipts.insert(identity, fingerprint([VERSION, workspace['id']]), receipt)

    def summary(self, item):
        value = {key: copy.deepcopy(item[key]) for key in (
            'id', 'workspaceId', 'revision', 'promiseRevision', 'title', 'commitment',
            'disposition', 'ownerSessionId', 'nextAction', 'blockedReason', 'updatedAt',
            'verification', 'humanDecision', 'delivery')}
        value['readRef'] = {'action': 'workspace.work.read', 'args': {'workspaceId': item['workspaceId'], 'id': item['id']}}
        return value

    def listing(self, workspace, args, *, owner=None, context=False):
        version = self.version(workspace)
        limit = min(args.get('limit', 20), 20 if context else 50)
        selected = args.get('filter', 'accepted')
        query = args.get('query', '').casefold()
        scope = [workspace['id'], owner, selected, query, 'context' if context else 'list']
        after_id = ''
        if args.get('after'):
            cursor = _decode(args['after'])
            if not isinstance(cursor, list) or len(cursor) != 3 or cursor[0] != scope or cursor[1] != version or not isinstance(cursor[2], str):
                self.error('Work changed or this page belongs to another scope; refresh the list.')
            after_id = cursor[2]
        where = ["json_extract(receipt,'$.commandAction')=?", "json_extract(receipt,'$.result.workspaceId')=?"]
        params = [RECORD, workspace['id']]
        if owner:
            where.append("json_extract(receipt,'$.result.ownerSessionId')=?")
            params.append(owner)
        if selected == 'accepted':
            where.extend(["json_extract(receipt,'$.result.commitment')='accepted'", "json_extract(receipt,'$.result.disposition')='open'"])
        elif selected == 'proposed':
            where.append("json_extract(receipt,'$.result.commitment')='proposed'")
        elif selected == 'closed':
            where.append("json_extract(receipt,'$.result.disposition')='closed'")
        elif selected == 'attention':
            where.append("json_extract(receipt,'$.result.disposition')='open'")
        if query:
            self.db.create_function('work_casefold', 1, lambda s: str(s or '').casefold(), deterministic=True)
            where.append("instr(work_casefold(json_extract(receipt,'$.result.title')),?)>0")
            params.append(query)
        condition = ' AND '.join(where)
        total = self.db.execute('SELECT count(*) FROM commands WHERE ' + condition, params).fetchone()[0]
        raw = self.db.execute('SELECT receipt FROM commands WHERE ' + condition + ' AND id>? ORDER BY id LIMIT ?',
                              [*params, after_id, limit + 1]).fetchall()
        items = [self.summary(json.loads(row[0])['result']) for row in raw[:limit]]
        if context:
            items = [{key: row[key] for key in ('id', 'revision', 'promiseRevision', 'title', 'nextAction', 'blockedReason', 'readRef')} for row in items]
            for row in items:
                row['title'] = row['title'][:100]
                row['nextAction'] = row['nextAction'][:200]
                row['blockedReason'] = row['blockedReason'][:200]
        def result():
            more = len(raw) > len(items)
            return {'workspaceId': workspace['id'], 'version': version, 'items': items, 'total': total,
                    'truncated': more, 'nextAfter': _encode([scope, version, items[-1]['id']]) if more and items else None,
                    'observedAt': time.time(), 'coverage': 'Recorded Work only; historical reconciliation is incomplete.',
                    'detailRequired': bool(context), 'authority': 'Reference data; current constraints and execution permissions remain binding.'}
        page = result()
        if context:
            while len(json.dumps(page, ensure_ascii=False).encode()) > CONTEXT_BYTES and items:
                items.pop()
                page = result()
        return page

    def history(self, workspace, item, args):
        limit = args.get('limit', 20)
        after = 0
        if args.get('after'):
            cursor = _decode(args['after'])
            if not isinstance(cursor, list) or len(cursor) != 3 or cursor[:2] != [workspace['id'], item['id']] or type(cursor[2]) is not int:
                self.error('History cursor belongs to another Work item.')
            after = cursor[2]
        rows = self.db.execute("""SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')=?
            AND json_extract(receipt,'$.workItemId')=? AND json_extract(receipt,'$.sequence')>?
            ORDER BY json_extract(receipt,'$.sequence') LIMIT ?""", (EVENT, item['id'], after, limit + 1)).fetchall()
        values = [json.loads(row[0]) for row in rows[:limit]]
        # Source body is deliberately separate from history summaries.
        values = [{**{k: row[k] for k in ('id', 'kind', 'sequence', 'promiseRevision', 'actor', 'recordedAt')},
                   'changedFields': list(row.get('changes', {})),
                   'source': {k: v for k, v in row.get('source', {}).items() if k != 'text'},
                   'sourceRef': {'action': 'workspace.work.source', 'args': {
                       'workspaceId': workspace['id'], 'id': item['id'], 'sourceId': row['id']}}
                       if row.get('source') else None} for row in values]
        return {'items': values, 'total': item['eventCount'], 'truncated': len(rows) > limit,
                'nextAfter': _encode([workspace['id'], item['id'], values[-1]['sequence']]) if len(rows) > limit else None}

    async def capture(self, reference, actor, workspace):
        from .owned_history import require
        require(self.service, actor['id'], reference['sessionId'], 'workspace')
        target = self.service._session(reference['sessionId'])
        await self.service.history.ensure_loaded(target['id'])
        require(self.service, actor['id'], target['id'], 'workspace')
        if target.get('workspace') != workspace['path']:
            self.error('Source changed workspace during capture.', 403)
        message = self.receipts.resolve_message(target, reference['messageId'])
        if not message or not isinstance(message.get('text'), str):
            self.error('The exact original source is unavailable.', 404)
        text = message['text']
        if not text.strip() or len(text) > SOURCE_CHARS:
            self.error('Source must be nonempty and at most 131072 characters; retain an explicit bounded source instead.', 400)
        provenance = message.get('inputOrigin', 'unknown')
        if provenance not in {'ui', 'user', 'peer', 'agent'}:
            provenance = 'unknown'
        if message.get('role') != 'user' and provenance in {'ui', 'user'}:
            provenance = 'unknown'
        return {'sessionId': target['id'], 'messageId': message['id'],
                'nativeMessageId': message.get('nativeMessageId'), 'nativeIndex': message.get('nativeIndex'),
                'inputOrigin': provenance, 'role': message.get('role'), 'text': text,
                'sha256': hashlib.sha256(text.encode()).hexdigest(), 'capturedAt': time.time()}

    def validate_content(self, args):
        for key in ('title',):
            if key in args and not args[key].strip():
                self.error('Provide a nonempty title.', 400)
        criteria = args.get('criteria')
        if criteria is not None and (len({c['id'] for c in criteria}) != len(criteria)
                or any(not c['id'].strip() or not c['text'].strip() for c in criteria)):
            self.error('Criteria need unique nonempty identifiers and text.', 400)

    async def dispatch(self, action, args, origin, command_id, caller):
        verb = action.removeprefix('workspace.work.')
        workspace = self.workspace(args, origin, caller)
        if verb == 'context':
            if origin not in {'ui', 'user'} and args['sessionId'] != caller:
                self.error('Work context belongs to the calling root.', 403)
            return {'accepted': True, 'result': self.listing(workspace, args, owner=args['sessionId'], context=True)}
        if verb == 'list':
            return {'accepted': True, 'result': self.listing(workspace, args)}
        if verb in {'read', 'history', 'source'}:
            item = self.load(args['id'], workspace)
            if verb == 'history':
                value = self.history(workspace, item, args)
            else:
                event = self.receipts.receipt(args['sourceId'] if verb == 'source' else item['originalSourceRef'])
                if event.get('commandAction') != EVENT or event.get('workItemId') != item['id'] or not event.get('source'):
                    self.error('Source does not belong to this Work item.', 404)
                source = copy.deepcopy(event['source'])
                offset = args.get('offset' if verb == 'source' else 'sourceOffset', 0)
                limit = args.get('limit' if verb == 'source' else 'sourceLimit', 8000)
                total = len(source['text'])
                source['text'] = source['text'][offset:offset + limit]
                source.update(offset=offset, total=total, truncated=offset + limit < total,
                              nextOffset=offset + limit if offset + limit < total else None)
                value = ({'source': source} if verb == 'source' else
                         {'item': item, 'originalSource': source, 'historyRef': {'action': 'workspace.work.history', 'args': {'workspaceId': workspace['id'], 'id': item['id']}}})
            return {'accepted': True, 'result': value}
        actor = self.actor(args, origin, caller, workspace)
        if not command_id:
            self.error('Work mutations require a stable command ID.', 400)
        self.validate_content(args)
        digest = fingerprint([action, args, origin, actor['id']])
        # Avoid rehydrating a source for a confirmed idempotent retry.
        async with self.service.lock:
            duplicate = self.receipts.duplicate(command_id, digest)
            if duplicate:
                return duplicate
        captured = await self.capture(args['source'], actor, workspace) if 'source' in args else None
        async with self.service.lock:
            workspace = self.workspace(args, origin, caller)
            actor = self.actor(args, origin, caller, workspace)
            duplicate = self.receipts.duplicate(command_id, digest)
            if duplicate:
                return duplicate
            if self.db.in_transaction:
                self.db.commit()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                now = time.time()
                if verb == 'create':
                    if args['commitment'] == 'accepted' and captured['inputOrigin'] not in {'ui', 'user'}:
                        self.error('Accepted Work requires proven human source origin; retain other sources as proposals.', 400)
                    source_key = fingerprint([workspace['id'], captured['sessionId'], captured['messageId'], captured['sha256'], args['intentKey']])
                    prior = self.db.execute("SELECT receipt FROM commands WHERE json_extract(receipt,'$.commandAction')=? AND json_extract(receipt,'$.result.sourceKey')=?", (RECORD, source_key)).fetchone()
                    if prior:
                        item = json.loads(prior[0])['result']
                        receipt = {'accepted': True, 'commandAction': action, 'result': {'item': self.summary(item)}, 'sourceDuplicate': True}
                        self.receipts.insert(command_id, digest, receipt)
                        self.db.commit()
                        return receipt
                    identity = 'work:' + str(uuid.uuid5(uuid.NAMESPACE_URL, workspace['id'] + ':' + command_id))
                    item = {'id': identity, 'workspaceId': workspace['id'], 'originSessionId': captured['sessionId'],
                            'ownerSessionId': actor['id'], 'schemaVersion': 1, 'sourceKey': source_key,
                            'revision': 1, 'promiseRevision': 1, 'commitment': args['commitment'],
                            'disposition': 'open', 'verification': 'not_verified', 'humanDecision': 'not_recorded',
                            'delivery': 'not_recorded', 'eventCount': 1, 'createdAt': now, 'updatedAt': now,
                            'interpretation': '', 'criteria': [], 'constraints': [], 'nextAction': '', 'blockedReason': ''}
                elif verb == 'revise':
                    item = self.load(args['id'], workspace)
                    if item['ownerSessionId'] != actor['id']:
                        self.error('Only the accountable Work owner can revise it.', 403)
                    if item['revision'] != args['expectedRevision'] or item['promiseRevision'] != args['expectedPromiseRevision']:
                        self.error('Work revision changed; inspect current state before changing it.')
                    if item['disposition'] != 'open':
                        self.error('Closed Work needs an explicit corrective reopening.')
                    promise_change = any(key in args and args[key] != item.get(key) for key in ('interpretation', 'criteria', 'constraints'))
                    if promise_change and not captured:
                        self.error('A promise correction needs its exact source.')
                    if promise_change and item['commitment'] == 'accepted' and captured['inputOrigin'] not in {'ui', 'user'}:
                        self.error('Changing accepted scope needs proven human source origin.', 400)
                    item.update(revision=item['revision'] + 1, eventCount=item['eventCount'] + 1, updatedAt=now)
                    if promise_change:
                        item['promiseRevision'] += 1
                        item['verification'] = 'not_verified'
                else:
                    self.error('Unsupported Work mutation.', 400)
                for key in ('title', 'interpretation', 'criteria', 'constraints', 'nextAction', 'blockedReason'):
                    if key in args:
                        item[key] = copy.deepcopy(args[key])
                event_id = 'work-event:' + str(uuid.uuid5(uuid.NAMESPACE_URL, command_id))
                event = {'id': event_id, 'commandAction': EVENT, 'kind': verb, 'workItemId': item['id'],
                         'sequence': item['eventCount'], 'promiseRevision': item['promiseRevision'],
                         'actor': {'origin': origin, 'sessionId': actor['id']}, 'recordedAt': now,
                         'source': captured or {}, 'changes': {k: copy.deepcopy(args[k]) for k in ('title', 'interpretation', 'criteria', 'constraints', 'nextAction', 'blockedReason') if k in args}}
                self.receipts.insert(event_id, fingerprint(event), event)
                item['latestEventRef'] = event_id
                if verb == 'create':
                    item['originalSourceRef'] = event_id
                    self.receipts.insert(item['id'], fingerprint([RECORD, item['id']]), {'commandAction': RECORD, 'result': item})
                else:
                    self.receipts.save(item['id'], {'commandAction': RECORD, 'result': item})
                self.advance(workspace)
                receipt = {'accepted': True, 'commandAction': action, 'result': {'item': self.summary(item)}}
                self.receipts.insert(command_id, digest, receipt)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            self.service.coordination.notify()
            return receipt
