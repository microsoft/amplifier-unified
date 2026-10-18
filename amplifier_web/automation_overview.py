"""Bounded schedule discovery and explicit human recovery, without owner inference."""
import copy
import json
from pathlib import Path

from amplifier_scheduling import next_after
from amplifier_scheduling.store import fingerprint


def overview(schedules, args):
    app, now = schedules.app, schedules.clock()
    limit, offset = min(100, args.get('limit', 30)), args.get('offset', 0)
    status = args.get('status', 'current')
    where = " WHERE json_extract(s.value,'$.status')!='cancelled'" if status == 'current' else ''
    total = schedules.store.db.execute('SELECT count(*) FROM schedules s' + where).fetchone()[0]
    rows = schedules.store.db.execute('''SELECT s.value, (SELECT value FROM schedule_runs
        WHERE schedule_id=s.id AND session_id=s.session_id ORDER BY due DESC LIMIT 1),
        EXISTS(SELECT 1 FROM schedule_runs WHERE schedule_id=s.id AND session_id=s.session_id AND phase='unknown')
        FROM schedules s''' + where + ''' ORDER BY
        CASE json_extract(s.value,'$.status') WHEN 'needs_review' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
        coalesce(json_extract(s.value,'$.nextDue'), 1e30), s.id LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
    records = [(json.loads(value), json.loads(run) if run else None, uncertain) for value, run, uncertain in rows]
    identities = {record['sessionId'] for record, _, _ in records}
    # Catalog summaries never hydrate or admit dormant chats into the live overlay.
    owners = {row['id']: row for row in app.directory.summary_ids(identities)}
    owners.update((row['id'], row) for row in app.state['sessions'] if row['id'] in identities)
    items = []
    for record, run, uncertain in records:
        owner = owners.get(record['sessionId'])
        phase = record['status']
        reason = record.get('reviewReason')
        if not owner or owner.get('_deleting'):
            phase, reason = 'owner_unavailable', 'The owning chat is unavailable.'
        elif phase == 'active':
            blocked = app.assignments.blocked(owner)
            if blocked: phase, reason = 'blocked', blocked
            elif owner.get('configurationBusy') or owner.get('fileAccessPending'):
                phase, reason = 'blocked', 'Waiting for the owning chat configuration change.'
            elif uncertain: phase, reason = 'needs_review', 'A prior run has an uncertain outcome.'
            elif owner.get('interruptionRevision', 0) != record.get('interruptionRevision', 0):
                phase, reason = 'owner_stopped', 'Stop changed after this schedule was reviewed.'
            elif record['destination'] != 'new_task' and owner.get('status') in {'stopped', 'stopping', 'interrupted', 'error'}:
                phase, reason = 'owner_' + owner['status'], 'Review future work in this chat before the next run.'
            elif record.get('nextDue') is not None and record['nextDue'] <= now: phase = 'due'
        items.append({**{key: copy.deepcopy(record.get(key)) for key in
            ('id', 'sessionId', 'revision', 'status', 'prompt', 'destination', 'spec', 'nextDue', 'reviewReason')},
            'displayState': phase, 'reason': reason,
            'owner': {key: copy.deepcopy(owner.get(key)) for key in ('id', 'title', 'workspace', 'status')} if owner and not owner.get('_deleting') else None,
            'lastRun': {key: copy.deepcopy(run.get(key)) for key in ('id', 'phase', 'dueAt', 'detail', 'destinationSessionId')} if run else None})
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
            'hasMore': offset + len(items) < total, 'readAt': now}


def repair_preview(schedules, args):
    app, sid = schedules.app, args['sessionId']
    owner = app._session(sid, hydrate=False)
    record = schedules.store.get(sid, args['id'])
    if record['revision'] != args['expectedRevision']:
        raise ValueError('The schedule changed. Refresh and review it again.')
    if record['destination'] != 'new_task' or record['status'] == 'cancelled':
        raise ValueError('Open the owning chat to review this task. Only existing new-task schedules can be recovered here.')
    if owner.get('configurationBusy') or owner.get('fileAccessPending') or owner.get('status') in {'working', 'running', 'busy', 'starting', 'stopping'}:
        raise ValueError('Wait for the owning chat and its configuration changes to finish.')
    if reason := app.assignments.blocked(owner): raise ValueError(reason)
    if not Path(owner.get('workingDirectory') or owner['workspace']).is_dir():
        raise ValueError('The owning workspace is unavailable. Restore its location before review.')
    if schedules.store.db.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND session_id=? AND phase='unknown' LIMIT 1", (record['id'], sid)).fetchone():
        raise ValueError('Reconcile the uncertain prior run in the owning chat before resuming future work.')
    config = schedules.configuration(record)
    reviewed = schedules.reviewed(sid, config, None)
    stop = owner.get('status') == 'stopped' or owner.get('interruptionRevision', 0) != record.get('interruptionRevision', 0)
    binding = {'scheduleId': record['id'], 'revision': record['revision'],
               'review': reviewed['binding'], 'requiresStopAcknowledgement': stop}
    return {**reviewed, 'previewHash': fingerprint(binding), 'requiresStopAcknowledgement': stop,
            'ownerStatus': owner.get('status'), 'scheduleRevision': record['revision']}


async def repair(schedules, action, args, origin, command_id):
    if origin != 'ui':
        raise ValueError('Schedule recovery requires a person to review and confirm in Automation. Peer repair authority is unchanged.')
    async with schedules.app.lock:
        if action == 'schedule.repairPreview': return repair_preview(schedules, args)
        def build(record):
            reviewed = repair_preview(schedules, args)
            if args['previewHash'] != reviewed['previewHash']:
                raise ValueError('The configuration or Stop intent changed. Review again.')
            if reviewed['requiresStopAcknowledgement'] and args.get('acknowledgeStop') is not True:
                raise ValueError('Explicitly allow future scheduled tasks after Stop before resuming.')
            due = next_after(record['spec'], schedules.clock())
            if due is None: raise ValueError('There is no future occurrence. Past work is not replayed.')
            record.update(reviewed['binding'], status='active', nextDue=due, reviewReason=None,
                          authorization={'origin': 'ui', 'at': schedules.clock(), 'recovery': 'reviewed_future_runs',
                                         'acknowledgedStop': reviewed['requiresStopAcknowledgement']},
                          updatedAt=schedules.clock())
            return record
        result = schedules.store.mutate(action, {**args, 'authorizationOrigin': origin}, command_id, build)
        schedules.changed()
        return result
