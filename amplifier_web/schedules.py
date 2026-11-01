"""Host adapter for explicitly authorized, non-replaying scheduled work."""
import asyncio
import copy
import json
import time
import uuid

from amplifier_scheduling import ScheduleStore, normalize, next_after, preview
from amplifier_scheduling.store import ACTIVE_RUNS, fingerprint


def definitions(schema, string):
    sid = {'sessionId': string(200)}
    identity = {**sid, 'id': string(200)}
    revision = {'expectedRevision': {'type': 'integer', 'minimum': 0}}
    fields = {'prompt': {**string(16000), 'minLength': 1}, 'spec': {'type': 'object'}, 'kind': {'enum': ['task', 'monitor']}, 'missedRunPolicy': {'enum': ['skip', 'latest']}, 'notificationPolicy': {'enum': ['changes', 'always', 'failures_only']}}
    destination = {'destination': {'enum': ['same_task', 'new_task']}, 'newTaskTitle': string(200), 'newTaskMaxTurns': {'type': 'integer', 'minimum': 1, 'maximum': 100}}
    authorization = {'previewHash': string(100), 'sourceMessageId': string(200)}
    return {
        'schedule.overview': ('Read a bounded page of chat-owned schedules without histories or worker starts.', schema({'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'status': {'enum': ['current', 'all']}}, [])),
        'schedule.repairPreview': ('Preview owner-local future new-task recovery from saved configuration without waking the owner. Full accepts current native work or a fresh explicitly identified trusted system caller; Ask uses the human Automation controls.', schema({**identity, **revision})),
        'schedule.repairResume': ('Resume reviewed future new-task occurrences under current Full or human confirmation. Only a fresh trusted system request or human confirmation can cross owner Stop; old stopped native work cannot rearm itself. Never replays uncertain work or resumes the old generation.', schema({**identity, **revision, 'previewHash': string(100), 'acknowledgeStop': {'type': 'boolean'}}, [*identity, *revision, 'previewHash'])),
        'schedule.list': ('List saved schedules in any registered conversation in this user’s library, without starting work. Cross-conversation mutations remain unavailable.', schema(sid)),
        'schedule.read': ('Read a schedule and its bounded durable run history from any registered conversation in this user’s library. Unknown is never replayed.', schema(identity)),
        'schedule.preview': ('Preview concrete timezone occurrences and exact configuration for authorized scheduled work. destination defaults to same_task; new_task explicitly creates a fresh user-owned task for every run using the reviewed source configuration. Does not save or submit work.', schema({**sid, **fields, **destination}, [*sid, *fields])),
        'schedule.create': ('Save and activate exactly the reviewed prompt/timing. Full access needs no human message; Ask agents must cite the actual user sourceMessageId. A pending question never authorizes a schedule. Tool permissions still apply at execution.', schema({**sid, **revision, **fields, **destination, **authorization}, [*sid, *revision, *fields, 'previewHash'])),
        'schedule.update': ('Review and replace the saved prompt/timing against the latest task correction. Requires exact revision and concrete preview; Full retains current caller authority, while Ask requires renewed explicit user provenance.', schema({**identity, **revision, **fields, **destination, **authorization}, [*identity, *revision, *fields, 'previewHash'])),
        'schedule.pause': ('Pause future due runs. Current admitted work can finish; no rollback is claimed.', schema({**identity, **revision})),
        'schedule.resume': ('Deliberately resume after concrete review. Requires current preview and current Full authority or explicit user provenance. An admitted scheduleRepair handoff may supply handoffRequestId and the original human sourceMessageId for unchanged existing schedules. Never replays uncertain runs.', schema({**identity, **revision, **authorization, 'handoffRequestId': string(200)}, [*identity, *revision, 'previewHash'])),
        'schedule.cancel': ('Cancel future due runs while preserving run evidence. Does not undo admitted work.', schema({**identity, **revision})),
        'schedule.report': ('Record attributable monitor evidence for this scheduled run. Compared values are retained; agent-reported unchanged is labelled as a claim, not proof. This does not complete the task or grant permission.', schema({**sid, 'runId': string(200), **revision, 'outcome': {'enum': ['changed', 'unchanged']}, 'detail': string(4000), 'values': {}}, [*sid, 'runId', *revision, 'outcome', 'detail'])),
        'schedule.reconcile': ('Record an attributable finding that an uncertain run completed or was abandoned. Full needs no human message; Ask retains user evidence provenance. Never resubmits the old input. Schedule resume remains separate.', schema({**sid, 'runId': string(200), **revision, 'resolution': {'enum': ['completed', 'abandoned']}, 'evidence': {**string(4000), 'minLength': 1}, 'sourceMessageId': string(200)}, [*sid, 'runId', *revision, 'resolution', 'evidence'])),
    }


class Schedules:
    def __init__(self, service, *, clock=time.time):
        self.app, self.clock = service, clock
        self.store = ScheduleStore(service.data_dir / 'schedules.sqlite3')
        self.runner = None
        self.attached = False

    def start(self):
        if self.runner is None: self.runner = asyncio.create_task(self.loop())
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'register_source') and not self.attached:
            operations.register_source('schedule', self.operation_records, self.operation_record)
            self.attached = True

    async def close(self):
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)

    async def loop(self):
        while not self.app.closed:
            try:
                self.app.observations.wake()
                await self.tick()
            except asyncio.CancelledError: raise
            except Exception as exc:
                async with self.app.lock:
                    self.app.state['scheduleError'] = str(exc)[:1000]
                    self.app._publish_changes(globals={'scheduleError'})
            await asyncio.sleep(2)

    def sync(self):
        schedules = self.store.projection()
        index = self.app.projections.sessions(self.app.state)
        previous = getattr(self, '_projected_ids', None)
        if previous is None:
            previous = {sid for sid, row in index.by_id.items() if row.get('schedules')}
        dirty = set()
        for sid in set(schedules) | previous:
            session = index.by_id.get(sid)
            if session is not None and session.get('schedules', []) != schedules.get(sid, []):
                session['schedules'] = schedules.get(sid, [])
                dirty.add(sid)
        self._projected_ids = set(schedules)
        return dirty

    def changed(self):
        changed = self.sync()
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'notify'): operations.notify()
        self.app._publish_changes(sessions=changed)

    def operation_records(self, sid): return [self.operation_shape(row) for row in self.store.runs(sid)]
    def operation_record(self, sid, identity):
        try: return self.operation_shape(self.store.run(sid, identity))
        except ValueError: return None
    @staticmethod
    def operation_shape(row):
        return {'id': row['id'], 'sessionId': row['sessionId'], 'source': 'schedule', 'kind': 'scheduled-run', 'state': {'creating':'queued','claimed':'queued','submitting':'queued','accepted':'queued','unknown':'outcome_unknown','skipped':'cancelled','abandoned':'cancelled'}.get(row['phase'], row['phase']), 'revision': row['revision'], 'controlAvailable': False, 'evidence': copy.deepcopy(row), 'createdAt': row['createdAt'], 'updatedAt': row['updatedAt']}

    async def task(self, sid, config=None, *, prepare=False):
        if config and config.get('destination') == 'new_task':
            from .session_creation import template
            owner = self.app._session(sid, hydrate=False)
            directory = self.app.data_dir / 'sessions' / (owner.get('runtimeSessionId') or sid)
            if prepare and not any((directory / name).exists() for name in ('effective-configuration.json', 'configuration.json')):
                if owner.get('status') in {'stopped', 'stopping', 'interrupted', 'error'}:
                    raise ValueError('The owner has no saved configuration. Review it in its chat before scheduling new work.')
                await self.app.management.ensure_runtime(owner)
            template(self.app, owner)
            return None
        if not self.app.management or not self.app.runtime: raise ValueError('The runtime is unavailable')
        await self.app.history.ensure_loaded(sid)
        await self.app.management.ensure_runtime(self.app._session(sid))
        value = await self.app.runtime.control(sid, 'task.get', {})
        if not value.get('task'): raise ValueError('Save an explicit task before scheduling its continuation')
        return value['task']

    def configuration(self, args):
        result = {key: copy.deepcopy(args[key]) for key in ('prompt', 'kind', 'missedRunPolicy', 'notificationPolicy')} | {'spec': normalize(args['spec']), 'destination': args.get('destination', 'same_task')}
        if args.get('destination') == 'new_task':
            result.update(destination='new_task', newTaskTitle=args.get('newTaskTitle') or 'Scheduled task', newTaskMaxTurns=args.get('newTaskMaxTurns', 1))
        return result

    def reviewed(self, sid, config, task):
        owner = self.app._session(sid, hydrate=False)
        binding = {'sessionId': sid, 'taskId': task['id'] if task else None, 'taskRevision': task['revision'] if task else None, 'interruptionRevision': owner.get('interruptionRevision', 0), 'executionRevision': owner.get('executionRevision', 0), **config}
        if config.get('destination') == 'new_task':
            from .session_creation import template
            binding['newTaskConfiguration'] = {key: value for key, value in template(self.app, owner)[1].items() if key != 'legacyConfigurationHash'}
        return {'previewHash': fingerprint(binding), 'binding': binding, 'occurrences': preview(config['spec'], self.clock(), 5), 'daylightSaving': 'Nonexistent local times are skipped; repeated local times run once at the first occurrence.', 'missedRunPolicy': config['missedRunPolicy'], 'notificationPolicy': config['notificationPolicy']}

    def full_authority(self, sid, origin, *, fresh_recovery=False):
        if origin not in {'agent', 'system'}:
            return None
        session = self.app._session(sid, hydrate=False)
        authority = self.app.access_mode.capture_full(session, origin)
        if authority is not None:
            from .collaboration import BINDING, PRINCIPAL
            fresh_system = origin == 'system' and not (BINDING.get() or PRINCIPAL.get())
            if session.get('status') in {'stopped', 'stopping'} and not (fresh_recovery and fresh_system):
                raise ValueError('Stopped work cannot authorize its own future schedule. Review future recovery separately.')
            authority.update(workspace=session.get('workspace'),
                nativeSessionId=session.get('runtimeSessionId') or session.get('nativeIdentity') or sid,
                executionRevision=session.get('executionRevision', 0),
                interruptionRevision=session.get('interruptionRevision', 0))
            if reason := self.authority_reason(session, authority):
                raise ValueError(reason)
        return authority

    def authority_reason(self, session, authority):
        # Accepted recurring work outlives its original root/child generation.
        # A fresh mutation authenticates the actor; due effects check this saved
        # source and policy witness, never a later capture or stale invocation.
        if (not self.app.access_mode.full(session, authority['accessRevision'])
                or session['id'] != authority['sessionId']
                or session.get('workspace') != authority['workspace']
                or (session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']) != authority['nativeSessionId']
                or any(session.get(key, 0) != authority[key]
                       for key in ('executionRevision', 'interruptionRevision'))):
            return 'Full schedule authority changed or its work was stopped or moved. Review future work before continuing.'
        return self.app.assignments.blocked(session)

    def authorization(self, sid, args, origin, *, previous=None, action=None, authority=None):
        if authority is not None:
            if reason := self.authority_reason(self.app._session(sid, hydrate=False), authority):
                raise ValueError(reason)
            return {'origin': origin, 'authority': copy.deepcopy(authority), 'at': self.clock()}
        if origin == 'ui': return {'origin': 'ui', 'at': self.clock()}
        if origin not in {'agent', 'voice', 'user'}: raise ValueError('Only an explicit user request can authorize scheduled work')
        handoff = None
        if args.get('handoffRequestId'):
            from .schedule_handoff import authorize
            _, source, handoff = authorize(self.app, sid, args, previous, action)
        else:
            source = next((row for row in self.app._session(sid)['messages'] if row['id'] == args.get('sourceMessageId') and row.get('role') == 'user'), None)
        if not source or source.get('inputOrigin') not in {'ui', 'user', 'voice'} or source.get('questionId') or source.get('scheduledRunId'):
            raise ValueError('Cite the actual user message requesting this schedule; an agent message or question answer is not schedule authorization')
        if previous and previous.get('status') == 'needs_review' and (previous.get('authorization', {}).get('messageId') == source['id'] or source.get('createdAt', 0) <= previous.get('authorization', {}).get('at', 0)):
            raise ValueError('Cite the new user request or correction that authorizes this schedule review')
        return {**({'handoff': handoff} if handoff else {}), 'origin': origin, 'messageId': source['id'], 'text': source.get('text', ''), 'inputOrigin': source['inputOrigin'], 'messageCreatedAt': source.get('createdAt', 0), 'textDigest': fingerprint(source.get('text', '')), 'voiceId': source.get('voiceId'), 'voiceItemId': source.get('voiceItemId'), 'at': self.clock()}

    async def dispatch(self, action, args, origin, command_id, caller=None):
        if action == 'schedule.overview':
            from .automation_overview import overview
            return overview(self, args)
        if action in {'schedule.repairPreview', 'schedule.repairResume'}:
            from .automation_overview import repair
            return await repair(self, action, args, origin, command_id or str(uuid.uuid4()), caller)
        sid = args['sessionId']
        self.app._session(sid)
        if action in {'schedule.list', 'schedule.read'}:
            async with self.app.lock:
                owner = sid
                if action == 'schedule.read':
                    linked = next((row for row in self.store.execution_runs(sid) if row['scheduleId'] == args['id'] and row.get('destinationSessionId') == sid), None)
                    if linked: owner = linked['sessionId']
                result = {'items': self.store.list(sid), 'incomingRuns': [row for row in self.store.execution_runs(sid) if row.get('destinationSessionId') == sid]} if action == 'schedule.list' else {'schedule': self.store.get(owner, args['id']), 'runs': self.store.runs(owner, args['id']), 'readOnly': owner != sid}
                changed = self.sync()
                if changed: self.app._publish_changes(sessions=changed)
                return result
        if origin in {'agent', 'system'} and (not caller or caller != sid):
            raise ValueError('Schedule mutations must identify the calling conversation.')
        if action in {'schedule.report', 'schedule.reconcile'}:
            return await self.run_command(action, args, origin, command_id or str(uuid.uuid4()))
        command_id = command_id or str(uuid.uuid4())
        # sessionId is the canonical caller/owner above; preserve the original
        # command identity independently of changing generations/access modes.
        intent = {**args, 'authorizationOrigin': origin}
        async with self.app.lock:
            if action != 'schedule.preview':
                retained = self.store.receipt(command_id, fingerprint([action, intent]))
                if retained is not None:
                    return retained
            authority = (self.full_authority(sid, origin)
                if action in {'schedule.create', 'schedule.update', 'schedule.resume'} else None)
        config = self.configuration(args) if action in {'schedule.preview', 'schedule.create', 'schedule.update'} else None
        if action == 'schedule.resume': config = self.configuration(self.store.get(sid, args['id']))
        task = await self.task(sid, config, prepare=True) if action in {'schedule.preview', 'schedule.create', 'schedule.update', 'schedule.resume'} else None
        if action == 'schedule.preview': return self.reviewed(sid, config, task)
        if args.get('handoffRequestId'):
            receipt = self.app.collaboration.receipt(args['handoffRequestId'])
            if (receipt.get('commandAction') != 'coordination.send' or not receipt.get('scheduleRepair')
                    or receipt.get('target', {}).get('sessionId') != sid):
                raise ValueError('Choose a schedule repair handoff addressed to this owner.')
            await self.app.history.ensure_loaded(receipt['senderSessionId'])
        async with self.app.lock:
            def build(previous):
                now = self.clock()
                if action == 'schedule.create':
                    if len(self.store.list(sid)) >= 100: raise ValueError('This conversation already has 100 schedules')
                    record = {'id': str(uuid.uuid4()), 'sessionId': sid, 'createdAt': now}
                else:
                    record = previous
                    if record['status'] == 'cancelled': raise ValueError('This schedule is cancelled; create a new one explicitly')
                if action in {'schedule.create', 'schedule.update', 'schedule.resume'}:
                    if task is not None and task['status'] != 'active': raise ValueError('Resume the saved task explicitly before activating its schedule')
                    effective = config or self.configuration(record)
                    reviewed = self.reviewed(sid, effective, task)
                    if args['previewHash'] != reviewed['previewHash']: raise ValueError('The task, stop intent, or schedule preview changed; preview again')
                    if any(run['phase'] == 'unknown' for run in self.store.runs(sid, record['id'])): raise ValueError('Reconcile the uncertain prior run before resuming')
                    authorization = self.authorization(sid, args, origin, previous=previous, action=action, authority=authority)
                    authorization['commandId'] = command_id
                    next_due = next_after(effective['spec'], now)
                    if next_due is None: raise ValueError('Choose a future occurrence; past one-shot work is not replayed')
                    for key in ('newTaskConfiguration', 'newTaskTitle', 'newTaskMaxTurns'): record.pop(key, None)
                    record.update(reviewed['binding'], status='active', nextDue=next_due, authorization=authorization, reviewReason=None)
                elif action == 'schedule.pause': record['status'] = 'paused'
                elif action == 'schedule.cancel': record['status'] = 'cancelled'
                else: raise ValueError('Unknown schedule action')
                record['updatedAt'] = now
                return record
            result = self.store.mutate(action, intent, command_id, build)
            self.changed()
            return result

    async def run_command(self, action, args, origin, command_id):
        sid, identity, now = args['sessionId'], args['runId'], self.clock()
        async with self.app.lock:
            owner = self.store.execution_run(sid, identity)['sessionId'] if action == 'schedule.report' else sid
            signature = fingerprint([action, args, origin])
            retained = self.store.receipt(command_id, signature)
            if retained is not None:
                return retained
            authority = self.full_authority(sid, origin, fresh_recovery=True) if action == 'schedule.reconcile' else None
            def edit(run):
                if action == 'schedule.report':
                    if run['phase'] not in {'submitting', 'accepted', 'running'}: raise ValueError('Only an admitted current run can report monitor evidence')
                    values = args.get('values')
                    if len(json.dumps(values)) > 64000: raise ValueError('Keep compared monitor values below 64 KB')
                    report = {'outcome': args['outcome'], 'detail': args['detail'], 'source': {'agent': 'agent_report', 'system': 'system_report'}.get(origin, 'user_report'), 'at': now}
                    if 'values' in args: report.update(values=copy.deepcopy(values), valuesDigest=fingerprint(values))
                    run['report'] = report
                else:
                    if run['phase'] != 'unknown': raise ValueError('Only an uncertain run requires reconciliation')
                    provenance = self.authorization(sid, args, origin, authority=authority)
                    if origin != 'ui' and authority is None and provenance.get('messageCreatedAt', 0) < run['createdAt']:
                        raise ValueError('Reconciliation needs the user’s actual finding after this run, not an older request')
                    run.update(phase=args['resolution'], reconciliation={'evidence': args['evidence'], 'provenance': provenance, 'at': now}, detail='Explicitly reconciled without replaying its input.')
                run['updatedAt'] = now
            result = self.store.run_command(owner, identity, args['expectedRevision'], command_id, signature, edit)
            self.changed()
            return result

    def dependency_reason(self, session, task, schedule):
        if reason := self.app.assignments.blocked(session):
            return reason
        if session.get('configurationBusy') or session.get('fileAccessPending'):
            return 'This task has an unresolved configuration change or execution-folder handoff.'
        if session.get('executionRevision', 0) != schedule.get('executionRevision', 0):
            return 'The task execution folder changed. Review the schedule for its current checkout.'
        if session.get('interruptionRevision', 0) != schedule['interruptionRevision']:
            return 'The user stopped this conversation. Review the schedule before resuming.'
        authorization = schedule.get('authorization', {})
        if authority := authorization.get('authority'):
            if reason := self.authority_reason(session, authority):
                return reason
        authority_session = session
        if handoff := authorization.get('handoff'):
            try:
                authority_session = self.app._session(handoff['senderSessionId'], hydrate=False)
            except Exception:
                return 'The schedule repair authorizing chat is unavailable. Review authorization.'
            if (authority_session.get('workspace') != session.get('workspace') or
                    authority_session.get('interruptionRevision', 0) != handoff['sourceInterruptionRevision']):
                return 'The authorizing chat was stopped or changed workspace. Review authorization.'
            from .schedule_handoff import latest_human
            if (latest_human(authority_session) or {}).get('id') != authorization.get('messageId'):
                return 'The authorizing chat has a newer human request. Review schedule authorization.'
        if authorization.get('messageId'):
            source = next((row for row in authority_session['messages'] if row['id'] == authorization['messageId']), None)
            if handoff and source is None:
                return 'The original schedule repair request is unavailable. Review authorization.'
            if source is not None and fingerprint(source.get('text', '')) != authorization.get('textDigest'):
                return 'The original schedule request was edited. Review the saved authorization.'
        if schedule.get('destination') == 'new_task':
            from .session_creation import template
            try: current_configuration = template(self.app, session)[1]
            except (ValueError, OSError): return 'The saved configuration is unavailable. Review it before creating another task.'
            from .session_creation import configuration_matches, configuration_changes
            if not configuration_matches(schedule['newTaskConfiguration'], current_configuration):
                fields = configuration_changes(schedule['newTaskConfiguration'], current_configuration)
                return 'The configuration for new tasks changed' + (': ' + ', '.join(fields) if fields else '') + '. Review the schedule before creating another task.'
            return None
        if task['id'] != schedule['taskId'] or task['revision'] != schedule['taskRevision']:
            return 'The saved task has a newer objective, correction, or lifecycle revision. Review the schedule against it.'
        if task['status'] != 'active': return 'The saved task is paused, blocked, or completed.'
        for identity in task.get('questionIds', []):
            try: question = self.app.questions.store.get(session['id'], identity)
            except Exception: return 'A linked question is unavailable; inspect it before continuing.'
            if question['required'] and question['status'] != 'answered': return 'A required linked question remains unanswered. It is not schedule authorization.'
        return None

    async def tick(self):
        now = self.clock()
        if not self.store.acquire(now): return
        if self.store.recovered:
            async with self.app.lock:
                for run in self.store.recovered:
                    self.decide_notification(self.app._session(run['sessionId']), run)
                self.changed()
        from .updates import work_paused
        from .update_drain import held
        if work_paused(self.app.state) or held(self.app): return
        for schedule in self.store.list():
            if schedule['status'] != 'active' or schedule.get('nextDue') is None or schedule['nextDue'] > now: continue
            sid = schedule['sessionId']
            session = self.app._session(sid)
            if session.get('configurationBusy') or session.get('fileAccessPending') or session.get('status') in {'working', 'starting', 'running', 'busy', 'stopping'}: continue
            try:
                if handoff := schedule.get('authorization', {}).get('handoff'):
                    await self.app.history.ensure_loaded(handoff['senderSessionId'])
                task = await self.task(sid, schedule)
            except Exception as exc:
                async with self.app.lock:
                    detail = 'Preparation failed before submission: ' + str(exc)[:500]
                    failed = self.store.claim(sid, schedule['id'], self.clock())
                    if failed and failed['phase'] == 'claimed':
                        failed = self.store.transition(sid, failed['id'], ['claimed'], 'failed', self.clock(), detail=detail)
                        self.decide_notification(self.app._session(sid), failed)
                    self.store.review(sid, schedule['id'], detail, self.clock())
                    self.changed()
                continue
            async with self.app.lock:
                if held(self.app):
                    continue  # The occurrence has not been claimed or submitted.
                # Refresh authoritative schedule after asynchronous runtime reads.
                current = self.store.get(sid, schedule['id'])
                if current['revision'] != schedule['revision'] or current['status'] != 'active': continue
                reason = self.dependency_reason(self.app._session(sid), task, current)
                if reason:
                    self.store.review(sid, current['id'], reason, self.clock()); self.changed(); continue
                run = self.store.claim(sid, current['id'], self.clock())
                if run is None: continue
                if run['phase'] == 'skipped': self.changed(); continue
                if current.get('destination') == 'new_task':
                    destination = str(uuid.uuid5(uuid.NAMESPACE_URL, 'unified-scheduled-task:' + run['id']))
                    run = self.store.transition(sid, run['id'], ['claimed'], 'creating', self.clock(), destinationSessionId=destination, interruptionRevision=current['interruptionRevision'], executionRevision=current.get('executionRevision', 0))
                    self.changed()
            if current.get('destination') == 'new_task':
                from .scheduled_destinations import create_destination
                prepared = await create_destination(self, current, run)
                if prepared is None: continue
                run, session, task = prepared
            async with self.app.lock:
                target = session['id']
                # This is the durable handoff boundary. A crash from this point
                # leaves an uncertain input; retries never send it again.
                run = self.store.transition(sid, run['id'], ['claimed', 'creating'], 'submitting', self.clock(), interruptionRevision=current['interruptionRevision'], executionRevision=current.get('executionRevision', 0), destinationInterruptionRevision=run.get('destinationInterruptionRevision', session.get('interruptionRevision', 0)), destinationExecutionRevision=run.get('destinationExecutionRevision', session.get('executionRevision', 0)), taskId=task['id'], taskRevision=task['revision'])
                text = current['prompt']
                if current['kind'] == 'monitor':
                    text += '\n\nScheduled monitor run ' + run['id'] + '. Inspect schedule.read with id ' + current['id'] + ' for prior compared values, then record this run with schedule.report and actual values when available. Label evidence accurately; an unchanged claim is not proof. Do not complete the overall task merely because this run ends.'
                self.app._message(session, 'user', text, 'schedule', inputId=run['inputId'], inputOrigin='scheduler', scheduledRunId=run['id'], delivery={'status': 'sending'})
                from .execution import ensure_turn
                ensure_turn(session, run['inputId'], text)
                session['status'] = 'working'
                self.changed()
            try:
                # Exact task revision and idle admission are rechecked inside the
                # worker command lock, immediately before its nonblocking queue.
                configuration_comparison = {}
                def guard():
                    latest = self.store.get(sid, current['id'])
                    if not self.store.owns(self.clock()): return 'Scheduler ownership changed before admission.'
                    owner = self.app._session(sid)
                    if owner.get('configurationBusy') or owner.get('fileAccessPending') or owner.get('executionRevision', 0) != current.get('executionRevision', 0): return 'The task configuration changed or its execution folder is moving; this occurrence was skipped.'
                    reason = self.dependency_reason(owner, task, current)
                    if reason: return reason
                    destination = self.app._session(target)
                    if run.get('destinationConfigurationHash'):
                        from .session_creation import template, configuration_changes
                        observed = template(self.app, destination)[1]
                        if observed['configurationHash'] != run['destinationConfigurationHash']:
                            previous = {'configurationHash': run['destinationConfigurationHash'],
                                        'configurationParts': run.get('destinationConfigurationParts', {})}
                            fields = configuration_changes(previous, observed)
                            configuration_comparison.update(
                                before={'stage': 'destination_created', **previous},
                                after={'stage': 'input_admission', 'configurationHash': observed['configurationHash'],
                                       'configurationParts': observed['configurationParts']},
                                changedCategories=fields)
                            return ('The new task configuration changed before admission'
                                    + (': ' + ', '.join(fields) if fields else '')
                                    + ' (destination creation compared with input admission).')
                    if destination.get('configurationBusy') or destination.get('fileAccessPending') or destination.get('executionRevision', 0) != run['destinationExecutionRevision'] or destination.get('interruptionRevision', 0) != run['destinationInterruptionRevision']: return 'The destination task changed or stopped before input admission.'
                    if latest['status'] != 'active' or latest['revision'] != current['revision']: return 'The schedule changed before admission.'
                    if self.app._session(sid).get('interruptionRevision', 0) != current['interruptionRevision']: return 'The user stop won input admission; this occurrence was skipped.'
                    return None
                result = await self.app.runtime.scheduled_input(target, {'taskId': task['id'], 'taskRevision': task['revision'], 'inputId': run['inputId'], 'text': text, 'kind': current['kind'], 'interruptionRevision': run['destinationInterruptionRevision']}, guard)
                phase = 'accepted' if result.get('accepted') else 'skipped'
                detail = ('Input admitted to the new task.' if current.get('destination') == 'new_task' else 'Input admitted to the existing task.') if result.get('accepted') else result.get('reason', 'Input was not admitted.')
            except BaseException as exc:
                phase, detail = 'unknown', 'Input handoff outcome is uncertain. No automatic replay: ' + str(exc)[:500]
                cancelled = isinstance(exc, asyncio.CancelledError)
            else: cancelled = False
            async with self.app.lock:
                saved = self.store.transition(sid, run['id'], ['submitting'], phase, self.clock(), detail=detail,
                    **({'destinationConfigurationComparison': configuration_comparison} if configuration_comparison else {}),
                    observedInterruptionRevision=self.app._session(sid).get('interruptionRevision', 0))
                self.app._delivery(self.app._session(target), run['inputId'], 'accepted' if phase == 'accepted' else 'unknown' if phase == 'unknown' else 'rejected')
                if saved['phase'] in {'unknown', 'skipped'}:
                    self.store.review(sid, current['id'], detail, self.clock())
                    self.app._session(target)['status'] = 'interrupted' if phase == 'unknown' else 'idle'
                    if saved['phase'] == 'unknown': self.decide_notification(self.app._session(target), saved)
                self.changed()
            if cancelled: raise asyncio.CancelledError

    def generation(self, session, payload):
        """Observe exact scheduled inputs; never equate run end with task completion."""
        ids = payload.get('input_ids') or ([payload['initial_input_id']] if payload.get('initial_input_id') else [])
        scheduled = [identity for identity in ids if str(identity).startswith('schedule:')]
        monitor = payload.get('scheduled_monitor_input_id')
        if monitor and monitor not in scheduled: scheduled.append(monitor)
        for identity in scheduled:
            try: run = self.store.execution_run(session['id'], identity)
            except ValueError: continue
            if run.get('destinationSessionId', run['sessionId']) != session['id']: continue
            event = payload.get('event')
            if event == 'generation.started':
                self.store.transition(run['sessionId'], identity, ['submitting', 'accepted'], 'running', self.clock(), generationId=payload.get('generation_id'))
            elif event in {'generation.finished', 'generation.failed', 'generation.detached'}:
                if monitor and event == 'generation.finished':
                    self.store.transition(run['sessionId'], identity, ['submitting', 'accepted', 'running'], 'running', self.clock(), monitorScope=True, parentFinished=event, pendingJobs=sorted(set(run.get('pendingJobs', []) + payload.get('active_job_ids', []))), detail=payload.get('text', 'Waiting for the monitor to finish.'))
                    continue
                if payload.get('active_job_ids'):
                    self.store.transition(run['sessionId'], identity, ['submitting', 'accepted', 'running'], 'running', self.clock(), pendingJobs=payload['active_job_ids'], jobOutcomes={}, parentFinished=event, detail=payload.get('text', 'Waiting for delegated work.'))
                    continue
                phase = 'completed' if event == 'generation.finished' else 'unknown' if event == 'generation.detached' else 'failed'
                run = self.store.transition(run['sessionId'], identity, ['submitting', 'accepted', 'running'], phase, self.clock(), generationId=payload.get('generation_id'), detail=payload.get('text') or payload.get('error') or event)
                self.decide_notification(session, run)
        return bool(payload.get('scheduled_monitor_only')) if monitor else bool(scheduled) and len(scheduled) == len(ids)

    def decide_notification(self, session, run):
        with self.store.transaction():
            run = self.store.run(run['sessionId'], run['id'])
            if run.get('notificationDecision') is not None: return
            schedule = self.store.get(run['sessionId'], run['scheduleId'])
            policy = schedule['notificationPolicy']
            report = run.get('report', {})
            changed, source = None, 'unreported'
            if 'valuesDigest' in report:
                previous = next((other for other in self.store.runs(run['sessionId'], run['scheduleId']) if other['id'] != run['id'] and other['phase'] == 'completed' and 'valuesDigest' in other.get('report', {})), None)
                changed = previous is None or report['valuesDigest'] != previous['report']['valuesDigest']
                source = 'compared_reported_values' if previous else 'initial_reported_values'
            elif report.get('outcome'):
                changed = report['outcome'] == 'changed'
                source = report['source']
            failure = run['phase'] in {'failed', 'unknown'}
            notify = failure or policy == 'always' or (policy == 'changes' and changed is True)
            run['notificationDecision'] = {'notify': notify, 'changed': changed, 'source': source, 'at': self.clock(), 'policy': policy}
            self.store.put_run(run)
            if schedule.get('nextDue') is None and schedule['status'] == 'active':
                schedule.update(status='completed' if run['phase'] == 'completed' else 'needs_review', reviewReason=None if run['phase'] == 'completed' else 'The last scheduled run did not complete successfully.', revision=schedule['revision'] + 1)
                self.store.put(schedule)
        if notify:
            message = {'id': run['id'] + ':notice', 'sessionId': session['id'], 'role': 'assistant', 'via': 'text', 'createdAt': self.clock(), 'text': report.get('detail') or run.get('detail') or 'Scheduled work needs attention.'}
            notices = self.app.state.setdefault('scheduleNotifications', [])
            if not any(row['id'] == message['id'] for row in notices): notices.append(message)
            self.app.state['scheduleNotifications'] = notices[-100:]
            if self.app.management:
                self.app._task(self.app.management.notifications.send(copy.deepcopy(session), {'generation_id': run['id'], 'text': message['text']}))


    def worker(self, session, payload):
        for run in self.store.execution_runs(session['id']):
            if run.get('destinationSessionId', run['sessionId']) != session['id']: continue
            if run['phase'] != 'running' or payload.get('id') not in run.get('pendingJobs', []): continue
            status = payload.get('status')
            if status not in {'completed', 'error', 'cancelled', 'interrupted'}: continue
            outcomes = {**run.get('jobOutcomes', {}), payload['id']: status}
            if run.get('monitorScope') or len(outcomes) < len(run['pendingJobs']):
                self.store.transition(run['sessionId'], run['id'], ['running'], 'running', self.clock(), jobOutcomes=outcomes)
                continue
            phase = 'unknown' if 'interrupted' in outcomes.values() else 'failed' if any(value != 'completed' for value in outcomes.values()) else 'completed'
            result = self.store.transition(run['sessionId'], run['id'], ['running'], phase, self.clock(), jobOutcomes=outcomes)
            self.decide_notification(session, result)


    def idle(self, session):
        for run in self.store.execution_runs(session['id']):
            if run.get('destinationSessionId', run['sessionId']) != session['id']: continue
            if run['phase'] != 'running' or not run.get('monitorScope') or not run.get('parentFinished'): continue
            outcomes = run.get('jobOutcomes', {})
            if len(outcomes) < len(run.get('pendingJobs', [])): continue
            phase = 'unknown' if 'interrupted' in outcomes.values() else 'failed' if any(value != 'completed' for value in outcomes.values()) else 'completed'
            result = self.store.transition(run['sessionId'], run['id'], ['running'], phase, self.clock())
            self.decide_notification(session, result)


    def runtime_ended(self, session):
        for run in self.store.execution_runs(session['id']):
            if run.get('destinationSessionId', run['sessionId']) != session['id']: continue
            if run['phase'] not in {'submitting', 'accepted', 'running'}: continue
            detail = 'The runtime exited before a confirmed scheduled-run outcome. Inspect the original effects; no input was replayed.'
            value = self.store.transition(run['sessionId'], run['id'], ['submitting', 'accepted', 'running'], 'unknown', self.clock(), detail=detail)
            self.store.review(run['sessionId'], run['scheduleId'], detail, self.clock())
            self.decide_notification(session, value)
