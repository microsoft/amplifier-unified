"""Optional work responsibility in the existing assignment/command receipts."""
from __future__ import annotations

import copy
import time
import uuid

from amplifier_operations.coordination import fingerprint

MAX_TASKS = 128


def definitions(schema, string):
    source = {'sessionId': string(200)}
    target = {**source, 'taskId': string(200)}
    revision = {**target, 'expectedRevision': {'type': 'integer', 'minimum': 1}}
    content = {'title': {**string(200), 'minLength': 1}, 'description': string(4000),
               'blockedReason': string(1000),
               'dependsOn': {'type': 'array', 'maxItems': 32, 'uniqueItems': True, 'items': string(200)}}
    claim = {**revision, 'claimId': string(200)}
    return {
        'assignment.tasks.list': ('Read optional assignment tasks without loading history or starting work. Independent chats are not enrolled. Completion is a participant report, not verified artifact success.', schema(source, ['sessionId'])),
        'assignment.tasks.wait': ('Passively wait for a scoped task-list change; rechecks membership after every wait. No execution or history reads.', schema({**source, 'afterVersion': string(64), 'waitMs': {'type': 'integer', 'minimum': 0, 'maximum': 30000}}, ['sessionId', 'afterVersion'])),
        'assignment.tasks.read': ('Read one task and its current claim authority. A historical claim receipt alone does not authorize completion.', schema(target, ['sessionId', 'taskId'])),
        'assignment.tasks.create': ('Assignment owner creates a task. Dependencies must be existing assignment tasks. Does not commission or start work.', schema({**source, **content, 'expectedAssignmentRevision': {'type': 'integer', 'minimum': 1}}, ['sessionId', 'title', 'expectedAssignmentRevision'])),
        'assignment.tasks.update': ('Owner edits an unclaimed unfinished task, including its block and acyclic dependencies. Completed/cancelled tasks cannot be reopened.', schema({**revision, **content}, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.claim': ('Atomically claim a ready assignment task for your own bound conversation. One concurrent claimant wins. Retain claimId and revision; claiming never starts execution or locks files.', schema(revision, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.release': ('Holder or assignment owner explicitly releases this exact claim. Prior external effects may remain; release does not stop a process or imply safe replay.', schema(claim, ['sessionId', 'taskId', 'expectedRevision', 'claimId'])),
        'assignment.tasks.complete': ('Current holder reports completion of the exact valid claim. This is reported completion, not independently verified artifacts. Dependencies become ready but never execute automatically.', schema({**claim, 'report': {**string(4000), 'minLength': 1}, 'references': {'type': 'array', 'maxItems': 16, 'items': string(2000)}}, ['sessionId', 'taskId', 'expectedRevision', 'claimId', 'report'])),
        'assignment.tasks.cancel': ('Owner cancels an unfinished work item, preserving its claim and prior transition receipts. This does not stop live execution.', schema(revision, ['sessionId', 'taskId', 'expectedRevision'])),
        'assignment.tasks.reassign': ('Owner explicitly replaces responsibility with a new claim for a current member. Fences the old claim; does not stop prior effects or start new work. Inspect uncertain effects first.', schema({**revision, 'holderSessionId': string(200)}, ['sessionId', 'taskId', 'expectedRevision', 'holderSessionId'])),
    }


class AssignmentTasks:
    def __init__(self, assignments):
        self.assignments = assignments
        self.service = assignments.service
        self.collaboration = self.service.collaboration

    def error(self, detail, status=409):
        self.collaboration.error(detail, status)

    def member(self, value, sid):
        if value['members'].get(sid, {}).get('status') != 'bound':
            return None
        try:
            row = self.service._session(sid, hydrate=False)
        except Exception:
            return None
        identity = row.get('assignmentId') or (row.get('collaboration') or {}).get('assignmentId')
        return row if identity == value['id'] else None

    def claim_status(self, value, task):
        claim = task.get('claim')
        if not claim:
            return None
        status, detail = 'current', 'Responsibility is retained; no execution is implied.'
        member = value['members'].get(claim['sessionId'], {})
        holder = self.member(value, claim['sessionId'])
        if value['status'] != 'active':
            status, detail = 'stale', 'The assignment was stopped. Inspect prior effects before explicitly releasing this claim.'
        elif member.get('status') != 'bound':
            status, detail = 'stale', 'The holder is no longer an assignment member. Explicit owner reconciliation is required.'
        elif holder is None:
            status, detail = 'unavailable', 'Current holder authority is unavailable. The task remains claimed.'
        elif holder.get('interruptionRevision', 0) != claim['interruptionRevision']:
            status, detail = 'stale', 'The holder was explicitly stopped after this claim. Release or reassign explicitly before continuing.'
        return {**claim, 'status': status, 'detail': detail}

    def readiness(self, tasks, task):
        blocked = [identity for identity in task.get('dependsOn', [])
                   if tasks.get(identity, {}).get('status') != 'completed']
        return ('blocked' if blocked or task.get('blockedReason') else 'ready'), blocked

    def project(self, value, task):
        readiness, blocked = self.readiness(value.get('tasks', {}), task)
        return {**copy.deepcopy(task), 'readiness': readiness, 'blockedBy': blocked,
                'claim': self.claim_status(value, task)}

    def snapshot(self, source, task_id=None):
        value = self.assignments.current(source)
        if not value:
            if task_id:
                self.error('This conversation has no assignment task list.', 404)
            return {'assignmentId': None, 'tasks': [], 'members': [], 'canManage': False, 'version': 'unassigned'}
        if self.member(value, source['id']) is None:
            self.error('Read tasks only as a current bound assignment member.', 403)
        tasks = value.get('tasks', {})
        if task_id and task_id not in tasks:
            self.error('Task not found in this assignment.', 404)
        result = {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
                  'ownerSessionId': value['ownerSessionId'], 'assignmentStatus': value['status'],
                  'canManage': source['id'] == value['ownerSessionId'],
                  'assignmentControl': {key: copy.deepcopy(value[key]) for key in
                      ('id', 'revision', 'status', 'ownerSessionId', 'limits', 'members', 'integration') if key in value},
                  'tasks': [self.project(value, task) for task in tasks.values()],
                  'members': []}
        for sid in value['members']:
            row = self.member(value, sid)
            if row:
                result['members'].append({'sessionId': sid, 'label': row.get('title') or 'Conversation'})
        result['version'] = fingerprint([value['id'], value['revision'],
            [(t['id'], (t.get('claim') or {}).get('status')) for t in result['tasks']], result['members']])
        if task_id:
            result['task'] = self.project(value, tasks[task_id])
        return result

    def content(self, value, task, args):
        result = {key: args.get(key, task.get(key, [] if key == 'dependsOn' else ''))
                  for key in ('title', 'description', 'blockedReason', 'dependsOn')}
        if not result['title'].strip():
            self.error('Give the task a title.', 400)
        dependencies = result['dependsOn']
        tasks = value.get('tasks', {})
        if len(dependencies) != len(set(dependencies)) or any(key not in tasks or key == task['id'] for key in dependencies):
            self.error('Dependencies must be distinct existing tasks in this assignment, excluding this task.', 400)
        graph = {key: row.get('dependsOn', []) for key, row in tasks.items()}
        graph[task['id']] = dependencies
        def visit(key, visiting, done):
            if key in visiting:
                self.error('Task dependencies cannot form a cycle.', 400)
            if key in done:
                return
            visiting.add(key)
            for dependency in graph.get(key, []):
                visit(dependency, visiting, done)
            visiting.remove(key)
            done.add(key)
        done = set()
        for key in graph:
            visit(key, set(), done)
        return copy.deepcopy(result)

    async def dispatch(self, action, args, origin, command_id, caller):
        source = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
        if source['id'] != args['sessionId']:
            self.error('Task actions belong to the calling conversation.', 403)
        identity = command_id or str(uuid.uuid4())
        digest = fingerprint([action, args, origin, source['id']])
        if action == 'assignment.tasks.wait':
            def read():
                current = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
                result = self.snapshot(current)
                return {'accepted': True, 'result': result, 'changed': result['version'] != args['afterVersion']}
            return await self.service.coordination.signal.wait(read, wait_ms=args.get('waitMs', 30000))
        async with self.service.lock:
            source = self.collaboration.source({'sessionId': args['sessionId']}, origin, caller, hydrate=False)
            if source['id'] != args['sessionId']:
                self.error('Task actions belong to the calling conversation.', 403)
            if action in {'assignment.tasks.list', 'assignment.tasks.read'}:
                return {'accepted': True, 'result': self.snapshot(source, args.get('taskId'))}
            async with self.assignments.mutation(source, identity):
                if origin not in {'ui', 'user'}:
                    self.collaboration.active_binding(source)
                value = self.assignments.current(source)
                if not value or self.member(value, source['id']) is None:
                    self.error('Task changes require a current bound assignment member.', 403)
                duplicate = self.collaboration.duplicate(identity, digest)
                if duplicate:
                    # Reauthorize current membership before exposing any saved
                    # result. The old receipt never renews its historical claim.
                    return {**duplicate, 'historical': True}
                owner = source['id'] == value['ownerSessionId']
                verb = action.removeprefix('assignment.tasks.')
                if verb in {'create', 'update', 'cancel', 'reassign'} and not owner:
                    self.error('Only the commissioning owner can manage shared tasks.', 403)
                if value['status'] != 'active' and verb not in {'release', 'cancel'}:
                    self.error('The assignment is stopped; only explicit release or cancellation is available.')
                tasks = value.setdefault('tasks', {})
                if verb == 'create':
                    if args['expectedAssignmentRevision'] != value['revision']:
                        self.error('The assignment revision changed; refresh before creating a task.')
                    if len(tasks) >= MAX_TASKS:
                        self.error('This assignment already retains 128 tasks. Existing task evidence is not discarded.', 429)
                    task = {'id': 'task:' + str(uuid.uuid5(uuid.NAMESPACE_URL, value['id'] + ':' + identity)),
                            'revision': 1, 'status': 'open', 'createdAt': time.time(), 'createdBy': source['id']}
                    task.update(self.content(value, task, args))
                    tasks[task['id']] = task
                else:
                    task = tasks.get(args['taskId'])
                    if not task:
                        self.error('Task not found in this assignment.', 404)
                    if task['revision'] != args['expectedRevision']:
                        self.error('The task revision changed; refresh and inspect its claim before acting.')
                    if task['status'] in {'completed', 'cancelled'}:
                        self.error('Completed and cancelled tasks cannot be changed or reopened.')
                    claim = task.get('claim')
                    if verb in {'release', 'complete'}:
                        if not claim or claim['id'] != args['claimId']:
                            self.error('The exact current claim ID is required; a previous claim cannot act.')
                        if source['id'] != claim['sessionId'] and not (owner and verb == 'release'):
                            self.error('Only the claim holder can report completion; holder or owner may release it.', 403)
                    if verb == 'update':
                        if claim:
                            self.error('Release the claim before editing this task or its dependencies.')
                        task.update(self.content(value, task, args))
                    elif verb in {'claim', 'reassign'}:
                        if verb == 'claim' and claim:
                            self.error('This task is already claimed. Stale claims require explicit reconciliation.')
                        if self.readiness(tasks, task)[0] != 'ready':
                            self.error('This task is blocked. Resolve its block and reported dependencies before claiming.')
                        holder_id = args['holderSessionId'] if verb == 'reassign' else source['id']
                        holder = self.member(value, holder_id)
                        if not holder or holder.get('status') in {'stopping', 'stopped'}:
                            self.error('Choose a current bound member whose conversation is not stopped.')
                        task['claim'] = {'id': 'claim:' + str(uuid.uuid5(uuid.NAMESPACE_URL, task['id'] + ':' + identity)),
                                         'sessionId': holder_id, 'interruptionRevision': holder.get('interruptionRevision', 0),
                                         'claimedAt': time.time(), 'declaredBy': source['id']}
                        task['status'] = 'claimed'
                    elif verb == 'release':
                        task.pop('claim')
                        task['status'] = 'open'
                    elif verb == 'complete':
                        if self.claim_status(value, task)['status'] != 'current':
                            self.error('This claim is stale or unavailable. Inspect prior effects, then explicitly release or reassign it.')
                        if not args['report'].strip():
                            self.error('Give a completion report.', 400)
                        task['status'] = 'completed'
                        task['completion'] = {'report': args['report'], 'references': args.get('references', []),
                                              'reportedBy': source['id'], 'reportedAt': time.time(),
                                              'verification': 'participant_report'}
                    elif verb == 'cancel':
                        task['status'] = 'cancelled'
                        task['cancelledBy'] = source['id']
                    else:
                        self.error('Unknown task transition.', 400)
                    task['revision'] += 1
                task['updatedAt'] = time.time()
                value['revision'] += 1
                self.assignments.save(value)
                result = {'assignmentId': value['id'], 'assignmentRevision': value['revision'],
                          'task': self.project(value, task), 'observedAt': time.time()}
                receipt = {'accepted': True, 'commandAction': action, 'assignmentId': value['id'],
                           'result': result}
                self.collaboration.insert(identity, digest, receipt)
                self.service._publish_changes(sessions={source['id']})
                return receipt
