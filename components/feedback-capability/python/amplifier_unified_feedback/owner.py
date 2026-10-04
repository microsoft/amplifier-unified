from .retention import selected, result, exists, managed_selected, add_protection
"""Indexed, explicit feedback delivery with durable uncertainty and no UI state."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import time
from urllib.parse import urlencode

from filelock import FileLock
from amplifier_operations.quiescence import DurableIntakeFence
from jsonschema import Draft202012Validator
from . import __version__, files, uploads
from .github import github_api
from .aggregate_admission import AggregateAdmissions, validate_authority
from .redaction import redact
from .schemas import ACTIONS, REMOTE_WRITES

REPOSITORY = 'microsoft/amplifier-unified'
RECEIPT_REPOSITORIES = (REPOSITORY, 'bkrabach/amplifier-unified')
UNKNOWN = 'The remote write may have completed. Inspect its saved receipt and GitHub; this request will not be repeated.'

def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

def revision(issue):
    return digest({key: issue.get(key) for key in ('id', 'number', 'html_url', 'title', 'body', 'state', 'updated_at')})

def owned(issue, user, identity, url=None):
    actual = issue.get('html_url')
    return (type(user.get('id')) is int and issue.get('user', {}).get('id') == user['id']
            and type(issue.get('number')) is int and 'pull_request' not in issue
            and actual in [f'https://github.com/{repo}/issues/{issue["number"]}' for repo in RECEIPT_REPOSITORIES]
            and (url is None or actual == url)
            and f'<!-- amplifier-feedback:{identity} -->' in (issue.get('body') or ''))

def validate_storage(path):
    # This current embedded profile owns durable authority, not just an index.
    # Old unmarked layouts cannot prove absence is a supported migration.
    if not os.path.lexists(path):
        if any(os.path.lexists(str(path)+suffix) for suffix in ['-wal','-shm','-journal']):
            raise sqlite3.DatabaseError('Owner database missing with surviving storage evidence')
        return
    check=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        columns={'commands': 'id,fingerprint,operation,payload,status,receipt', 'attachments': 'id,session,metadata', 'reviews': 'id,session,payload'}
        for table,names in columns.items():
            kind=check.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()
            if kind is None or kind[0]!='table':raise sqlite3.DatabaseError('Authoritative owner schema unavailable; no implicit repair or legacy migration')
            check.execute('SELECT '+names+' FROM '+table+' LIMIT 0')
    finally:check.close()

class Owner:
    def __init__(self, config, host, notify, *, github=None):
        self.root = Path(config['dataDir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.guard = FileLock(str(self.root / 'owner.lock'))
        self.guard.acquire(timeout=0)
        try:
            validate_storage(self.root/'feedback.sqlite3')
            validate_authority(self.root / 'intake.sqlite3')
            self.intake = DurableIntakeFence(self.root / 'intake.sqlite3')
            self.aggregate_admissions = AggregateAdmissions(self.intake)
            self.awaiting_idle = False
            self.db = sqlite3.connect(self.root / 'feedback.sqlite3', isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,operation TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS feedback_status ON commands(status);
                CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,session TEXT NOT NULL,metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,session TEXT NOT NULL,payload TEXT NOT NULL);''')
            self.db.execute("UPDATE commands SET status='unknown',receipt=json_set(receipt,'$.status','unknown','$.message',?) WHERE status='dispatching'", (UNKNOWN,))
            self.host, self.notify, self.github = host, notify, github or github_api
            self.tasks = set()
            self.closing = False

            self.db.execute("CREATE INDEX IF NOT EXISTS retention_feedback ON commands(status,json_extract(payload,'$.sessionId'))")
        except BaseException:
            if hasattr(self,'db'):self.db.close()
            if hasattr(self,'intake'):self.intake.close()
            self.guard.release();raise

    async def notify_counted(self, *args):
        # Awaited callbacks remain owner work until their actual return.
        self.intake.background += 1
        try:
            return await self.notify(*args)
        finally:
            self.intake.background -= 1

    async def close(self):
        self.closing = True
        self.db.close()
        self.intake.close()
        self.guard.release()

    def receipt(self, identity):
        row = self.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('No feedback receipt has that request ID')
        return json.loads(row[0])

    def project(self, receipt):
        return {key: receipt[key] for key in ('requestId', 'feedbackId', 'operation', 'status', 'title', 'category', 'createdAt', 'updatedAt', 'url', 'commentUrl', 'message') if key in receipt}

    def page(self, args):
        limit = args.get('limit', 20)
        cursor = args.get('cursor', 2**63 - 1)
        rows = self.db.execute('SELECT rowid,receipt FROM commands WHERE rowid<? ORDER BY rowid DESC LIMIT ?', (cursor, limit + 1)).fetchall()
        return {'repository': REPOSITORY, 'items': [self.project(json.loads(row['receipt'])) for row in rows[:limit]],
                'nextCursor': rows[limit - 1]['rowid'] if len(rows) > limit else None}

    async def update(self, identity, **fields):
        receipt = {**self.receipt(identity), **fields, 'updatedAt': time.time()}
        if len(encode(receipt).encode()) > 2_000_000:
            raise ValueError('Feedback receipt exceeds the selected read bound')
        self.db.execute('UPDATE commands SET status=?,receipt=? WHERE id=?', (receipt['status'], encode(receipt), identity))
        try:
            await self.notify_counted('owner/changed', {})
        except (BrokenPipeError, ConnectionError):
            # Notification loss cannot erase a confirmed durable result.
            pass
        return receipt

    def diagnostics(self, device=None):
        return {'schemaVersion': 3, 'ownerVersion': __version__, 'osFamily': platform.system(),
                'pythonVersion': platform.python_version(), 'workerLoadedComponentGeneration': 'unverified',
                **({'device': device} if device else {})}

    def retention_references(self,args,*,managed=False):
        sessions=managed_selected(self.intake,args) if managed else selected(self.intake,args)
        def check(session):
            reasons=[]
            if exists(self.db,"SELECT 1 FROM commands WHERE status IN ('dispatching','unknown') AND (json_extract(payload,'$.sessionId')=? OR json_extract(payload,'$.sessionId') IS NULL) LIMIT 1",(session,)):reasons.append('feedback-unsettled')
            return reasons
        return result(sessions,check)

    def managed_references(self,args):
        base=self.retention_references(args,managed=True);sessions=args['sessions']
        return base


    async def request(self, method, params):
        if method=='quiescence.aggregateAdmission':return self.aggregate_admissions.request(params)
        if method=='quiescence.retention':return self.retention_references(params)
        if method=='quiescence.managedFiles':return self.managed_references(params)
        if method=='quiescence.abortAdmission':return self.intake.abort_admission(params,owner_id=params['ownerId'],pending=0)
        if method=='quiescence.admissionAbortReceipt':return self.intake.admission_abort_receipt(params,owner_id=params['ownerId'])
        if method == 'quiescence.acquire':
            value = self.intake.acquire(params)
            if not value['acquired']: self.awaiting_idle = True
            return value
        if method == 'quiescence.release': return self.intake.release(params)
        if method == 'quiescence.inspect': return {'intakeClosed': bool(self.intake.fence), 'fence': self.intake.fence, 'activeRequests': self.intake.calls}
        passive = method in {'initialize', 'actions', 'snapshot'} or method == 'action' and params.get('operation') in {'feedback.list', 'feedback.receipt', 'feedback.diagnostics'}
        if self.intake.fence and not passive:
            raise ValueError('Feedback intake is closed; no new delivery or preparation was admitted')
        if not passive: self.intake.calls += 1
        try:
            return await self._request(method, params)
        finally:
            if not passive:
                self.intake.calls -= 1
                if self.awaiting_idle and not self.intake.calls:
                    self.awaiting_idle = False
                    await self.notify_counted('owner/idle', {})

    async def _request(self, method, params):
        if method == 'initialize':
            return {'protocolVersion':1,'quiescence':{'version':1,**({'aggregateAdmission':{'version':1}} if getattr(DurableIntakeFence,'ADMISSION_ABORT_VERSION',0)==1 else {}),'retentionHide':{'version':1},'managedFiles':{'version':1,'preservesCanonical':True},'heldIntake':True,'durableRelease':True,**({'admissionAbort':{'version':1}} if getattr(DurableIntakeFence,'ADMISSION_ABORT_VERSION',0)==1 else {}),**({'serviceStop':{'version':1}} if getattr(DurableIntakeFence,'SERVICE_STOP_VERSION',0)==1 else {})}}
        if method == 'actions':
            return ACTIONS
        if method == 'snapshot':
            return self.page({})
        if method != 'action':
            raise ValueError('Unknown feedback owner method')
        operation, args = params['operation'], params.get('args', {})
        if operation not in ACTIONS:
            raise ValueError('Unadvertised feedback action')
        if list(Draft202012Validator(ACTIONS[operation]['parameters']).iter_errors(args)):
            raise ValueError('Invalid explicit feedback arguments')
        if self.closing:
            raise ValueError('Feedback owner is closing')
        if operation == 'feedback.list':
            return self.page(args)
        if operation == 'feedback.receipt':
            return self.receipt(args['requestId'])
        if operation == 'feedback.diagnostics':
            if args.get('requestId'):
                receipt = self.receipt(args['requestId'])
                return {'snapshot': 'accepted', 'diagnostics': receipt.get('diagnostics')}
            return {'snapshot': 'current', 'diagnostics': self.diagnostics(args.get('deviceDiagnostics'))}

        identity = args['requestId']
        intent = digest([operation, args])
        old = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
        if old:
            if old['fingerprint'] != intent:
                raise ValueError('Request identity already belongs to different feedback contents')
            return json.loads(old['receipt'])
        if self.db.execute("SELECT count(*) FROM commands WHERE status='dispatching'").fetchone()[0] >= 8:
            raise ValueError('Wait for accepted feedback work to finish')
        receipt = {'requestId': identity, 'operation': operation, 'status': 'dispatching', 'createdAt': time.time()}
        receipt.update({key: args[key] for key in ('feedbackId', 'title', 'category') if key in args})
        self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)', (identity, intent, operation, encode(args), 'dispatching', encode(receipt)))
        effect = {'attempted': False}
        try:
            result = await self.perform(operation, args, params, effect)
            return await self.update(identity, status='completed', **result)
        except BaseException as exc:
            status = 'unknown' if effect['attempted'] else 'failed'
            message = UNKNOWN if effect['attempted'] else (str(exc) if isinstance(exc, ValueError) else 'Feedback preparation or read did not complete. No remote write was attempted.')
            saved = await self.update(identity, status=status, message=message)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return saved

    async def perform(self, operation, args, context, effect):
        if operation == 'feedback.attachment.add':
            return {'attachment': await self.add_attachment(args)}
        if operation == 'feedback.excerpt.review':
            return {'review': await self.review(args)}
        if operation == 'feedback.excerpt.stage':
            row = self.db.execute('SELECT session,payload FROM reviews WHERE id=?', (args['reviewId'],)).fetchone()
            if not row or row['session'] != args['sessionId']:
                raise ValueError('Reviewed excerpt belongs to a different conversation')
            review = json.loads(row['payload'])
            if review['requiresExtraReview'] and args.get('acknowledgeWarnings') is not True:
                raise ValueError('Acknowledge the reviewed disclosure warnings first')
            item = files.save(self.root, review['filename'], review['text'].encode(), 'text/markdown')
            item['excerpt'] = {key: review[key] for key in ('reviewId', 'sha256', 'repository', 'visibility', 'bytes')}
            self.db.execute('INSERT INTO attachments VALUES(?,?,?)', (item['id'], args['sessionId'], encode(item)))
            return {'attachment': item}
        if operation == 'feedback.submit':
            return await self.submit(args, context, effect)
        if operation == 'feedback.reconcile':
            return await self.reconcile(args)
        return await self.followup(operation, args, effect)

    async def add_attachment(self, args):
        meta = await self.host('attachmentMetadata', {'uri': args['resourceUri']})
        if not isinstance(meta.get('size'), int) or not 0 < meta['size'] <= files.MAX_FILE or meta.get('sha256') != args['sha256']:
            raise ValueError('Attachment size or reviewed hash changed')
        body = bytearray()
        while len(body) < meta['size']:
            page = await self.host('attachmentPage', {'uri': args['resourceUri'], 'offset': len(body), 'limit': 262144})
            chunk = base64.b64decode(page['data'], validate=True)
            if page.get('encoding') != 'base64' or not chunk or len(chunk) > 262144 or len(body) + len(chunk) > meta['size']:
                raise ValueError('Invalid bounded attachment page')
            body.extend(chunk)
        if hashlib.sha256(body).hexdigest() != args['sha256']:
            raise ValueError('Attachment bytes differ from the reviewed hash')
        item = files.save(self.root, args['name'], body, meta['contentType'])
        self.db.execute('INSERT INTO attachments VALUES(?,?,?)', (item['id'], 'host', encode(item)))
        return item

    async def review(self, args):
        exported = await self.host('readExport', {'session': args['sessionId'], 'uri': args['resourceUri']})
        summary = exported.get('summary', {})
        if summary.get('minimal') is not True or summary.get('format') != 'markdown':
            raise ValueError('Choose an immutable minimal Markdown export before preparing feedback')
        text = args.get('text', exported['text'])
        if not text.strip() or len(text.encode()) > 64000:
            raise ValueError('Choose an excerpt up to 64 KB; nothing was truncated')
        if 'text' not in args:
            text = re.sub(r'^\[Attachment reference: .*; payload omitted\]$', '[Attachment reference omitted; no file content included.]', text, flags=re.M)
        text, redactions, warnings = redact(text, [str(self.root)])
        if len(text.encode()) > 64000:
            raise ValueError('Redacted excerpt exceeds 64 KB; nothing was truncated')
        target = await self.github('repos/' + REPOSITORY, None)
        if type(target.get('private')) is not bool or target.get('full_name', '').casefold() != REPOSITORY.casefold():
            raise ValueError('Feedback repository identity and visibility could not be verified')
        sha = hashlib.sha256(text.encode()).hexdigest()
        result = {'text': text, 'sha256': sha, 'sessionId': args['sessionId'], 'resourceUri': args['resourceUri'],
                  'filename': 'conversation-excerpt-' + sha[:12] + '.md', 'bytes': len(text.encode()),
                  'repository': REPOSITORY, 'visibility': 'private' if target['private'] else 'public',
                  'redactions': redactions, 'warnings': warnings, 'summary': summary,
                  'requiresExtraReview': summary.get('scope') == 'all' or summary.get('messageCount', 0) > 20 or len(text.encode()) > 16000 or bool(redactions) or len(warnings) > 1}
        result['reviewId'] = digest(result)
        self.db.execute('INSERT OR IGNORE INTO reviews VALUES(?,?,?)', (result['reviewId'], args['sessionId'], encode(result)))
        return result

    async def submit(self, args, context, effect):
        if not args['title'].strip() or not args['body'].strip():
            raise ValueError('Enter a title and description before sending')
        staged = []
        for identity in args.get('attachmentIds', []):
            row = self.db.execute('SELECT session,metadata FROM attachments WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('Attachment is unavailable in the authorized conversation')
            item = json.loads(row['metadata'])
            staged.append((item, uploads.read_verified(self.root, item)))
        if sum(item['size'] for item, _ in staged) > uploads.MAX_TOTAL_BYTES:
            raise ValueError('Selected feedback files exceed 24 MiB')
        actual = sorted((item['id'], item['excerpt']['sha256']) for item, _ in staged if item.get('excerpt'))
        approved = sorted((item['id'], item['sha256']) for item in args.get('confirmedExcerpts', []))
        if (actual or approved) and (args.get('confirmExcerpts') is not True or actual != approved):
            raise ValueError('Review and confirm the exact selected excerpt IDs and hashes')
        user = await self.github('user', None)
        if type(user.get('id')) is not int:
            raise ValueError('GitHub user could not be verified')
        facts = self.diagnostics(args.get('deviceDiagnostics')) if args.get('includeDiagnostics', True) else None
        await self.update(args['requestId'], diagnostics=facts)
        body = args['body'] + '\n\n---\nCategory: ' + args['category']
        if facts:
            body += '\n\n### Reviewed reproduction facts\n\n```json\n' + json.dumps(facts, indent=2) + '\n```'
        async def write(endpoint, payload, **kwargs):
            # Destination visibility reads occur before this flips, so a refused
            # public upload is a known no-write result rather than uncertainty.
            if payload is not None:
                effect['attempted'] = True
            return await self.github(endpoint, payload, **kwargs)
        if staged:
            uploaded = await uploads.upload(REPOSITORY, args['requestId'], staged, write)
            await self.update(args['requestId'], attachments=uploaded)
            body += uploads.markdown(uploaded)
        body += f'\n\n<!-- amplifier-feedback:{args["requestId"]} -->'
        result = await write('repos/' + REPOSITORY + '/issues', {'title': args['title'], 'body': body})
        if not owned(result, user, args['requestId']):
            raise ValueError('GitHub returned an invalid feedback receipt')
        return {'url': result['html_url'], 'message': 'Feedback submitted.', 'diagnostics': facts}

    async def followup(self, operation, args, effect):
        original = self.receipt(args['feedbackId'])
        url = original.get('url', '')
        if original['operation'] != 'feedback.submit' or original['status'] != 'completed' or not any(re.fullmatch(re.escape('https://github.com/' + repo + '/issues/') + r'[1-9][0-9]*', url) for repo in RECEIPT_REPOSITORIES):
            raise ValueError('Choose a successfully submitted report from this host')
        endpoint = 'repos/' + url.removeprefix('https://github.com/')
        user = await self.github('user', None)
        issue = await self.github(endpoint, None)
        if not owned(issue, user, args['feedbackId'], url):
            raise ValueError('The current GitHub owner and original feedback marker do not match')
        if operation == 'feedback.get':
            comments = await self.github(endpoint + f'/comments?per_page=20&page={args.get("page", 1)}', None)
            if not isinstance(comments, list) or len(comments) > 20:
                raise ValueError('Invalid bounded feedback comment page')
            if len(encode([issue, comments]).encode()) > 1_500_000:
                raise ValueError('Selected feedback report exceeds the read bound')
            original_payload = json.loads(self.db.execute('SELECT payload FROM commands WHERE id=?', (args['feedbackId'],)).fetchone()[0])
            corrected = self.db.execute("SELECT payload FROM commands WHERE operation='feedback.update' AND status='completed' AND json_extract(payload,'$.feedbackId')=? ORDER BY rowid DESC LIMIT 1", (args['feedbackId'],)).fetchone()
            editable = json.loads(corrected[0]) if corrected else original_payload
            return {'report': {'editableTitle': editable['title'], 'editableBody': editable['body'], 'feedbackId': args['feedbackId'], 'url': url, 'title': issue.get('title'), 'body': issue.get('body'), 'state': issue.get('state'), 'revision': revision(issue),
                               'page': args.get('page', 1), 'hasMore': len(comments) == 20,
                               'comments': [{key: row.get(key) for key in ('id', 'body', 'html_url', 'created_at', 'updated_at', 'user')} | {'author': {key: row.get('user', {}).get(key) for key in ('id', 'login')}} for row in comments]}}
        if operation != 'feedback.comment' and revision(issue) != args['expectedRevision']:
            raise ValueError('The report changed. Refresh and review before changing it; nothing was posted')
        if operation in {'feedback.close', 'feedback.reopen'}:
            desired = 'closed' if operation.endswith('close') else 'open'
            await self.update(args['requestId'], audit={'sourceRevision': revision(issue), 'priorState': issue.get('state'), 'desiredState': desired, 'author': user['id']})
            if issue.get('state') != desired:
                effect['attempted'] = True
                result = await self.github(endpoint, {'state': desired}, method='PATCH')
                if not owned(result, user, args['feedbackId'], url) or result.get('state') != desired:
                    raise ValueError('Invalid issue state receipt')
            return {'url': url, 'issueState': desired}
        body = args['body']
        if not body.strip():
            raise ValueError('Enter a comment before sending')
        if operation == 'feedback.update':
            await self.update(args['requestId'], audit={'sourceRevision': revision(issue), 'priorTitle': issue.get('title'), 'priorBody': issue.get('body'), 'title': args['title'], 'body': body, 'author': user['id']})
            body = '## Feedback correction\n\n### Updated title\n' + args['title'] + '\n\n### Updated description\n' + body + '\n\nOriginal report retained.'
        effect['attempted'] = True
        result = await self.github(endpoint + '/comments', {'body': body + f'\n\n<!-- amplifier-feedback-comment:{args["requestId"]} -->'})
        if not re.fullmatch(re.escape(url) + r'#issuecomment-[1-9][0-9]*', result.get('html_url', '')) or result.get('user', {}).get('id') != user['id']:
            raise ValueError('Invalid feedback comment receipt')
        return {'url': url, 'commentUrl': result['html_url'], 'commentId': result.get('id')}

    async def reconcile(self, args):
        original = self.receipt(args['feedbackId'])
        if original['operation'] != 'feedback.submit' or original['status'] != 'unknown':
            raise ValueError('Choose an original submission with unknown delivery')
        user = await self.github('user', None)
        if type(user.get('id')) is not int or not re.fullmatch(r'[A-Za-z0-9-]+', user.get('login', '')):
            raise ValueError('GitHub user could not be verified')
        matches, complete, checked = {}, True, 0
        for repository in RECEIPT_REPOSITORIES:
            query = f'repo:{repository} is:issue author:{user["login"]} in:body "amplifier-feedback:{args["feedbackId"]}"'
            page = await self.github('search/issues?' + urlencode({'q': query, 'per_page': 20}), None)
            if not isinstance(page.get('items'), list):
                raise ValueError('Invalid feedback search response')
            complete = complete and page.get('incomplete_results') is False and type(page.get('total_count')) is int and page['total_count'] <= 20
            for candidate in page['items'][:20]:
                checked += 1
                if not owned(candidate, user, args['feedbackId']):
                    continue
                issue = await self.github('repos/' + candidate['html_url'].removeprefix('https://github.com/'), None)
                if owned(issue, user, args['feedbackId'], candidate['html_url']):
                    matches[issue['html_url']] = issue
        evidence = {'checkedCandidates': checked, 'searchComplete': complete, 'matchingIssues': len(matches)}
        if complete and len(matches) == 1:
            url = next(iter(matches))
            await self.update(args['feedbackId'], status='completed', url=url, reconciliationRequestId=args['requestId'], message='Original delivery confirmed; nothing was reposted.')
            return {'outcome': 'found', 'url': url, 'evidence': evidence}
        return {'outcome': 'not_found' if complete and not matches else 'indeterminate', 'evidence': evidence, 'message': 'Original delivery remains unknown; nothing was reposted.'}
