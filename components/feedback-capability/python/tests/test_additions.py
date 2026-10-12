import base64
import copy
import hashlib
import json

import pytest

from amplifier_unified_feedback import files
from amplifier_unified_feedback.owner import Owner, REPOSITORY


class GitHub:
    def __init__(self):
        self.repo = {'id': 42, 'full_name': REPOSITORY, 'private': False}
        self.user = {'id': 7, 'login': 'fixture'}
        self.issue = {'id': 101, 'number': 1, 'html_url': 'https://github.com/' + REPOSITORY + '/issues/1', 'user': {'id': 7}, 'body': '<!-- amplifier-feedback:original-report -->'}
        self.calls, self.objects, self.comments = [], {}, []
        self.lose = None

    async def __call__(self, endpoint, payload, **kwargs):
        self.calls.append((endpoint, copy.deepcopy(payload), kwargs))
        prefix = 'repos/' + REPOSITORY
        if endpoint == 'user': return dict(self.user)
        if endpoint == prefix: return dict(self.repo)
        if endpoint == prefix + '/issues/1': return dict(self.issue)
        if endpoint.startswith(prefix + '/issues/1/comments?'): return copy.deepcopy(self.comments)
        if payload is None: return copy.deepcopy(self.objects[endpoint])
        if endpoint == prefix + '/issues':
            return {**self.issue, **payload}
        if endpoint == prefix + '/issues/1/comments':
            result = {**payload, 'id': len(self.comments) + 1, 'user': dict(self.user), 'html_url': self.issue['html_url'] + '#issuecomment-' + str(len(self.comments) + 1)}
            self.comments.append(result)
        elif endpoint.endswith('/git/blobs'):
            data = base64.b64decode(payload['content'])
            sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            result = {'sha': sha}
        elif endpoint.endswith('/git/trees'):
            tree = []
            for entry in payload['tree']:
                folder, name = entry['path'].split('/', 1)
                child_sha = hashlib.sha1(json.dumps(entry).encode()).hexdigest()
                self.objects[prefix + '/git/trees/' + child_sha] = {'sha': child_sha, 'tree': [{**entry, 'path': name}]}
                tree.append({'path': folder, 'type': 'tree', 'mode': '040000', 'sha': child_sha})
            sha = hashlib.sha1(json.dumps(tree).encode()).hexdigest()
            self.objects[prefix + '/git/trees/' + sha] = {'sha': sha, 'tree': tree, 'truncated': False}
            result = {'sha': sha}
        elif endpoint.endswith('/git/commits'):
            sha = hashlib.sha1(json.dumps(payload).encode()).hexdigest()
            result = {'sha': sha}
            self.objects[prefix + '/git/commits/' + sha] = {**payload, 'sha': sha, 'tree': {'sha': payload['tree']}}
        elif endpoint.endswith('/git/refs'):
            result = {'ref': payload['ref'], 'object': {'sha': payload['sha'], 'type': 'commit'}}
            self.objects[prefix + '/git/ref/' + payload['ref'].removeprefix('refs/')] = result
        else: raise AssertionError(endpoint)
        if self.lose and endpoint.endswith(self.lose):
            self.lose = None
            raise OSError('Lost acknowledged remote response')
        return copy.deepcopy(result)


async def no_host(*args): raise AssertionError('No conversation, model or export access')
async def notify(*args): pass


def setup(tmp_path):
    github = GitHub()
    owner = Owner({'dataDir': str(tmp_path)}, no_host, notify, github=github)
    receipt = {'requestId': 'original-report', 'operation': 'feedback.submit', 'status': 'completed', 'url': github.issue['html_url']}
    owner.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)', ('original-report', 'fixture', 'feedback.submit', '{}', 'completed', json.dumps(receipt)))
    rows = [files.save(tmp_path, 'one.txt', b'original first bytes', 'text/plain'), files.save(tmp_path, 'two.txt', b'original second bytes', 'text/plain')]
    for row in rows: owner.db.execute('INSERT INTO attachments VALUES(?,?,?)', (row['id'], 'host', json.dumps(row)))
    return owner, github, rows


async def action(owner, name, args):
    return await owner.request('action', {'operation': 'feedback.' + name, 'args': args})


async def reviewed(owner, rows):
    receipt = await action(owner, 'attachments.review', {'requestId': 'review-files', 'feedbackId': 'original-report', 'attachmentIds': [row['id'] for row in rows]})
    assert receipt['status'] == 'completed'
    return {'requestId': 'add-files-once', 'feedbackId': 'original-report', 'reviewRequestId': 'review-files', 'confirmedFiles': [{'id': row['id'], 'sha256': row['sha256']} for row in rows], 'comment': 'Only these reviewed files.'}


@pytest.mark.asyncio
@pytest.mark.parametrize('private', [True, False])
async def test_exact_public_or_private_files_append_without_replacing_original(tmp_path, private):
    owner, github, rows = setup(tmp_path); github.repo['private'] = private
    try:
        args = await reviewed(owner, rows); before = dict(github.issue)
        assert all(payload is None for _, payload, _ in github.calls)
        result = await action(owner, 'attachments.add', args)
        assert result['status'] == 'completed' and github.issue == before
        assert len(github.comments) == 1 and github.comments[0]['body'].endswith('<!-- amplifier-feedback-attachments:add-files-once -->')
        assert len(result['audit']['phases']) == 6 and all(row['status'] == 'succeeded' for row in result['audit']['phases'].values())
        assert all(row['visibility'] == ('private' if private else 'public') for row in result['attachments'])
        assert not any(options.get('method') == 'PATCH' for _, _, options in github.calls)
        assert await action(owner, 'attachments.add', args) == result
        assert len(github.comments) == 1
        assert [files.location(tmp_path, row['id']).joinpath('content').read_bytes() for row in rows] == [b'original first bytes', b'original second bytes']
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['repositoryId', 'visibility', 'account', 'issue', 'hash', 'partial', 'bytes'])
async def test_changed_review_refuses_before_any_remote_write(tmp_path, change):
    owner, github, rows = setup(tmp_path)
    try:
        args = await reviewed(owner, rows)
        if change == 'repositoryId': github.repo['id'] = 43
        elif change == 'visibility': github.repo['private'] = True
        elif change == 'account': github.user['id'] = 8
        elif change == 'issue': github.issue['id'] = 102
        elif change == 'hash': args['confirmedFiles'][0]['sha256'] = '0' * 64
        elif change == 'partial': args['confirmedFiles'].pop()
        else:
            path = files.location(tmp_path, rows[0]['id']) / 'content'; path.chmod(0o600); path.write_bytes(b'changed')
        result = await action(owner, 'attachments.add', args)
        assert result['status'] == 'failed' and not any(payload is not None for _, payload, _ in github.calls)
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['/git/blobs', '/git/trees', '/git/commits', '/git/refs', '/comments'])
async def test_lost_phase_restart_never_repeats_any_remote_write(tmp_path, lost):
    owner, github, rows = setup(tmp_path)
    try:
        args = await reviewed(owner, rows); github.lose = lost
        result = await action(owner, 'attachments.add', args)
        assert result['status'] == 'unknown' and any(row['status'] == 'unknown' for row in result['audit']['phases'].values())
        writes = [(endpoint, payload) for endpoint, payload, _ in github.calls if payload is not None]
        await owner.close(); owner = Owner({'dataDir': str(tmp_path)}, no_host, notify, github=github)
        assert await action(owner, 'attachments.add', args) == result
        with pytest.raises(ValueError, match='different feedback'):
            await action(owner, 'attachments.add', {**args, 'comment': 'Changed retry'})
        reconciled = await action(owner, 'attachments.reconcile', {'requestId': 'verify-original', 'feedbackId': 'original-report', 'additionRequestId': args['requestId']})
        assert reconciled['status'] == 'completed'
        assert reconciled['outcome'] == ('found' if lost == '/comments' else 'indeterminate')
        assert owner.receipt(args['requestId'])['status'] == ('completed' if lost == '/comments' else 'unknown')
        assert writes == [(endpoint, payload) for endpoint, payload, _ in github.calls if payload is not None]
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'repositoryId', 'visibility', 'hash', 'partial'])
async def test_public_initial_submission_requires_exact_file_and_destination_consent(tmp_path, change):
    owner, github, rows = setup(tmp_path)
    try:
        destination = await action(owner, 'destination', {})
        args = {'requestId': 'new-report-files', 'title': 'Reviewed', 'body': 'Only this text', 'category': 'bug', 'includeDiagnostics': False,
                'attachmentIds': [row['id'] for row in rows], 'uploadDestination': destination, 'confirmedFiles': [{'id': row['id'], 'sha256': row['sha256']} for row in rows]}
        if change == 'repositoryId': github.repo['id'] = 43
        elif change == 'visibility': github.repo['private'] = True
        elif change == 'hash': args['confirmedFiles'][0]['sha256'] = '0' * 64
        elif change == 'partial': args['confirmedFiles'].pop()
        result = await action(owner, 'submit', args)
        assert result['status'] == ('completed' if change == 'none' else 'failed')
        assert bool([payload for _, payload, _ in github.calls if payload is not None]) == (change == 'none')
    finally: await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['duplicate', 'foreignAuthor', 'extraFile', 'changedCommit', 'wrongRef'])
async def test_reconciliation_never_confirms_ambiguous_or_tampered_delivery(tmp_path, change):
    owner, github, rows = setup(tmp_path)
    try:
        args = await reviewed(owner, rows); github.lose = '/comments'
        original = await action(owner, 'attachments.add', args)
        assert original['status'] == 'unknown'
        prefix = 'repos/' + REPOSITORY
        if change == 'duplicate': github.comments.append(copy.deepcopy(github.comments[0]))
        elif change == 'foreignAuthor': github.comments[0]['user']['id'] = 8
        elif change == 'extraFile':
            sha = original['audit']['phases']['tree']['result']
            github.objects[prefix + '/git/trees/' + sha]['tree'].append({'path': 'unreviewed', 'type': 'blob', 'sha': 'f' * 40})
        elif change == 'changedCommit':
            sha = original['audit']['phases']['commit']['result']
            github.objects[prefix + '/git/commits/' + sha]['parents'] = [{'sha': 'f' * 40}]
        else: github.objects[prefix + '/git/ref/heads/feedback-assets/' + args['requestId']]['ref'] = 'refs/heads/wrong'
        writes = sum(payload is not None for _, payload, _ in github.calls)
        result = await action(owner, 'attachments.reconcile', {'requestId': 'check-tampered', 'feedbackId': 'original-report', 'additionRequestId': args['requestId']})
        assert result.get('outcome') != 'found'
        assert owner.receipt(args['requestId'])['status'] == 'unknown'
        assert sum(payload is not None for _, payload, _ in github.calls) == writes
    finally: await owner.close()
