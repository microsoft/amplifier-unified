"""Narrow schedule-repair authority carried by a host-owned peer receipt.

This permits re-review/resume of exact existing schedules only. It never grants
new schedules, changed prompts/timing, arbitrary cross-chat writes or replay.
"""
from amplifier_scheduling.store import fingerprint


def human_messages(session):
    return [row for row in session.get('messages', [])
            if row.get('role') == 'user' and row.get('inputOrigin') in {'ui', 'user', 'voice'}]


def latest_human(session):
    rows = human_messages(session)
    return rows[-1] if rows else None


def prepare(app, source, target, value, lineage):
    human = latest_human(source)
    if (not human or human['id'] != value['sourceMessageId'] or human.get('questionId')
            or human.get('scheduledRunId')):
        raise ValueError('Schedule repair needs the latest actual human request in the sending chat.')
    if source['workspace'] != target['workspace'] or source['id'] == target['id']:
        raise ValueError('Schedule repair handoffs require two roots in the same workspace.')
    if human.get('inputId') not in lineage.get('sourceInputIds', []):
        raise ValueError('The human repair request must belong to the sending root’s current admitted input.')
    scopes = []
    for scope in value['schedules']:
        row = app.schedules.store.get(target['id'], scope['id'])
        if row['revision'] != scope['expectedRevision'] or row['status'] == 'cancelled':
            raise ValueError('The schedule changed or was cancelled; inspect it before handing off repair.')
        scopes.append({'id': row['id'], 'revision': row['revision'],
                       'configurationDigest': fingerprint(app.schedules.configuration(row))})
    if not scopes or len({row['id'] for row in scopes}) != len(scopes):
        raise ValueError('Choose distinct existing schedules for repair.')
    return {'sourceMessageId': human['id'], 'sourceInputId': human['inputId'],
            'textDigest': fingerprint(human.get('text', '')), 'sourceWorkspace': source['workspace'],
            'sourceInterruptionRevision': source.get('interruptionRevision', 0),
            'targetInterruptionRevision': target.get('interruptionRevision', 0),
            'targetHumanMessageId': (latest_human(target) or {}).get('id'),
            'schedules': scopes}


def authorize(app, sid, args, previous, action):
    if action != 'schedule.resume' or not previous:
        raise ValueError('A schedule repair handoff only permits resuming the exact existing schedule unchanged.')
    receipt = app.collaboration.receipt(args['handoffRequestId'])
    scope = receipt.get('scheduleRepair')
    if (receipt.get('commandAction') != 'coordination.send' or not receipt.get('accepted')
            or receipt.get('target', {}).get('sessionId') != sid or not scope):
        raise ValueError('Choose a host-verified schedule repair handoff addressed to this owner.')
    target = app._session(sid)
    binding, active = app.collaboration.active_binding(target)
    inputs = {row.get('inputId') for row in binding.get('_inputBindings', [])}
    if receipt.get('inputId') not in inputs.intersection(active.get('inputIds', [])):
        raise ValueError('The repair handoff is not an admitted input in this owner’s current generation.')
    source = app._session(receipt['senderSessionId'])
    human = latest_human(source)
    if (source['workspace'] != target['workspace'] or source['workspace'] != scope['sourceWorkspace']
            or source.get('interruptionRevision', 0) != scope['sourceInterruptionRevision']
            or target.get('interruptionRevision', 0) != scope['targetInterruptionRevision']
            or (latest_human(target) or {}).get('id') != scope['targetHumanMessageId']
            or not human or human['id'] != scope['sourceMessageId']
            or args.get('sourceMessageId') != human['id']
            or human.get('questionId') or human.get('scheduledRunId')
            or fingerprint(human.get('text', '')) != scope['textDigest']):
        raise ValueError('The human request, workspace or stop intent changed; obtain a fresh repair handoff.')
    selected = next((row for row in scope['schedules'] if row['id'] == previous['id']), None)
    if (not selected or selected['revision'] != previous['revision']
            or selected['configurationDigest'] != fingerprint(app.schedules.configuration(previous))):
        raise ValueError('This schedule or revision was not covered by the repair handoff.')
    return source, human, {'requestId': receipt['requestId'], 'senderSessionId': source['id'],
                           'sourceInterruptionRevision': scope['sourceInterruptionRevision'],
                           'scheduleId': previous['id'], 'scheduleRevision': previous['revision']}
