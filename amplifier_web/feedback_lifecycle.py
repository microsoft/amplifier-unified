"""Read-only delivery reconciliation and versioned, non-destructive corrections."""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlencode

from . import feedback


def revision(issue):
    return hashlib.sha256(json.dumps({key: issue.get(key) for key in
        ('id', 'number', 'html_url', 'title', 'body', 'state', 'updated_at')}, sort_keys=True).encode()).hexdigest()


def owned(issue, user, identity, url=None):
    actual = issue.get('html_url', '')
    return (type(user.get('id')) is int and issue.get('user', {}).get('id') == user['id']
            and type(issue.get('number')) is int and 'pull_request' not in issue
            and any(actual == f'https://github.com/{repo}/issues/{issue["number"]}' for repo in feedback.RECEIPT_REPOSITORIES)
            and (url is None or actual == url)
            and f'<!-- amplifier-feedback:{identity} -->' in (issue.get('body') or ''))


async def reconcile(followups, identity, args):
    """A missing search hit never authorizes a second remote write."""
    user = await feedback.github_api('user', None)
    login = user.get('login')
    if type(user.get('id')) is not int or not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9-]+', login):
        raise ValueError('GitHub user could not be verified')
    matches = {}
    checked = 0
    complete = True
    for repository in feedback.RECEIPT_REPOSITORIES:
        query = f'repo:{repository} is:issue author:{login} in:body "amplifier-feedback:{args["feedbackId"]}"'
        # Bound the read. Search absence is not authoritative (indexing/access
        # may lag); only a unique, freshly verified marker can bind a receipt.
        page = await feedback.github_api('search/issues?' + urlencode({'q': query, 'per_page': 100}), None)
        if not isinstance(page, dict) or not isinstance(page.get('items'), list):
            raise ValueError('Invalid search response')
        complete = complete and page.get('incomplete_results') is False and type(page.get('total_count')) is int and page['total_count'] <= 100
        for item in page['items'][:100]:
            checked += 1
            if not owned(item, user, args['feedbackId']):
                continue
            url = item['html_url']
            issue = await feedback.github_api('repos/' + url.removeprefix('https://github.com/'), None)
            if owned(issue, user, args['feedbackId'], url):
                matches[url] = issue
    evidence = {'checkedCandidates': checked, 'repositories': list(feedback.RECEIPT_REPOSITORIES),
                'searchComplete': complete, 'matchingIssues': len(matches)}
    if len(matches) == 1 and complete:
        issue = next(iter(matches.values()))
        # Both receipts change in one service transaction; no conversation runs.
        async with followups.service.lock:
            raw = followups.service.db.execute('SELECT receipt FROM feedback_requests WHERE id=?', (args['feedbackId'],)).fetchone()
            original = json.loads(raw[0])
            if original.get('status') != 'unknown':
                raise ValueError('Submission changed during reconciliation')
            original.update(status='submitted', url=issue['html_url'], reconciliationRequestId=identity, updatedAt=time.time(),
                            message='Delivery confirmed by the original feedback marker and current GitHub owner.')
            followups.service.db.execute('UPDATE feedback_requests SET receipt=? WHERE id=?', (json.dumps(original), args['feedbackId']))
            row = followups.service.db.execute('SELECT receipt FROM feedback_followups WHERE id=?', (identity,)).fetchone()
            receipt = json.loads(row[0])
            receipt.update(status='completed', outcome='found', url=issue['html_url'], evidence=evidence, updatedAt=time.time(),
                           message='Original feedback found. Reads and follow-ups are now available; nothing was posted.')
            followups.service.db.execute('UPDATE feedback_followups SET receipt=? WHERE id=?', (json.dumps(receipt), identity))
            followups.owner.refresh(args['feedbackId'])
            followups.service._publish()
    else:
        outcome = 'not_found' if complete and not matches else 'indeterminate'
        await followups.update(identity, status='completed', outcome=outcome, evidence=evidence,
                               message=('No matching feedback was found in this search. Delivery remains unknown; nothing was resent.' if outcome == 'not_found'
                                        else 'Delivery could not be resolved uniquely. The original receipt remains unknown; nothing was resent.'))


async def correction(followups, identity, args, issue, actor):
    """GitHub has no issue PATCH CAS. Preserve originals with a new version."""
    if revision(issue) != args['expectedRevision']:
        await followups.update(identity, status='failed', code='feedback_revision_conflict',
                               message='The report changed. Refresh it and review your correction before sending; nothing was posted.')
        return None
    await followups.update(identity, audit={'sourceRevision': revision(issue), 'sourceUpdatedAt': issue.get('updated_at'),
                                           'priorTitle': issue.get('title'), 'priorBody': issue.get('body'),
                                           'title': args['title'], 'body': args['body'], 'author': actor})
    return ('## Feedback correction\n\n### Updated title\n' + args['title'] + '\n\n### Updated description\n' + args['body']
            + '\n\nOriginal report retained. This is a correction version, not an overwrite.'
            + f'\n\n<!-- amplifier-feedback-correction:{identity} source:{args["expectedRevision"]} -->')
