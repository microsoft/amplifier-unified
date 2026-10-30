"""Passive bounded current-assignment view; canonical records stay authoritative."""
import base64
import json
import time

from amplifier_operations.coordination import fingerprint

MAX_BYTES = 9 * 1024


def preview(value, limit=160):
    if not isinstance(value, str):
        return None
    return {'text': value[:limit], 'truncated': len(value) > limit}


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


# Explicit metadata only: task payloads below are separately projected from
# their retained owner; messages, configuration and execution stay cold.
FIELDS = ('id', 'workspace', 'assignmentId', 'collaboration', 'runtimeSessionId',
          'nativeIdentity', 'sessionKind', 'title', 'status')


def dispatch(owner, args, origin, caller):
    service = owner.service

    def project():
        from .collaboration import PRINCIPAL
        sid = caller if origin not in {'ui', 'user'} else args['sessionId']
        if not sid:
            service.collaboration.error('A user must explicitly authorize a calling conversation.')
        source = service._session_read_sources([sid], FIELDS)[0]
        actor = PRINCIPAL.get()
        native = source.get('runtimeSessionId') or source.get('nativeIdentity') or source['id']
        if origin not in {'ui', 'user'} and actor and actor != native:
            from .coordination_guidance import ROOT_SCOPE_MESSAGE
            service.collaboration.error(ROOT_SCOPE_MESSAGE)
        if source.get('sessionKind', 'root') != 'root':
            service.collaboration.error('Collaboration belongs to ordinary root conversations.')
        if source['id'] != args['sessionId']:
            service.collaboration.error('Assignment actions belong to the calling conversation.')
        rows = {source['id']: source}

        def lookup(identity):
            if identity not in rows:
                rows[identity] = service._session_read_sources([identity], FIELDS)[0]
            return rows[identity]

        return {'accepted': True, 'result': read(owner, source, args, lookup)}

    return service.directory.read_frame(project)


def task_preview(service, row):
    resident = next((item for item in service.state['sessions'] if item['id'] == row['id']), {})
    control = service.state.get('runtimeControl', {}).get(row['id'], {})
    task = dict.get(resident, 'task') or dict.get(dict.get(control, 'task.get', {}), 'task')
    if isinstance(task, dict):
        return {'id': task.get('id'), 'revision': task.get('revision'), 'status': task.get('status'),
                'objective': preview(task.get('objective')),
                'blockedReason': preview(task.get('blockedReason')) if task.get('status') == 'blocked' else None}
    # Directory-only members have no live task cache. Select bounded scalar
    # fields from the existing retained projection, never load an owner record
    # or native control/history body merely to orient a caller.
    fields = ('id', 'revision', 'status', 'objective', 'blockedReason')
    for kind, prefix in (('runtime', '$."task.get".task'), ('session', '$.task')):
        expressions = []
        for field in fields:
            path = prefix + '.' + field
            expressions.append(f"substr(json_extract(value, '{path}'), 1, 161)" if field in {'objective', 'blockedReason'}
                               else f"json_extract(value, '{path}')")
        saved = service.db.execute('SELECT ' + ','.join(expressions) +
            " FROM state_records WHERE kind=? AND id=? AND json_type(value, ?)='object'",
            (kind, row['id'], prefix)).fetchone()
        if saved:
            task = dict(zip(fields, saved))
            return {'id': task['id'], 'revision': task['revision'], 'status': task['status'],
                    'objective': preview(task['objective']),
                    'blockedReason': preview(task['blockedReason']) if task['status'] == 'blocked' else None}
    return {'id': None, 'revision': None, 'status': None, 'objective': None, 'blockedReason': None}


def read(owner, source, args, lookup):
    service = owner.service
    value = owner.current(source)
    if not value:
        return {'assignmentId': None, 'members': [], 'nextAfter': None, 'totalMembers': 0}
    if owner.tasks.member(value, source['id'], lookup=lookup) is None:
        service.collaboration.error('Read the brief only as a current assignment member.')
    identities = sorted(value['members'])
    # Membership changes invalidate paging, while ordinary progress can keep
    # moving. Each page reports current observations, not a frozen global view.
    scope = fingerprint([source['id'], source['workspace'], value['id'],
        [(sid, value['members'][sid]['status']) for sid in identities]])
    offset, limit = 0, args.get('limit', 8)
    if type(limit) is not int or not 1 <= limit <= 16:
        service.collaboration.error('Brief page size must be between 1 and 16.', 400)
    if args.get('after'):
        try:
            token = args['after']
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError()
            cursor = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
            if (not isinstance(cursor, list) or len(cursor) != 2 or cursor[0] != scope
                    or type(cursor[1]) is not int or not 0 <= cursor[1] < len(identities)):
                raise ValueError()
            offset = cursor[1]
        except (ValueError, TypeError, UnicodeError):
            service.collaboration.error('Assignment membership changed or the cursor belongs to another brief. Read a fresh first page.', 409)
    result = {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
        'ownerSessionId': value['ownerSessionId'], 'status': value['status'],
        'canManage': source['id'] == value['ownerSessionId'], 'observedAt': time.time(),
        'totalMembers': len(identities), 'activeMemberCount': owner.active_member_count(value),
        'members': [], 'nextAfter': None,
        'taskListRef': {'action': 'assignment.tasks.list', 'args': {'sessionId': source['id']}},
        'coverage': 'Current retained member metadata and latest direct requester-owned request per member. No history or runtime was loaded. Progress may change between pages.',
        'authority': 'Orientation only. Previews do not replace exact task constraints, corrections, Shared Work or evidence; idle is not completion.'}
    for sid in identities[offset:offset + limit]:
        member = value['members'][sid]
        row = owner.tasks.member(value, sid, lookup=lookup)
        item = {'sessionId': sid, 'membership': member['status'], 'role': member.get('role'),
            'available': bool(row is not None and row.get('workspace') == source['workspace'])}
        if item['available']:
            item.update(title=preview(row.get('title'), 120), status=row.get('status'),
                task=task_preview(service, row),
                inspectRef={'action': 'session.inspect', 'args': {'id': sid}})
            request_row = service.db.execute("""SELECT receipt FROM commands
                WHERE json_extract(receipt,'$.commandAction')='coordination.send'
                AND json_extract(receipt,'$.senderSessionId')=?
                AND json_extract(receipt,'$.target.sessionId')=?
                AND json_extract(receipt,'$.workspace')=?
                AND json_extract(receipt,'$.assignmentId')=?
                AND coalesce(json_extract(receipt,'$.origin'),'')!='system'
                ORDER BY rowid DESC LIMIT 1""", (source['id'], sid, source['workspace'], value['id'])).fetchone()
            item['latestRequest'] = None
            if request_row:
                from .collaboration_observations import outcome, phase
                request = json.loads(request_row[0])
                report = outcome(request)
                item['latestRequest'] = {'requestId': request['requestId'], 'delivery': request.get('delivery'),
                    'label': phase(request), 'outcome': report.get('outcome'),
                    'qualified': report.get('qualified') is True,
                    'outcomeRevision': report.get('outcomeRevision'), 'text': preview(report.get('text')),
                    'resultRef': {'action': 'coordination.result', 'args': {'requestId': request['requestId']}}}
        if size(result) + size(item) > MAX_BYTES - 768:
            if not result['members']:
                service.collaboration.error('This brief row exceeds the bounded page; inspect the exact assignment.', 409)
            break
        result['members'].append(item)
    end = offset + len(result['members'])
    if end < len(identities):
        result['nextAfter'] = base64.urlsafe_b64encode(json.dumps([scope, end]).encode()).decode()
    assert size(result) <= MAX_BYTES
    return result
