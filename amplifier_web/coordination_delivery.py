"""Fresh coordination reference data in the fitted provider request only.

Hook ephemerality can still accumulate reminders within a native turn. Use the
existing provider delivery boundary, as surfaces and memory do, so observation
replacement does not depend on the orchestrator's reminder persistence mode.
"""
import asyncio
import copy
import hashlib
import json
import uuid
from contextvars import ContextVar

WORK_BYTES = 12 * 1024
STUB_FIELDS = frozenset({
    'id', 'revision', 'promiseRevision', 'title', 'originSessionId', 'candidate',
    'latestOutcome', 'humanDecisionCurrent', 'attentionReasons', 'nextAction',
    'blockedReason', 'readRef',
})
DISCOVERY_FIELDS = frozenset({
    'workspaceId', 'version', 'snapshotVersion', 'observationVersion', 'items', 'total',
    'truncated', 'nextAfter', 'observedAt', 'coverage', 'detailRequired', 'authority',
})
REQUIRED_FIELDS = frozenset({
    'status', 'reason', 'version', 'rootSessionId', 'generationId', 'inputIds', 'inputCount',
    'associationRequestIds', 'workCount', 'requirementCount', 'requirementsRef',
    'effectAuthorized', 'coverage',
})
WORK_READS = frozenset({'workspace.work.invocation', 'workspace.work.promise', 'workspace.work.source'})
MAX_READS = 512
READ_BYTES = 4 * 1024 * 1024
WORK_ATTEMPT = ContextVar('unified_work_delivery_attempt', default=None)


def _unavailable_work():
    return {'available': False,
            'discovery': {'status': 'unavailable', 'reason': 'Current Work projection is unavailable.'},
            'required': {'status': 'unavailable', 'version': None,
                         'reason': 'Read current Work details; absence or truncation is not permission.',
                         'workCount': None, 'requirementCount': None, 'requirementsRef': None,
                         'coverage': {'complete': False}, 'effectAuthorized': False}}


def _text(value, maximum=200, *, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _texts(value, maximum):
    return isinstance(value, list) and len(value) <= maximum and all(_text(v) for v in value)


def _digest(value):
    return _text(value, 64) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _valid_required(required):
    if (not isinstance(required, dict) or set(required) - REQUIRED_FIELDS
            or required.get('effectAuthorized') is not False
            or not _text(required.get('status'), 20)
            or required['status'] not in {'available', 'unavailable'}):
        return False
    # Even unavailable headers must not smuggle source bodies in scalar slots.
    for field in ('reason', 'rootSessionId', 'generationId'):
        if required.get(field) is not None and not _text(required[field], empty=True):
            return False
    for field in ('inputIds', 'associationRequestIds'):
        if field in required and not _texts(required[field], 16):
            return False
    if 'inputCount' in required and not _integer(required['inputCount']):
        return False
    coverage = required.get('coverage')
    if (not isinstance(coverage, dict)
            or set(coverage) - {'complete', 'supportedInputIds', 'unsupportedInputs', 'reason', 'detail'}
            or type(coverage.get('complete')) is not bool
            or not _texts(coverage.get('supportedInputIds', []), 16)
            or any(not _text(coverage[k]) for k in ('reason', 'detail') if k in coverage)):
        return False
    unsupported = coverage.get('unsupportedInputs', [])
    if (not isinstance(unsupported, list) or len(unsupported) > 16
            or any(not isinstance(row, dict) or set(row) != {'inputId', 'reason'}
                   or not _text(row['inputId']) or not _text(row['reason'], empty=True)
                   for row in unsupported)):
        return False
    if required['status'] == 'unavailable':
        return (coverage['complete'] is False and all(required.get(k) is None for k in
                ('version', 'workCount', 'requirementCount', 'requirementsRef')))
    # Available is an exact producer observation, not an optional collection of
    # optimistic labels. Its read handle and input coverage must agree.
    inputs = required.get('inputIds')
    return (REQUIRED_FIELDS - {'inputCount'} <= set(required)
            and _digest(required['version']) and required['reason'] is None
            and _text(required['rootSessionId']) and _text(required['generationId'])
            and _texts(inputs, 16) and bool(inputs) and len(set(inputs)) == len(inputs)
            and len(required['associationRequestIds']) == len(inputs)
            and required.get('inputCount', len(inputs)) == len(inputs)
            and _integer(required['workCount'], 0, 8)
            and _integer(required['requirementCount'], 2 * required['workCount'],
                         4097 * required['workCount'])
            and coverage == {'complete': True, 'supportedInputIds': inputs, 'unsupportedInputs': []}
            and required['requirementsRef'] == {'action': 'workspace.work.invocation',
                'args': {'sessionId': required['rootSessionId'], 'expectedVersion': required['version']}})


def _valid_stub(row, workspace):
    if (not isinstance(row, dict) or set(row) != STUB_FIELDS
            or not _text(row['id']) or not _text(row['originSessionId'])
            or not _integer(row['revision'], 1) or not _integer(row['promiseRevision'], 1, row['revision'])
            or not _text(row['title'], 100)
            or not _text(row['nextAction'], empty=True) or not _text(row['blockedReason'], empty=True)
            or row['candidate'] is not None and not _text(row['candidate'], 100)
            or not _text(row['latestOutcome'], 100)
            or type(row['humanDecisionCurrent']) is not bool
            or not _texts(row['attentionReasons'], 16)):
        return False
    return row['readRef'] == {'action': 'workspace.work.read',
                              'args': {'workspaceId': workspace, 'id': row['id']}}


def _work_reference(value):
    """Validate the existing bounded producer DTO; never retain source bodies.

    This is a presentation fence, not an effect-admission decision. In particular,
    available discovery plus unavailable requirements cannot become empty scope.
    """
    if not isinstance(value, dict) or set(value) != {'available', 'discovery', 'required'}:
        return _unavailable_work()
    try:
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > WORK_BYTES:
            return _unavailable_work()
        discovery, required = value['discovery'], value['required']
        if (value['available'] is not True or not isinstance(discovery, dict)
                or set(discovery) != DISCOVERY_FIELDS or not _valid_required(required)):
            return _unavailable_work()
        items = discovery.get('items')
        if (not isinstance(items, list) or len(items) > 20
                or not _text(discovery['workspaceId']) or not _integer(discovery['version'])
                or not _digest(discovery['snapshotVersion']) or not _digest(discovery['observationVersion'])
                or not _integer(discovery['total'], len(items))
                or type(discovery.get('truncated')) is not bool
                or discovery['truncated'] != bool(discovery.get('nextAfter'))
                or discovery['nextAfter'] is not None and not _text(discovery['nextAfter'], 4096)
                or type(discovery['observedAt']) not in (int, float) or not 0 <= discovery['observedAt'] < 10**15
                or not _text(discovery['coverage'], 500) or not _text(discovery['authority'], 500)
                or discovery['detailRequired'] is not True):
            return _unavailable_work()
        if any(not _valid_stub(row, discovery['workspaceId']) for row in items):
            return _unavailable_work()
        return copy.deepcopy(value)
    except (TypeError, ValueError, UnicodeError):
        return _unavailable_work()


class CoordinationDelivery:
    def __init__(self, previous, bridge):
        self.previous, self.bridge = previous, bridge
        # Transient observations, not another canonical Work store. Dropped or
        # reopened observations cannot count as delivered content.
        self.reads = []
        self.requests = {}
        self.attempts, self.calls, self.seen_calls = {}, {}, set()
        self.binding_generation = None
        self.binding_overflow = False

    def __getattr__(self, name):
        return getattr(self.previous, name)

    def remember_work(self, receipt, tool_input):
        """Only the actual AppControl bridge result may enter this ledger."""
        args = tool_input.get('args', {})
        if tool_input.get('operation') != 'dispatch' or args.get('action') not in WORK_READS:
            return receipt
        if not isinstance(receipt, dict) or receipt.get('accepted') is not True:
            return receipt
        receipt = copy.deepcopy(receipt)
        # Core's hook JSON roundtrip can change a binary float by one ULP.
        # These observation-only timestamps have a lossless decimal transport
        # presentation; compare the entire returned receipt exactly thereafter.
        # The canonical service records and all content/authority fields stay
        # unchanged. Never fuzzy-match timestamps, body text, hashes or IDs.
        result = receipt.get('result', {})
        if args['action'] == 'workspace.work.invocation' and type(result.get('observedAt')) is float:
            result['observedAt'] = repr(result['observedAt'])
        source = result.get('source', {})
        if args['action'] == 'workspace.work.source' and type(source.get('capturedAt')) is float:
            source['capturedAt'] = repr(source['capturedAt'])
        size = len(json.dumps(receipt, ensure_ascii=False).encode())
        if size > WORK_BYTES:
            return receipt  # Unbounded/raw output cannot establish delivery.
        self.reads.append({'receipt': copy.deepcopy(receipt), 'input': copy.deepcopy(tool_input),
                           'tool_call_id': None, 'bytes': size})
        while len(self.reads) > MAX_READS or sum(row['bytes'] for row in self.reads) > READ_BYTES:
            self.reads.pop(0)
        return receipt

    def bind_work(self, data):
        from .output_image_delivery import successful_output
        # Nested execution success alone does not prove that tool_exec printed
        # the page. It also cannot authorize an effect inside that same batch.
        if (data.get('tool_name') != 'app_control' or not data.get('tool_call_id')
                or data.get('parent_tool_call_id') or data.get('dispatch_source')):
            return
        matches = [row for row in self.reads if row['tool_call_id'] is None
                   and row['input'] == data.get('tool_input')
                   and row['receipt'] == successful_output(data.get('result'))]
        if len(matches) == 1:
            matches[0]['tool_call_id'] = data['tool_call_id']

    def _request(self, request):
        observations = [m for m in request.messages if (m.metadata or {}).get('workRequestObservation')]
        if len(observations) != 1:
            return None
        message = observations[0]
        saved = self.requests.get(message.metadata['workRequestObservation'])
        # A copied identifier with modified prefix data is not the same request.
        return saved if saved and message.content == saved['content'] else None

    def work_request(self, request):
        return self._request(request)

    def begin_work(self, request):
        saved = self._request(request)
        if not saved or saved['required']['status'] != 'available':
            return None
        required = saved['required']
        generation = (required['rootSessionId'], required['generationId'])
        if generation != self.binding_generation:
            self.attempts.clear()
            self.calls.clear()
            self.seen_calls.clear()
            self.binding_overflow = False
            self.binding_generation = generation
        identity = uuid.uuid4().hex
        attempt = {'id': identity, 'required': copy.deepcopy(required),
                   'coverage': self.request_coverage(request), 'status': 'sending',
                   'requestSha256': hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                   'transport': 'host_complete_boundary', 'responses': set(), 'native': False}
        self.attempts[identity] = attempt
        while len(self.attempts) > 8:
            expired = next(iter(self.attempts))
            self.attempts.pop(expired)
            self.calls = {key: row for key, row in self.calls.items() if row['attemptId'] != expired}
        return attempt

    def _bind_call(self, attempt, identity, name, arguments, *, response=None):
        if self.binding_overflow or attempt['id'] not in self.attempts:
            return
        if not _text(identity) or not _text(name) or not isinstance(arguments, dict):
            return
        fingerprint = hashlib.sha256(json.dumps([name, arguments], sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest()
        existing = self.calls.get(identity)
        row = {'attemptId': attempt['id'], 'name': name, 'argsSha256': fingerprint,
               'responseId': response}
        if identity in self.seen_calls:
            # Repeated native terminal reporting is the same observation. Any
            # competing attempt/content invalidates the call for both requests.
            if existing != row:
                self.calls.pop(identity, None)
            return
        self.seen_calls.add(identity)
        if len(self.seen_calls) > 4096 or len(self.calls) >= 128:
            self.binding_overflow = True
            self.calls.clear()
            return
        self.calls[identity] = row

    def finish_work(self, attempt, response=None, *, failed=False, streaming=False):
        if attempt is None:
            # Unbound/failed-projection calls still consume their output IDs.
            # They must not inherit an earlier covered call with the same ID.
            value = response.model_dump() if hasattr(response, 'model_dump') else response
            for block in (value.get('content') or []) if isinstance(value, dict) else []:
                if isinstance(block, dict) and block.get('type') == 'tool_call' and _text(block.get('id')):
                    self.calls.pop(block['id'], None)
                    if len(self.seen_calls) < 4096:
                        self.seen_calls.add(block['id'])
                    else:
                        self.binding_overflow = True
                        self.calls.clear()
            return
        if failed:
            attempt['status'] = 'unknown'
            self.calls = {key: row for key, row in self.calls.items() if row['attemptId'] != attempt['id']}
            return
        attempt['status'] = 'returned'
        if attempt['native'] or streaming:
            # Native calls may already have executed; never assign the final
            # aggregate response identity to all returned tool calls.
            if streaming:
                attempt['transport'] = 'streaming_origin_unavailable'
            return
        value = response.model_dump() if hasattr(response, 'model_dump') else response
        if not isinstance(value, dict):
            return
        for block in value.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'tool_call':
                self._bind_call(attempt, block.get('id'), block.get('name'), block.get('input'))

    def native_work_event(self, attempt, kind, value):
        """Observe existing callbacks without claiming a nonexistent send proof."""
        if attempt is None or attempt['id'] not in self.attempts:
            return
        attempt['native'] = True
        attempt['transport'] = 'native_immutable_send_proof_unavailable'
        if kind == 'native.send':
            attempt['outboundSha256'] = hashlib.sha256(json.dumps(
                value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            # trace() precedes an awaited lifecycle callback and socket.send.
            # Its mutable object cannot be promoted to an immutable send receipt.
            return
        if kind != 'native.receive' or not isinstance(value, dict):
            return
        event = value.get('type')
        if event == 'response.created':
            identity = value.get('response', {}).get('id')
            if _text(identity) and len(attempt['responses']) < 128:
                attempt['responses'].add(identity)
            return
        terminal = value.get('response', {})
        response = terminal.get('id') if event in {'response.completed', 'response.done'} else value.get('response_id')
        if response not in attempt['responses']:
            return  # A mutable "latest response" is not enough attribution.
        items = terminal.get('output', []) if terminal else [value.get('item')]
        for item in items:
            if not isinstance(item, dict) or item.get('type') != 'function_call':
                continue
            try:
                arguments = json.loads(item['arguments'])
            except (KeyError, TypeError, ValueError):
                continue
            self._bind_call(attempt, item.get('call_id'), item.get('name'), arguments, response=response)

    def effect_coverage(self, data):
        """Private adapter predicate, NOT an execution grant or action policy.

        Backend applicability/current permission must be checked independently.
        Nested provenance is not qualified by this direct-call implementation.
        """
        unavailable = {'complete': False, 'effectAuthorized': False, 'status': 'unavailable'}
        if data.get('parent_tool_call_id') or data.get('dispatch_source'):
            return {**unavailable, 'reason': 'Nested causal delivery is not qualified.'}
        row = self.calls.get(data.get('tool_call_id'))
        if row is None:
            return {**unavailable, 'reason': 'No unambiguous originating request.'}
        attempt = self.attempts.get(row['attemptId'])
        try:
            fingerprint = hashlib.sha256(json.dumps([data.get('tool_name'), data.get('tool_input')],
                                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        except (TypeError, ValueError):
            return {**unavailable, 'reason': 'Tool arguments are not exact JSON.'}
        if (not attempt or attempt['status'] != 'returned'
                or row['argsSha256'] != fingerprint or attempt['native']
                or attempt['transport'] != 'host_complete_boundary'):
            return {**unavailable, 'reason': 'Origin, transport or exact tool arguments are unavailable.'}
        return {**copy.deepcopy(attempt['coverage']), 'attemptId': attempt['id'],
                'requestSha256': attempt['requestSha256'], 'effectAuthorized': False}

    async def recheck_effect_coverage(self, data):
        """Host-only observation seam for the existing effect-policy owner."""
        before = self.effect_coverage(data)
        if before.get('complete') is not True:
            return before
        attempt = self.attempts[before['attemptId']]
        try:
            current = await asyncio.wait_for(self.bridge('work.invocation.revalidate',
                                                        {'version': before['version']}), 1.5)
            if (current.get('current') is not True or current.get('effectAuthorized') is not False
                    or current.get('required') != attempt['required']
                    or self.effect_coverage(data) != before):
                raise ValueError('Work origin or authority changed during effect check.')
        except Exception:
            return {'status': 'unavailable', 'complete': False, 'effectAuthorized': False,
                    'reason': 'Work currentness or originating request became unavailable.'}
        return before

    def request_coverage(self, request):
        """Inspect exact fitted host bytes; this is NOT native/effect authority.

        The native adapter can replace history with opaque checkpoint content.
        Effect admission additionally needs an authenticated causal request
        binding and supported actual-transport proof from that adapter.
        """
        from .output_image_delivery import pair_retained
        saved = self._request(request)
        result = {'status': 'unavailable', 'complete': False, 'effectAuthorized': False,
                  'scope': 'fitted_host_request_only', 'reason': 'No bound available Work observation.'}
        if not saved or saved['required']['status'] != 'available':
            return result
        required = saved['required']
        result.update(version=required['version'], rootSessionId=required['rootSessionId'],
                      generationId=required['generationId'], inputIds=required['inputIds'],
                      requirementCount=required['requirementCount'], coveredCount=0)
        retained = [row for row in self.reads if pair_retained(request, row)]
        try:
            requirements, cursor, visited = [], None, set()
            while True:
                pages = []
                for row in retained:
                    action = row['input']['args']
                    args, page = action.get('args', {}), row['receipt'].get('result', {})
                    if (action['action'] == 'workspace.work.invocation'
                            and args.get('after') == cursor and page.get('version') == required['version']
                            and all(page.get(k) == required.get(k) for k in
                                    ('rootSessionId', 'generationId', 'inputIds', 'workCount', 'requirementCount', 'coverage'))):
                        pages.append(page)
                # Duplicate genuine reads are harmless only when their content
                # page agrees. observedAt is not content or authority.
                pages = {json.dumps({k: p.get(k) for k in ('items', 'truncated', 'nextAfter')},
                                    sort_keys=True, ensure_ascii=False): p for p in pages}
                if len(pages) != 1:
                    raise ValueError('Required manifest page is absent or ambiguous in fitted content.')
                page = next(iter(pages.values()))
                items = page.get('items')
                if not isinstance(items, list) or len(items) > 20:
                    raise ValueError('Invalid required manifest page.')
                requirements.extend(items)
                if len(requirements) > required['requirementCount']:
                    raise ValueError('Required manifest count changed.')
                if page.get('truncated') is False and page.get('nextAfter') is None:
                    break
                cursor = page.get('nextAfter')
                if not items or not _text(cursor, 4096) or cursor in visited:
                    raise ValueError('Required manifest cannot progress.')
                visited.add(cursor)
            if len(requirements) != required['requirementCount']:
                raise ValueError('Not every required manifest page is retained.')
            identities = set()
            for requirement in requirements:
                ref = requirement['readRef']
                identity = json.dumps(ref, sort_keys=True)
                if identity in identities or not _digest(requirement['contentSha256']):
                    raise ValueError('Required content identity is invalid or duplicated.')
                identities.add(identity)
                offset, total, pieces = 0, None, []
                while True:
                    pages = {}
                    for row in retained:
                        call = row['input']['args']
                        args = call.get('args', {})
                        if (call['action'] != ref['action'] or args.get('offset', 0) != offset
                                or any(args.get(k) != v for k, v in ref['args'].items())):
                            continue
                        page = row['receipt']['result']
                        if requirement['kind'] == 'source':
                            page = page.get('source', {})
                        pages[json.dumps(page, sort_keys=True, ensure_ascii=False)] = page
                    if len(pages) != 1:
                        raise ValueError('Required body page is absent or ambiguous in fitted content.')
                    page = next(iter(pages.values()))
                    if (page.get('offset') != offset or not isinstance(page.get('text'), str)
                            or not _integer(page.get('total')) or page.get('sha256') != requirement['contentSha256']
                            or total is not None and page['total'] != total):
                        raise ValueError('Required body page identity changed.')
                    total = page['total']
                    text = page['text']
                    end = offset + len(text)
                    if (end > total or page.get('truncated') != (end < total)
                            or page.get('nextOffset') != (end if end < total else None)):
                        raise ValueError('Required body page is not contiguous.')
                    pieces.append(text)
                    if end == total:
                        break
                    if end <= offset:
                        raise ValueError('Required body page cannot progress.')
                    offset = end
                if hashlib.sha256(''.join(pieces).encode()).hexdigest() != requirement['contentSha256']:
                    raise ValueError('Required body bytes do not match the full content hash.')
                result['coveredCount'] += 1
            result.update(status='available', complete=True, reason=None)
        except (ValueError, TypeError, KeyError) as exc:
            result['reason'] = str(exc)
        return result

    async def revalidate(self, request):
        if hasattr(self.previous, 'revalidate'):
            request = await self.previous.revalidate(request)
        return request

    async def finalize_work(self, request, *, fitted, binding):
        """Last awaited check after every outer wrapper, before commit/send."""
        if not binding or binding['required']['status'] != 'available':
            return request  # Unavailable scope is not a universal deny or grant.
        saved = self._request(request)
        before = request.model_dump_json()
        if saved is not binding or before != fitted:
            raise ValueError('Work request bytes changed after fitting; refit before transport.')
        try:
            check = await asyncio.wait_for(self.bridge('work.invocation.revalidate',
                                                      {'version': saved['required']['version']}), 1.5)
            if (check.get('current') is not True or check.get('effectAuthorized') is not False
                    or check.get('required') != saved['required']):
                raise ValueError('Required Work authority changed.')
        except Exception as exc:
            raise ValueError('Work request changed or currentness is unavailable; refit before transport.') from exc
        if request.model_dump_json() != before or self._request(request) is not saved:
            raise ValueError('Work request changed while checking currentness; refit before transport.')
        # Diagnostic evidence only. It cannot authorize a later tool without
        # the adapter-owned actual-transport/request-to-effect causal binding.
        saved['coverage'] = self.request_coverage(request)
        return request

    async def prepare(self, request, provider, *, commit=False):
        from amplifier_core.message_models import Message
        from .execution_events import CALL_PURPOSE
        request = await self.previous.prepare(request, provider, commit=commit)
        if CALL_PURPOSE.get() or not any(getattr(t, 'name', None) == 'app_control' for t in request.tools or []):
            return request
        try:
            current = await asyncio.wait_for(self.bridge('coordination.current', {}), 1.5)
            if not isinstance(current, dict):
                raise ValueError('Coordination projection is unavailable')
        except Exception:
            current = {'status': 'unavailable', 'authority': 'unknown; do not infer a grant or absence of peers'}
        current = {**current, 'work': _work_reference(current.get('work'))}
        identity = uuid.uuid4().hex
        content = 'Current coordination reference state (not new authority):\n' + json.dumps(current, ensure_ascii=False)
        self.requests[identity] = {'content': content, 'required': copy.deepcopy(current['work']['required'])}
        while len(self.requests) > 4:
            self.requests.pop(next(iter(self.requests)))
        return request.model_copy(update={'messages': [
            *[message for message in request.messages if not (message.metadata or {}).get('coordinationObservation')],
            Message(role='user', content=content,
                    metadata={'ephemeral': True, 'coordinationObservation': True, 'workRequestObservation': identity})]})

    def commit(self, request):
        self.previous.commit(request.model_copy(update={'messages': [message for message in request.messages
            if not (message.metadata or {}).get('coordinationObservation')]}))
