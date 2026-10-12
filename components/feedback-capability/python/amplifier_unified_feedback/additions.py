"""Reviewed immutable file additions, using the existing command journal.

The accepted review freezes ownership, destination and bytes. Each remote write
is journaled before dispatch. Reconciliation only reads; it never resumes a phase.
"""
import base64
import hashlib
import json
import re
from urllib.parse import quote

from . import uploads


async def destination(github, repository):
    row = await github('repos/' + repository, None)
    if (type(row.get('id')) is not int or row['id'] <= 0
            or row.get('full_name') != repository or type(row.get('private')) is not bool):
        raise ValueError('The repository identity and visibility could not be verified')
    return {'repository': repository, 'repositoryId': row['id'],
            'visibility': 'private' if row['private'] else 'public'}


async def target(owner, feedback_id):
    from .owner import RECEIPT_REPOSITORIES, owned
    original = owner.receipt(feedback_id)
    url = original.get('url', '')
    if (original.get('operation') != 'feedback.submit' or original.get('status') != 'completed'
            or not any(re.fullmatch(re.escape('https://github.com/' + repo + '/issues/') + r'[1-9][0-9]*', url) for repo in RECEIPT_REPOSITORIES)):
        raise ValueError('Choose a successfully submitted report')
    repository = url.removeprefix('https://github.com/').split('/issues/')[0]
    user = await owner.github('user', None)
    issue = await owner.github('repos/' + url.removeprefix('https://github.com/'), None)
    if (not owned(issue, user, feedback_id, url) or type(issue.get('id')) is not int
            or issue['id'] <= 0 or type(user.get('id')) is not int or user['id'] <= 0):
        raise ValueError('The original report and signed-in account could not be verified')
    return {'url': url, 'issueId': issue['id'], 'issueNumber': issue['number'],
            'accountId': user['id'], **await destination(owner.github, repository)}


def selected(owner, identities):
    rows = []
    for identity in identities:
        saved = owner.db.execute('SELECT metadata FROM attachments WHERE id=?', (identity,)).fetchone()
        if not saved:
            raise ValueError('A selected feedback file is unavailable')
        row = json.loads(saved[0])
        if row.get('excerpt'):
            raise ValueError('Choose ordinary files. Conversation excerpts require their separate disclosure review.')
        rows.append((row, uploads.read_verified(owner.root, row)))
    if not 1 <= len(rows) <= 8 or sum(row['size'] for row, _ in rows) > uploads.MAX_TOTAL_BYTES:
        raise ValueError('Choose one to eight files totaling at most 24 MiB')
    return rows


def manifest(rows):
    return [{key: row[key] for key in ('id', 'name', 'mime', 'size', 'sha256')} for row, _ in rows]


def disclosure(review):
    return ('Public: anyone can read these files. ' if review['visibility'] == 'public'
            else 'Private: repository access is required. ') + 'Files remain in repository history. Removing local files does not retract uploaded copies.'


async def perform(owner, operation, args, effect):
    if operation == 'feedback.attachments.reconcile':
        return await reconcile(owner, args)
    if operation == 'feedback.attachments.review':
        rows = selected(owner, args['attachmentIds'])
        review = await target(owner, args['feedbackId'])
        review = {**review, 'manifest': manifest(rows)}
        review['disclosure'] = disclosure(review)
        return {'review': review, 'url': review['url'], 'message': 'Files and destination reviewed. Nothing published.'}

    saved = owner.receipt(args['reviewRequestId'])
    if (saved.get('operation') != 'feedback.attachments.review' or saved.get('status') != 'completed'
            or saved.get('feedbackId') != args['feedbackId']):
        raise ValueError('Choose the completed review for this exact report')
    review = saved['review']
    actual = sorted((row['id'], row['sha256']) for row in review['manifest'])
    if actual != sorted((row['id'], row['sha256']) for row in args['confirmedFiles']):
        raise ValueError('Confirm the exact complete reviewed file set')
    current = await target(owner, args['feedbackId'])
    if current != {key: review[key] for key in current}:
        raise ValueError('The report, account or repository visibility changed. Review again.')
    rows = selected(owner, [row['id'] for row in review['manifest']])
    if manifest(rows) != review['manifest']:
        raise ValueError('Reviewed file bytes or metadata changed')

    identity = args['requestId']
    prefix = 'repos/' + review['repository']
    journal = {'reviewRequestId': args['reviewRequestId'], 'review': review, 'phases': {}}
    await owner.update(identity, url=review['url'], audit=journal)

    async def phase(name, endpoint, payload, validate):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        row = {'phaseId': identity + ':' + name, 'status': 'unknown', 'endpoint': endpoint, 'payloadSha256': digest}
        journal['phases'][name] = row
        # Persist uncertainty before any bytes leave. No code path retries this.
        await owner.update(identity, audit=journal)
        effect['attempted'] = True
        result = validate(await owner.github(endpoint, payload))
        row.update(status='succeeded', result=result)
        await owner.update(identity, audit=journal)
        return result

    tree = []
    for row, data in rows:
        sha = await phase('blob:' + row['id'], prefix + '/git/blobs',
                          {'encoding': 'base64', 'content': base64.b64encode(data).decode('ascii')}, uploads.object_sha)
        tree.append({'path': row['id'] + '/' + row['name'], 'mode': '100644', 'type': 'blob', 'sha': sha})
    tree_sha = await phase('tree', prefix + '/git/trees', {'tree': tree}, uploads.object_sha)
    commit = await phase('commit', prefix + '/git/commits',
                         {'message': 'Feedback attachments ' + identity, 'tree': tree_sha, 'parents': []}, uploads.object_sha)
    ref = 'refs/heads/feedback-assets/' + identity
    def validate_ref(result):
        if uploads.object_sha(result.get('object', {})) != commit or result.get('ref') != ref:
            raise ValueError('Attachment branch receipt did not match')
        return {'sha': commit, 'ref': ref}
    await phase('ref', prefix + '/git/refs', {'ref': ref, 'sha': commit}, validate_ref)
    attachments = links(review, commit)
    body = comment_body(identity, args, attachments)
    # Store only a digest, not another copy of the potentially large comment.
    journal['commentSha256'] = hashlib.sha256(body.encode()).hexdigest()
    await owner.update(identity, attachments=attachments, audit=journal)
    def validate_comment(result):
        if (not re.fullmatch(re.escape(review['url']) + r'#issuecomment-[1-9][0-9]*', result.get('html_url', ''))
                or result.get('user', {}).get('id') != review['accountId'] or result.get('body') != body
                or type(result.get('id')) is not int):
            raise ValueError('The attachment comment receipt did not match')
        return {'commentUrl': result['html_url'], 'commentId': result['id']}
    comment = await phase('comment', 'repos/' + review['url'].removeprefix('https://github.com/') + '/comments', {'body': body}, validate_comment)
    return {'url': review['url'], **comment, 'attachments': attachments, 'message': 'Files added in a new comment. Original report retained.'}


def links(review, commit):
    return [{**row, 'visibility': review['visibility'], 'url': 'https://github.com/' + review['repository'] + '/blob/' + commit + '/' + quote(row['id'] + '/' + row['name'], safe='/')} for row in review['manifest']]


def comment_body(identity, args, attachments):
    return args.get('comment', '').strip() + uploads.markdown(attachments) + '\n\n<!-- amplifier-feedback-attachments:' + identity + ' -->'


async def reconcile(owner, args):
    original = owner.receipt(args['additionRequestId'])
    if (original.get('operation') != 'feedback.attachments.add' or original.get('feedbackId') != args['feedbackId']
            or original.get('status') != 'unknown'):
        raise ValueError('Choose an uncertain file addition for this exact report')
    audit = original.get('audit', {})
    review = audit.get('review')
    if not review or await target(owner, args['feedbackId']) != {key: review[key] for key in ('url', 'issueId', 'issueNumber', 'accountId', 'repository', 'repositoryId', 'visibility')}:
        raise ValueError('The reviewed destination or account changed')
    phases = audit['phases']
    if not phases.get('ref'):
        return {'outcome': 'indeterminate', 'message': 'Upload did not reach the attachment branch. The original receipt remains unknown; nothing was repeated.'}
    identity = args['additionRequestId']
    prefix = 'repos/' + review['repository']
    ref_name = 'refs/heads/feedback-assets/' + identity
    ref = await owner.github(prefix + '/git/ref/heads/feedback-assets/' + identity, None)
    commit = uploads.object_sha(ref.get('object', {}))
    if ref.get('ref') != ref_name or ref.get('object', {}).get('type') != 'commit':
        raise ValueError('Attachment branch identity could not be verified')
    # The commit receipt precedes ref creation, so it must already be conclusive.
    if phases.get('commit', {}).get('status') != 'succeeded' or commit != phases['commit']['result']:
        raise ValueError('Attachment branch does not match the original commit')
    saved_commit = await owner.github(prefix + '/git/commits/' + commit, None)
    if (saved_commit.get('sha') != commit or saved_commit.get('parents') != []
            or saved_commit.get('message') != 'Feedback attachments ' + identity
            or saved_commit.get('tree', {}).get('sha') != phases.get('tree', {}).get('result')):
        raise ValueError('Original isolated attachment commit could not be verified')
    tree = await owner.github(prefix + '/git/trees/' + saved_commit['tree']['sha'], None)
    entries = tree.get('tree')
    expected = {row['id']: row for row in review['manifest']}
    # Git trees can be nested. Read each exact directory tree, never recursive history.
    if tree.get('truncated') is True or not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError('Attachment tree differs from the complete reviewed set')
    seen = set()
    for entry in entries:
        row = expected.get(entry.get('path'))
        if not row or entry.get('type') != 'tree' or row['id'] in seen:
            raise ValueError('Unexpected attachment directory')
        seen.add(row['id'])
        child = await owner.github(prefix + '/git/trees/' + uploads.object_sha(entry), None)
        files = child.get('tree')
        if child.get('truncated') is True or not isinstance(files, list) or len(files) != 1:
            raise ValueError('Unexpected attachment directory contents')
        file = files[0]
        if (file.get('path') != row['name'] or file.get('type') != 'blob' or file.get('mode') != '100644'
                or file.get('sha') != phases.get('blob:' + row['id'], {}).get('result')):
            raise ValueError('Attachment content differs from the original blob')
    attachments = links(review, commit)
    original_args = json.loads(owner.db.execute('SELECT payload FROM commands WHERE id=?', (identity,)).fetchone()[0])
    expected_body = comment_body(identity, original_args, attachments)
    matched, complete = [], False
    # Bounded search; incomplete or ambiguous matches preserve the original unknown.
    if phases.get('comment'):
        for page in range(1, 6):
            comments = await owner.github('repos/' + review['url'].removeprefix('https://github.com/') + f'/comments?per_page=100&page={page}', None)
            if not isinstance(comments, list) or len(comments) > 100:
                raise ValueError('Invalid bounded comment page')
            for comment in comments:
                if (comment.get('user', {}).get('id') == review['accountId'] and comment.get('body') == expected_body
                        and type(comment.get('id')) is int and re.fullmatch(re.escape(review['url']) + r'#issuecomment-[1-9][0-9]*', comment.get('html_url', ''))):
                    matched.append(comment)
            if len(comments) < 100:
                complete = True
                break
    if complete and len(matched) == 1:
        found = matched[0]
        phases['ref'].update(status='succeeded', result={'sha': commit, 'ref': ref_name}, reconciledBy=args['requestId'])
        phases['comment'].update(status='succeeded', result={'commentUrl': found['html_url'], 'commentId': found['id']}, reconciledBy=args['requestId'])
        await owner.update(identity, status='completed', audit=audit, attachments=attachments, commentUrl=found['html_url'], commentId=found['id'], message='Original file delivery confirmed. Nothing was reposted.')
        return {'outcome': 'found', 'url': review['url'], 'commentUrl': found['html_url'], 'message': 'Original delivery confirmed.'}
    return {'outcome': 'indeterminate', 'url': review['url'], 'message': 'Stored files were verified; comment delivery remains unconfirmed. Nothing was uploaded or posted.'}
