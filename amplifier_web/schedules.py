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
    authorization = {'previewHash': string(100), 'sourceMessageId': string(200)}
    return {
        'schedule.list': ('List saved schedules without starting work.', schema(sid)),
        'schedule.read': ('Read a schedule and its bounded durable run history. Unknown is never replayed.', schema(identity)),
        'schedule.preview': ('Preview concrete timezone occurrences for an explicit user-requested schedule and the current task. Does not save or submit work.', schema({**sid, **fields})),
        'schedule.create': ('Save and activate exactly the reviewed prompt/timing for an explicit user request. Agents must cite the actual user sourceMessageId. A pending question never authorizes a schedule. Tool permissions still apply at execution.', schema({**sid, **revision, **fields, **authorization}, [*sid, *revision, *fields, 'previewHash'])),
        'schedule.update': ('Review and replace the saved prompt/timing against the latest task correction. Requires exact revision, concrete preview and renewed explicit user provenance.', schema({**identity, **revision, **fields, **authorization}, [*identity, *revision, *fields, 'previewHash'])),
        'schedule.pause': ('Pause future due runs. Current admitted work can finish; no rollback is claimed.', schema({**identity, **revision})),
        'schedule.resume': ('Deliberately resume after concrete review. Requires current preview and explicit user provenance; does not replay past uncertain runs.', schema({**identity, **revision, **authorization}, [*identity, *revision, 'previewHash'])),
        'schedule.cancel': ('Cancel future due runs while preserving run evidence. Does not undo admitted work.', schema({**identity, **revision})),
        'schedule.report': ('Record attributable monitor evidence for this scheduled run. Compared values are retained; agent-reported unchanged is labelled as a claim, not proof. This does not complete the task or grant permission.', schema({**sid, 'runId': string(200), **revision, 'outcome': {'enum': ['changed', 'unchanged']}, 'detail': string(4000), 'values': {}}, [*sid, 'runId', *revision, 'outcome', 'detail'])),
        'schedule.reconcile': ('Explicitly reconcile an uncertain run as completed or abandoned with user-provided evidence. Never resubmits the old input. Schedule resume remains separate.', schema({**sid, 'runId': string(200), **revision, 'resolution': {'enum': ['completed', 'abandoned']}, 'evidence': {**string(4000), 'minLength': 1}, 'sourceMessageId': string(200)}, [*sid, 'runId', *revision, 'resolution', 'evidence'])),
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
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception as exc:
                async with self.app.lock:
                    self.app.state['scheduleError'] = str(exc)[:1000]
                    self.app._publish()
            await asyncio.sleep(2)

    def sync(self):
        for session in self.app.state['sessions']:
            session['schedules'] = [{**record, 'runs': self.store.runs(session['id'], record['id'])[:10]} for record in self.store.list(session['id'])]

    def changed(self):
        self.sync()
        operations = getattr(self.app, 'operations', None)
        if operations and hasattr(operations, 'notify'): operations.notify()
        self.app._publish()

    def operation_records(self, sid): return [self.operation_shape(row) for row in self.store.runs(sid)]
    def operation_record(self, sid, identity):
        try: return self.operation_shape(self.store.run(sid, identity))
        except ValueError: return None
    @staticmethod
    def operation_shape(row):
        return {'id': row['id'], 'sessionId': row['sessionId'], 'source': 'schedule', 'kind': 'scheduled-run', 'state': {'claimed':'queued','submitting':'queued','accepted':'queued','unknown':'outcome_unknown','skipped':'cancelled','abandoned':'cancelled'}.get(row['phase'], row['phase']), 'revision': row['revision'], 'controlAvailable': False, 'evidence': copy.deepcopy(row), 'createdAt': row['createdAt'], 'updatedAt': row['updatedAt']}

    async def task(self, sid):
        if not self.app.management or not self.app.runtime: raise ValueError('The runtime is unavailable')
        await self.app.history.ensure_loaded(sid)
        await self.app.management.ensure_runtime(self.app._session(sid))
        value = await self.app.runtime.control(sid, 'task.get', {})
        if not value.get('task'): raise ValueError('Save an explicit task before scheduling its continuation')
        return value['task']

    def configuration(self, args):
        return {key: copy.deepcopy(args[key]) for key in ('prompt', 'kind', 'missedRunPolicy', 'notificationPolicy')} | {'spec': normalize(args['spec'])}

    def reviewed(self, sid, config, task):
        binding = {'sessionId': sid, 'taskId': task['id'], 'taskRevision': task['revision'], 'interruptionRevision': self.app._session(sid).get('interruptionRevision', 0), **config}
        return {'previewHash': fingerprint(binding), 'binding': binding, 'occurrences': preview(config['spec'], self.clock(), 5), 'daylightSaving': 'Nonexistent local times are skipped; repeated local times run once at the first occurrence.', 'missedRunPolicy': config['missedRunPolicy'], 'notificationPolicy': config['notificationPolicy']}

    def authorization(self, sid, args, origin, *, previous=None):
        if origin == 'ui': return {'origin': 'ui', 'at': self.clock()}
        if origin not in {'agent', 'voice', 'user'}: raise ValueError('Only an explicit user request can authorize scheduled work')
        source = next((row for row in self.app._session(sid)['messages'] if row['id'] == args.get('sourceMessageId') and row.get('role') == 'user'), None)
        if not source or source.get('inputOrigin') not in {'ui', 'user', 'voice'} or source.get('questionId') or source.get('scheduledRunId'):
            raise ValueError('Cite the actual user message requesting this schedule; an agent message or question answer is not schedule authorization')
        if previous and previous.get('status') == 'needs_review' and (previous.get('authorization', {}).get('messageId') == source['id'] or source.get('createdAt', 0) <= previous.get('authorization', {}).get('at', 0)):
            raise ValueError('Cite the new user request or correction that authorizes this schedule review')
        return {'origin': origin, 'messageId': source['id'], 'text': source.get('text', ''), 'inputOrigin': source['inputOrigin'], 'messageCreatedAt': source.get('createdAt', 0), 'textDigest': fingerprint(source.get('text', '')), 'voiceId': source.get('voiceId'), 'voiceItemId': source.get('voiceItemId'), 'at': self.clock()}

    async def dispatch(self, action, args, origin, command_id):
        sid = args['sessionId']
        self.app._session(sid)
        if action in {'schedule.list', 'schedule.read'}:
            async with self.app.lock:
                result = {'items': self.store.list(sid)} if action == 'schedule.list' else {'schedule': self.store.get(sid, args['id']), 'runs': self.store.runs(sid, args['id'])}
                self.sync()
                self.app._publish()
                return result
        if action in {'schedule.report', 'schedule.reconcile'}: return await self.run_command(action, args, origin, command_id or str(uuid.uuid4()))
        config = self.configuration(args) if action in {'schedule.preview', 'schedule.create', 'schedule.update'} else None
        task = await self.task(sid) if action in {'schedule.preview', 'schedule.create', 'schedule.update', 'schedule.resume'} else None
        if action == 'schedule.preview': return self.reviewed(sid, config, task)
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
                    if task['status'] != 'active': raise ValueError('Resume the saved task explicitly before activating its schedule')
                    effective = config or self.configuration(record)
                    reviewed = self.reviewed(sid, effective, task)
                    if args['previewHash'] != reviewed['previewHash']: raise ValueError('The task, stop intent, or schedule preview changed; preview again')
                    if any(run['phase'] == 'unknown' for run in self.store.runs(sid, record['id'])): raise ValueError('Reconcile the uncertain prior run before resuming')
                    authorization = self.authorization(sid, args, origin, previous=previous)
                    next_due = next_after(effective['spec'], now)
                    if next_due is None: raise ValueError('Choose a future occurrence; past one-shot work is not replayed')
                    record.update(reviewed['binding'], status='active', nextDue=next_due, authorization=authorization, reviewReason=None)
                elif action == 'schedule.pause': record['status'] = 'paused'
                elif action == 'schedule.cancel': record['status'] = 'cancelled'
                else: raise ValueError('Unknown schedule action')
                record['updatedAt'] = now
                return record
            result = self.store.mutate(action, {**args, "authorizationOrigin": origin}, command_id or str(uuid.uuid4()), build)
            self.changed()
            return result

    async def run_command(self, action, args, origin, command_id):
        sid, identity, now = args['sessionId'], args['runId'], self.clock()
        async with self.app.lock:
            def edit(run):
                if action == 'schedule.report':
                    if run['phase'] not in {'submitting', 'accepted', 'running'}: raise ValueError('Only an admitted current run can report monitor evidence')
                    values = args.get('values')
                    if len(json.dumps(values)) > 64000: raise ValueError('Keep compared monitor values below 64 KB')
                    report = {'outcome': args['outcome'], 'detail': args['detail'], 'source': 'agent_report' if origin == 'agent' else 'user_report', 'at': now}
                    if 'values' in args: report.update(values=copy.deepcopy(values), valuesDigest=fingerprint(values))
                    run['report'] = report
                else:
                    if run['phase'] != 'unknown': raise ValueError('Only an uncertain run requires reconciliation')
                    provenance = self.authorization(sid, args, origin)
                    if origin != 'ui' and provenance.get('messageCreatedAt', 0) < run['createdAt']:
                        raise ValueError('Reconciliation needs the user’s actual finding after this run, not an older request')
                    run.update(phase=args['resolution'], reconciliation={'evidence': args['evidence'], 'provenance': provenance, 'at': now}, detail='Explicitly reconciled without replaying its input.')
                run['updatedAt'] = now
            result = self.store.run_command(sid, identity, args['expectedRevision'], command_id, fingerprint([action, args, origin]), edit)
            self.changed()
            return result

    def dependency_reason(self, session, task, schedule):
        if session.get('interruptionRevision', 0) != schedule['interruptionRevision']:
            return 'The user stopped this conversation. Review the schedule before resuming.'
        authorization = schedule.get('authorization', {})
        if authorization.get('messageId'):
            source = next((row for row in session['messages'] if row['id'] == authorization['messageId']), None)
            if source is not None and fingerprint(source.get('text', '')) != authorization.get('textDigest'):
                return 'The original schedule request was edited. Review the saved authorization.'
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
        if work_paused(self.app.state): return
        for schedule in self.store.list():
            if schedule['status'] != 'active' or schedule.get('nextDue') is None or schedule['nextDue'] > now: continue
            sid = schedule['sessionId']
            session = self.app._session(sid)
            if session.get('status') in {'working', 'starting', 'running', 'busy', 'stopping'}: continue
            try:
                task = await self.task(sid)
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
                # Refresh authoritative schedule after asynchronous runtime reads.
                current = self.store.get(sid, schedule['id'])
                if current['revision'] != schedule['revision'] or current['status'] != 'active': continue
                reason = self.dependency_reason(self.app._session(sid), task, current)
                if reason:
                    self.store.review(sid, current['id'], reason, self.clock()); self.changed(); continue
                run = self.store.claim(sid, current['id'], self.clock())
                if run is None: continue
                if run['phase'] == 'skipped': self.changed(); continue
                # This is the durable handoff boundary. A crash from this point
                # leaves an uncertain input; retries never send it again.
                run = self.store.transition(sid, run['id'], ['claimed'], 'submitting', self.clock(), interruptionRevision=current['interruptionRevision'])
                text = current['prompt']
                if current['kind'] == 'monitor':
                    text += '\n\nScheduled monitor run ' + run['id'] + '. Inspect schedule.read for prior compared values, then record this run with schedule.report and actual values when available. Label evidence accurately; an unchanged claim is not proof. Do not complete the overall task merely because this run ends.'
                self.app._message(session, 'user', text, 'schedule', inputId=run['inputId'], inputOrigin='scheduler', scheduledRunId=run['id'], delivery={'status': 'sending'})
                from .execution import ensure_turn
                ensure_turn(session, run['inputId'], text)
                session['status'] = 'working'
                self.changed()
            try:
                # Exact task revision and idle admission are rechecked inside the
                # worker command lock, immediately before its nonblocking queue.
                def guard():
                    latest = self.store.get(sid, current['id'])
                    if not self.store.owns(self.clock()): return 'Scheduler ownership changed before admission.'
                    if latest['status'] != 'active' or latest['revision'] != current['revision']: return 'The schedule changed before admission.'
                    if self.app._session(sid).get('interruptionRevision', 0) != current['interruptionRevision']: return 'The user stop won input admission; this occurrence was skipped.'
                    return None
                result = await self.app.runtime.scheduled_input(sid, {'taskId': current['taskId'], 'taskRevision': current['taskRevision'], 'inputId': run['inputId'], 'text': text, 'kind': current['kind'], 'interruptionRevision': current['interruptionRevision']}, guard)
                phase = 'accepted' if result.get('accepted') else 'skipped'
                detail = 'Input admitted to the existing task.' if result.get('accepted') else result.get('reason', 'Input was not admitted.')
            except BaseException as exc:
                phase, detail = 'unknown', 'Input handoff outcome is uncertain. No automatic replay: ' + str(exc)[:500]
                cancelled = isinstance(exc, asyncio.CancelledError)
            else: cancelled = False
            async with self.app.lock:
                saved = self.store.transition(sid, run['id'], ['submitting'], phase, self.clock(), detail=detail, observedInterruptionRevision=self.app._session(sid).get('interruptionRevision', 0))
                self.app._delivery(self.app._session(sid), run['inputId'], 'accepted' if phase == 'accepted' else 'unknown' if phase == 'unknown' else 'rejected')
                if saved['phase'] in {'unknown', 'skipped'}:
                    self.store.review(sid, current['id'], detail, self.clock())
                    self.app._session(sid)['status'] = 'interrupted' if phase == 'unknown' else 'idle'
                    if saved['phase'] == 'unknown': self.decide_notification(self.app._session(sid), saved)
                self.changed()
            if cancelled: raise asyncio.CancelledError

    def generation(self, session, payload):
        """Observe exact scheduled inputs; never equate run end with task completion."""
        ids = payload.get('input_ids') or ([payload['initial_input_id']] if payload.get('initial_input_id') else [])
        scheduled = [identity for identity in ids if str(identity).startswith('schedule:')]
        monitor = payload.get('scheduled_monitor_input_id')
        if monitor and monitor not in scheduled: scheduled.append(monitor)
        for identity in scheduled:
            try: run = self.store.run(session['id'], identity)
            except ValueError: continue
            event = payload.get('event')
            if event == 'generation.started':
                self.store.transition(session['id'], identity, ['submitting', 'accepted'], 'running', self.clock(), generationId=payload.get('generation_id'))
            elif event in {'generation.finished', 'generation.failed', 'generation.detached'}:
                if monitor and event == 'generation.finished':
                    self.store.transition(session['id'], identity, ['submitting', 'accepted', 'running'], 'running', self.clock(), monitorScope=True, parentFinished=event, pendingJobs=sorted(set(run.get('pendingJobs', []) + payload.get('active_job_ids', []))), detail=payload.get('text', 'Waiting for the monitor to finish.'))
                    continue
                if payload.get('active_job_ids'):
                    self.store.transition(session['id'], identity, ['submitting', 'accepted', 'running'], 'running', self.clock(), pendingJobs=payload['active_job_ids'], jobOutcomes={}, parentFinished=event, detail=payload.get('text', 'Waiting for delegated work.'))
                    continue
                phase = 'completed' if event == 'generation.finished' else 'unknown' if event == 'generation.detached' else 'failed'
                run = self.store.transition(session['id'], identity, ['submitting', 'accepted', 'running'], phase, self.clock(), generationId=payload.get('generation_id'), detail=payload.get('text') or payload.get('error') or event)
                self.decide_notification(session, run)
        return bool(payload.get('scheduled_monitor_only')) if monitor else bool(scheduled) and len(scheduled) == len(ids)

    def decide_notification(self, session, run):
        with self.store.transaction():
            run = self.store.run(session['id'], run['id'])
            if run.get('notificationDecision') is not None: return
            schedule = self.store.get(session['id'], run['scheduleId'])
            policy = schedule['notificationPolicy']
            report = run.get('report', {})
            changed, source = None, 'unreported'
            if 'valuesDigest' in report:
                previous = next((other for other in self.store.runs(session['id'], run['scheduleId']) if other['id'] != run['id'] and other['phase'] == 'completed' and 'valuesDigest' in other.get('report', {})), None)
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
        for run in self.store.runs(session['id']):
            if run['phase'] != 'running' or payload.get('id') not in run.get('pendingJobs', []): continue
            status = payload.get('status')
            if status not in {'completed', 'error', 'cancelled', 'interrupted'}: continue
            outcomes = {**run.get('jobOutcomes', {}), payload['id']: status}
            if run.get('monitorScope') or len(outcomes) < len(run['pendingJobs']):
                self.store.transition(session['id'], run['id'], ['running'], 'running', self.clock(), jobOutcomes=outcomes)
                continue
            phase = 'unknown' if 'interrupted' in outcomes.values() else 'failed' if any(value != 'completed' for value in outcomes.values()) else 'completed'
            result = self.store.transition(session['id'], run['id'], ['running'], phase, self.clock(), jobOutcomes=outcomes)
            self.decide_notification(session, result)


    def idle(self, session):
        for run in self.store.runs(session['id']):
            if run['phase'] != 'running' or not run.get('monitorScope') or not run.get('parentFinished'): continue
            outcomes = run.get('jobOutcomes', {})
            if len(outcomes) < len(run.get('pendingJobs', [])): continue
            phase = 'unknown' if 'interrupted' in outcomes.values() else 'failed' if any(value != 'completed' for value in outcomes.values()) else 'completed'
            result = self.store.transition(session['id'], run['id'], ['running'], phase, self.clock())
            self.decide_notification(session, result)


    def runtime_ended(self, session):
        for run in self.store.runs(session['id']):
            if run['phase'] not in {'submitting', 'accepted', 'running'}: continue
            detail = 'The runtime exited before a confirmed scheduled-run outcome. Inspect the original effects; no input was replayed.'
            value = self.store.transition(session['id'], run['id'], ['submitting', 'accepted', 'running'], 'unknown', self.clock(), detail=detail)
            self.store.review(session['id'], run['scheduleId'], detail, self.clock())
            self.decide_notification(session, value)
