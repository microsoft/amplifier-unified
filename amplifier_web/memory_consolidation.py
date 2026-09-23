"""Opted-in host adaptation of portable, quote-verified consolidation."""
import asyncio
import copy
import json
import time

from amplifier_memory.consolidation import build_request, relevant, verified_candidates
from amplifier_memory.suggest import is_typed_text, verify
from amplifier_recall.personalization import Personalization
from amplifier_recall.store import digest


def human_rows(session):
    # Canonical history supplies attribution only where the host recorded it.
    # Unattributed imported/native rows are deliberately ineligible.
    return [row for row in session.get('messages', []) if row.get('role') == 'user'
            and row.get('inputOrigin') in {'ui', 'user', 'voice'}
            and not any(row.get(key) for key in ('scheduledRunId', 'questionId'))
            and row.get('delivery', {}).get('status', 'accepted') == 'accepted'
            and isinstance(row.get('text'), str) and is_typed_text(row['text'])]


def eligible(session, config):
    return (config['contribute'] and session.get('sessionKind', 'root') == 'root'
            and not session.get('parentId') and not session.get('_deleting')
            and not session.get('historyManaged')
            and session.get('status') in {'idle', 'completed'}
            and session['id'] not in config['excludedSessions']
            and not any(w.get('status') in {'starting', 'running', 'working', 'idle'}
                        for w in session.get('workers', [])))


def snapshot(session):
    # Complete newest turns only: no clipped evidence, no transcript copy on disk.
    rows, size = [], 2
    for row in reversed(human_rows(session)):
        cost = len(json.dumps(row['text'], ensure_ascii=False))+2
        if size + cost > 16000:
            continue
        rows.insert(0, row)
        size += cost
        if len(rows) == 24:
            break
    return rows, digest([(row['id'], row['text']) for row in rows])


class MemoryConsolidation:
    def __init__(self, recall):
        self.recall, self.app = recall, recall.app
        self.data = Personalization(recall.store)
        self.tasks = {}
        self.deliveries = {}

    def status(self, session):
        workspace = session.get('workspace') or ''
        return {'settings': self.data.settings(workspace), 'running': workspace in self.tasks,
                'attempts': self.data.attempts(workspace),
                'activity': self.data.activity(workspace),
                'lastContext': copy.deepcopy(self.deliveries.get(session['id'])),
                'limits': {'inputCharacters': 16000, 'outputTokens': 4096, 'memoriesPerCall': 8},
                'notice': 'Opt-in workspace memory uses the source conversation model. Each attempt can incur provider charges. Failed or interrupted source attempts are not replayed automatically.'}

    def idle(self, session):
        config = self.data.settings(session.get('workspace') or '')
        if eligible(session, config):
            self.start(session, [session['id']])

    def start(self, session, ids=None):
        workspace = session.get('workspace') or ''
        if not self.data.settings(workspace)['contribute']:
            raise ValueError('Enable contribution for this workspace before consolidating.')
        if workspace not in self.tasks:
            task = asyncio.create_task(self.run(workspace, ids))
            self.tasks[workspace] = task
            task.add_done_callback(lambda _: self.tasks.pop(workspace, None))

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    async def run(self, workspace, ids):
        async with self.app.lock:
            selected = [row['id'] for row in self.app.state['sessions'] if row.get('workspace', '') == workspace
                        and (ids is None or row['id'] in ids)]
        for sid in selected:
            identity = None
            try:
                async with self.app.lock:
                    session = copy.deepcopy(self.app._session(sid))
                    config = self.data.settings(workspace)
                    if not eligible(session, config):
                        continue
                    rows, signature = snapshot(session)
                    if not rows:
                        continue
                    known = []
                    for note in self.recall.store.list_memories([('workspace', workspace)], limit=10000)['items']:
                        if note.get('supersededBy'):
                            continue
                        try:
                            self.verified_source(note, config)
                        except (ValueError, KeyError):
                            continue
                        known.append(note)
                    # Do not silently omit known conflicts when the comparison
                    # window is full; fail visibly and ask for manual curation.
                request = build_request([row['text'] for row in rows], policy='workspace', known=known)
                if self.app.runtime is None:
                    raise ValueError('The conversation runtime is unavailable.')
                identity = self.data.claim(workspace, sid, signature, config['maxCallsPerDay'])
                if identity is None:
                    continue
                # Ordinary worker control and selected provider; no second session,
                # task controller, transcript input, model fallback, or OS timer.
                result = await self.app.runtime.control(sid, 'memory.consolidate', {'prompt': request})
                if result.get('interrupted'):
                    self.data.finish(identity, {'status':'interrupted','reason':'Foreground input took priority; no automatic replay'})
                    self.data.activity(workspace, {'status':'interrupted','reason':'Foreground input took priority'})
                    continue
                candidates = verified_candidates(result['text'], [row['text'] for row in rows], known=known)
                saved = []
                async with self.app.lock:
                    current = self.app._session(sid)
                    latest = self.data.settings(workspace)
                    if latest['revision'] != config['revision'] or not eligible(current, latest) or snapshot(current)[1] != signature:
                        raise ValueError('Source or memory settings changed before saving.')
                    with self.recall.store.atomic():
                        if any(self.recall.store.memory(n['id'])['revision'] != n['revision'] for n in known):
                            raise ValueError('Known memories changed during consolidation.')
                        for note in known:
                            self.verified_source(note, latest)
                        for candidate in candidates:
                            row = next(row for row in reversed(rows) if verify(candidate['quote'], [row['text']]))
                            source = {'sessionId': sid, 'messageId': row['id'], 'sha256': digest(row['text']),
                                      'sourceRevision': signature, 'quote': candidate['quote'], 'kind': 'attributed-user',
                                      'messageCreatedAt': row.get('createdAt', 0)}
                            superseded = [n for n in known if n['id'] in candidate['supersedes']]
                            if any(source['messageCreatedAt'] <= ((n.get('source') or {}).get('messageCreatedAt', n['updatedAt'])
                                    if n.get('provenance', {}).get('origin') == 'consolidation' else n['updatedAt']) for n in superseded):
                                raise ValueError('A correction must have newer human evidence than the reference it supersedes.')
                            note = self.data.save(workspace, sid, source, candidate, identity)
                            if note:
                                saved.append(note)
                                for prior in superseded:
                                    self.recall.store.mutate('memory.update', {'id':prior['id'], 'expectedRevision':prior['revision'],
                                        'text':prior['text'], 'supersededBy':note['id']}, command_id='supersede:'+note['id']+':'+prior['id'],
                                        provenance={'origin':'consolidation', 'sessionId':sid, 'supersedingSource':source})
                    self.data.finish(identity, {'status': 'completed', 'saved': saved,
                        'sourceRevision': signature, 'provider': result.get('provider'), 'model': result.get('model')})
                    if saved:
                        self.app._message(current, 'system', f"Memory saved {len(saved)} workspace reference(s). Review, correct, or delete them in Settings → Recall. References: " + ', '.join(n['id'] for n in saved), 'memory')
                        self.app._publish()
                    self.data.activity(workspace, {'status':'completed','saved':len(saved)})
            except asyncio.CancelledError:
                if identity:
                    self.data.finish(identity, {'status': 'interrupted', 'reason': 'Outcome unconfirmed; no automatic replay'})
                raise
            except Exception as exc:
                if identity:
                    self.data.finish(identity, {'status': 'failed', 'reason': type(exc).__name__})
                self.data.activity(workspace, {'status':'skipped' if identity is None else 'failed',
                    'reason':str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
                # A budget refusal is visible in settings; no queued retry loop.

    def verified_source(self, note, config):
        source = note.get('source') or {}
        if source.get('kind') != 'attributed-user':
            return None
        sid = source['sessionId']
        if sid in config['excludedSessions']:
            raise ValueError('The source conversation was withdrawn from memory.')
        session = next((row for row in self.app.state['sessions'] if row['id'] == sid), None)
        if session is None:
            raise ValueError('The original source conversation was removed.')
        if session.get('_deleting') or session.get('workspace') != config['workspace']:
            raise ValueError('The source conversation is unavailable in this workspace.')
        row = next((r for r in human_rows(session) if r['id'] == source['messageId']), None)
        if row is None or digest(row['text']) != source['sha256']:
            raise ValueError('The original memory evidence changed or was removed.')
        return {'text': row['text'], **source, 'verifiedAt': time.time()}

    async def context(self, sid, *, expected=None):
        async with self.app.lock:
            session = self.app._session(sid)
            config = self.data.settings(session.get('workspace') or '')
            result = {'items': [], 'enabled': config['use'], 'settingsRevision': config['revision']}
            if config['use'] and session.get('sessionKind', 'root') == 'root' and sid not in config['excludedSessions']:
                query = next((r['text'] for r in reversed(human_rows(session))), '')
                notes = self.recall.store.list_memories(self.recall.scopes(session), limit=10000)['items']
                available = []
                for note in notes:
                    if note.get('supersededBy'):
                        continue
                    try:
                        self.verified_source(note, config)
                    except (ValueError, KeyError):
                        continue
                    available.append(note)
                size = 0
                for note in relevant(available, query):
                    item = {key: note[key] for key in ('id', 'revision', 'scope', 'text', 'source', 'provenance', 'matchTerms', 'retrievalReason') if key in note}
                    # Historical quote remains available through memory.source.
                    # A corrected note must not compete with its old wording in
                    # the model request or masquerade as a verbatim source quote.
                    item['source'] = {key: value for key, value in (note.get('source') or {}).items() if key != 'quote'}
                    item['provenance'] = {key: value for key, value in note.get('provenance', {}).items()
                                          if key in {'origin','sessionId','authorizationMessageId','wording','evidence','attemptId'}}
                    size += len(json.dumps(item, ensure_ascii=False))
                    if size <= 6000:
                        result['items'].append(item)
            if expected == result:
                receipt = {'at': time.time(), 'items': [{k: n[k] for k in ('id', 'revision', 'matchTerms', 'retrievalReason')} for n in result['items']], 'settingsRevision': config['revision']}
                previous = self.deliveries.get(sid, {})
                self.deliveries[sid] = receipt
                if receipt['items'] and receipt['items'] != previous.get('items'):
                    self.app._message(session, 'system', 'Memory context supplied: ' + ', '.join(n['id'] for n in receipt['items']) + '. Inspect selection and sources in Settings → Recall.', 'memory')
                    self.app._publish()
            return result


async def generate(controls, args):
    """One tool-free call through the worker's configured and observed provider."""
    from amplifier_core.message_models import ChatRequest, Message
    from .execution_events import CALL_PURPOSE
    controls.require_idle()
    prompt = args.get('prompt')
    if not isinstance(prompt, str) or len(prompt) > 31000:
        raise ValueError('Invalid bounded memory prompt')
    coordinator = controls.coordinator
    loop = coordinator.get('orchestrator')
    providers = coordinator.get('providers') or {}
    provider = getattr(loop, 'root_provider', None) or loop._select_provider(providers)
    from .host.session import SelectedProvider
    if isinstance(provider, SelectedProvider):
        # Keep the chosen model/effort/account while constraining a larger
        # conversation output override for this auxiliary request only.
        provider = SelectedProvider(provider.original, {**provider.selection,
            'max_output_tokens': min(provider.selection.get('max_output_tokens') or 4096, 4096)},
            execution_adapter=provider.execution_adapter)
    observe = coordinator.get_capability('web.provider_observe')
    if observe:
        provider = observe(provider)
    request = ChatRequest(messages=[Message(role='user', content=prompt)], tools=[],
                          max_output_tokens=4096, timeout=60)
    token = CALL_PURPOSE.set({'label': 'Memory consolidation', 'lifecycle': 'background'})
    try:
        reply = await asyncio.wait_for(provider.complete(request), 60)
        info = provider.get_info()
        import inspect
        if inspect.isawaitable(info):
            info = await info
        text = ''.join(block.text for block in reply.content if getattr(block, 'type', None) == 'text')
        return {'text': text, 'provider': getattr(info, 'id', None),
                'model': getattr(reply, 'model', None) or getattr(info, 'defaults', {}).get('model')}
    finally:
        CALL_PURPOSE.reset(token)
