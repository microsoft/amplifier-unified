"""Bounded execution links and attributed evidence; no execution or claim owner."""
from __future__ import annotations

import copy
import time


def definitions(schema, string):
    target = {'workspaceId': string(100), 'sessionId': string(200), 'id': string(200),
              'expectedRevision': {'type': 'integer', 'minimum': 1}}
    required = ['workspaceId', 'sessionId', 'id', 'expectedRevision']
    candidate = {**string(100), 'minLength': 1}
    return {
        'workspace.work.link': ('Owner links an existing same-workspace execution or reviewer without enrolling, starting or granting it authority. Links are not claims.', schema({**target,
            'linkedSessionId': string(200), 'role': {'enum': ['executor', 'reviewer']},
            'candidate': candidate}, [*required, 'linkedSessionId', 'role', 'candidate'])),
        'workspace.work.report': ('Append an attributed result for an exact promise/candidate. A report alone never means independent verification. Optional checkReceiptId must be this actor\'s exact retained integration collection.', schema({**target,
            'promiseRevision': {'type': 'integer', 'minimum': 1}, 'candidate': candidate,
            'outcome': {'enum': ['passed', 'failed', 'unknown', 'not_run']},
            'report': {**string(4000), 'minLength': 1},
            'criteria': {'type': 'array', 'maxItems': 32, 'uniqueItems': True, 'items': string(100)},
            'checkReceiptId': string(200),
            'references': {'type': 'array', 'maxItems': 16, 'items': string(2000)}},
            [*required, 'promiseRevision', 'candidate', 'outcome', 'report', 'criteria'])),
        'workspace.work.decision': ('Authenticated human records acceptance or rejection of an exact promise/candidate. Agents cannot supply human decisions. Does not grant tool permissions.', schema({**target,
            'promiseRevision': {'type': 'integer', 'minimum': 1}, 'candidate': candidate,
            'decision': {'enum': ['accepted', 'rejected']}}, [*required, 'promiseRevision', 'candidate', 'decision'])),
        'workspace.work.disposition': ('Owner explicitly closes or reopens a promise, retaining all prior dispositions and unresolved effects. Does not stop, resume or replay execution.', schema({**target,
            'disposition': {'enum': ['open', 'closed']},
            'basis': {'enum': ['satisfied', 'cancelled', 'superseded', 'reopened']},
            'reason': {**string(2000), 'minLength': 1}}, [*required, 'disposition', 'basis', 'reason'])),
    }


def _link_current(work, link, workspace):
    # A declaration made before Stop cannot authorize a later report.
    try:
        row = work.service._session(link['sessionId'], hydrate=False)
    except Exception:
        return False
    if row.get('workspace') != workspace['path'] or row.get('interruptionRevision', 0) != link['interruptionRevision']:
        return False
    return not work.service.assignments.blocked(session=row)


def _qualification(work, actor, item, args):
    identity = args.get('checkReceiptId')
    if not identity:
        return 'participant_report'
    receipt = work.receipts.receipt(identity)
    collection = receipt.get('result', {})
    if receipt.get('commandAction') != 'integration.collect' or collection.get('sessionId') != actor['id']:
        work.error('Use this verifier root\'s exact retained integration collection.', 403)
    repositories = collection.get('repositories', [])
    matches = [r for r in repositories if r.get('resultingSource', {}).get('head') == args['candidate']]
    if len(matches) != 1:
        work.error('The check receipt does not bind this exact candidate.')
    row = matches[0]
    observed = row.get('status')
    if args['outcome'] == 'passed' and (
            collection.get('status') != 'passed' or collection.get('candidateStatus') != 'matched'
            or observed != 'passed' or row.get('candidateStatus') != 'matched'
            or row.get('operation', {}).get('state') != 'completed'
            or row.get('operation', {}).get('returncode') != 0
            or not row.get('artifacts') or any(a.get('status') != 'retained' for a in row['artifacts'])):
        work.error('Passing verification requires exact retained passed checks and artifacts; missing/unknown is not success.')
    if args['outcome'] != 'passed' and observed == 'passed':
        work.error('The reported outcome conflicts with its check receipt.')
    executors = {link['sessionId'] for link in item.get('links', [])
                 if link['candidate'] == args['candidate'] and link['role'] == 'executor'}
    if not executors:
        work.error('Record the candidate execution responsibility before qualifying its check.')
    return 'owner_check' if actor['id'] in executors else 'independent_check'


def mutate(work, verb, item, args, actor, origin, workspace):
    """Return immutable event fields. Caller owns the enclosing atomic transaction."""
    if item['revision'] != args['expectedRevision']:
        work.error('Work revision changed; inspect current state before changing it.')
    owner = item['ownerSessionId'] == actor['id']
    event_promise = item['promiseRevision']
    extra = {}
    if verb in {'link', 'disposition'} and not owner:
        work.error('Only the accountable Work owner can change responsibility or disposition.', 403)
    if verb == 'link':
        if item['disposition'] != 'open':
            work.error('Reopen closed Work explicitly before linking execution.')
        row = work.service._session(args['linkedSessionId'], hydrate=False)
        if row.get('workspace') != workspace['path'] or row.get('sessionKind', 'root') != 'root':
            work.error('Link an existing root in this workspace.', 403)
        if row.get('status') in {'stopping', 'stopped'}:
            work.error('Stopped work cannot gain a new execution link.', 403)
        links = item.setdefault('links', [])
        identity = (row['id'], args['candidate'], args['role'])
        prior = next((l for l in links if (l['sessionId'], l['candidate'], l['role']) == identity), None)
        if prior:
            work.error('This exact relationship is already retained; inspect it before changing responsibility.')
        if len(links) >= 32 or args['role'] == 'executor' and sum(l['role'] == 'executor' for l in links) >= 8:
            work.error('The bounded execution-link capacity is reached; no links were discarded.', 429)
        link = {'sessionId': row['id'], 'candidate': args['candidate'], 'role': args['role'],
                'promiseRevision': item['promiseRevision'], 'interruptionRevision': row.get('interruptionRevision', 0),
                'recordedBy': actor['id'], 'recordedAt': time.time()}
        links.append(link)
        if args['role'] == 'executor' and item.get('candidate') != args['candidate']:
            item.update(candidate=args['candidate'], verification='not_verified', criterionEvidence={},
                        humanDecision='not_recorded')
        extra['link'] = copy.deepcopy(link)
    elif verb == 'report':
        event_promise = args['promiseRevision']
        if event_promise > item['promiseRevision']:
            work.error('The report names an unknown future promise.')
        links = [l for l in item.get('links', []) if l['sessionId'] == actor['id']
                 and l['candidate'] == args['candidate'] and l['promiseRevision'] == event_promise]
        if not owner and not any(_link_current(work, l, workspace) for l in links):
            work.error('A report needs its exact current linked execution/reviewer responsibility.', 403)
        if event_promise == item['promiseRevision'] and not set(args['criteria']) <= {c['id'] for c in item['criteria']}:
            work.error('Report criteria must belong to this exact promise.', 400)
        qualification = _qualification(work, actor, item, args)
        extra['evidence'] = {key: copy.deepcopy(args[key]) for key in
            ('candidate', 'outcome', 'report', 'criteria', 'checkReceiptId', 'references') if key in args}
        extra['evidence'].update(qualification=qualification, reportedBy=actor['id'],
                                source='host-bound collection' if args.get('checkReceiptId') else 'agent report')
        # Historical attempts stay in immutable receipts, never current verdicts.
        if event_promise == item['promiseRevision']:
            item['execution'] = 'reported'
            if not item.get('candidate'):
                item['candidate'] = args['candidate']
            if item['candidate'] == args['candidate']:
                item['latestOutcome'] = args['outcome']
                if args['outcome'] in {'failed', 'unknown', 'not_run'}:
                    item['verification'] = 'not_verified'
                    item['criterionEvidence'] = {}
                elif qualification != 'participant_report':
                    checks = item.setdefault('criterionEvidence', {})
                    for criterion in args['criteria']:
                        checks[criterion] = {'candidate': args['candidate'], 'qualification': qualification,
                                             'checkReceiptId': args['checkReceiptId'], 'reviewer': actor['id']}
                    required = {c['id'] for c in item['criteria']}
                    item['verification'] = ('independent_checks_passed' if required and all(
                        checks.get(c, {}).get('qualification') == 'independent_check' for c in required)
                        else 'owner_checks_recorded')
    elif verb == 'decision':
        if origin not in {'ui', 'user'}:
            work.error('Only an authenticated human action records a human decision.', 403)
        if args['promiseRevision'] != item['promiseRevision'] or args['candidate'] != item.get('candidate'):
            work.error('Human decision must name the current exact promise and candidate.')
        item['humanDecision'] = args['decision']
        extra['decision'] = {key: args[key] for key in ('decision', 'candidate', 'promiseRevision')}
    elif verb == 'disposition':
        if not args['reason'].strip():
            work.error('A disposition needs a reason.', 400)
        if args['disposition'] == item['disposition']:
            work.error('The Work item already has that disposition.')
        if args['disposition'] == 'closed':
            if args['basis'] == 'reopened':
                work.error('Closing needs a closure basis.', 400)
            if args['basis'] == 'satisfied' and item['verification'] != 'independent_checks_passed' and item['humanDecision'] != 'accepted':
                work.error('Satisfied closure requires qualified criteria or an actual human acceptance decision.')
        elif args['basis'] != 'reopened':
            work.error('Opening a closed item needs an explicit corrective reopening.', 400)
        extra['disposition'] = {'previous': item['disposition'], 'current': args['disposition'],
                                'basis': args['basis'], 'reason': args['reason']}
        item['disposition'] = args['disposition']
        item['dispositionReason'] = args['reason']
        if args['disposition'] == 'open':
            item.update(verification='not_verified', criterionEvidence={}, humanDecision='not_recorded')
    else:
        work.error('Unsupported Work mutation.', 400)
    item.update(revision=item['revision'] + 1, eventCount=item['eventCount'] + 1, updatedAt=time.time())
    return extra, event_promise
