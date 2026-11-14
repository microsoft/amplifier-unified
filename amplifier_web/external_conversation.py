"""External ACP execution for a Unified-owned conversation and transcript."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import time
import uuid

from .acp_participant import ACPParticipant, endpoint
from .errors import AppError
from .execution import finish
from .shared_conversation import SharedConversations, is_shared


def is_external(session):
    return isinstance(session.get('externalAgent'), dict)


def bindings(session):
    """A saved singleton remains its own binding; a room owns a roster."""
    external = session['externalAgent']
    return external.get('participants', [external])


def addressed(session, identity=None):
    rows = bindings(session)
    if identity is None and len(rows) == 1:
        return rows[0]
    selected = next((row for row in rows if row['id'] == identity), None)
    if selected is None:
        raise AppError('Choose an agent in this conversation for the message.', 409)
    return selected


def shared_context(session, input_id):
    """Bounded public history, never another participant's private context."""
    if len(bindings(session)) == 1:
        return None
    messages = session['messages']
    end = next(i for i in range(len(messages) - 1, -1, -1)
               if messages[i].get('inputId') == input_id and messages[i]['role'] == 'user')
    rows, remaining = [], 24000
    start = max(0, end - 32)
    for index in range(end - 1, start - 1, -1):
        message = messages[index]
        if message.get('role') not in {'user', 'assistant'} or not message.get('text'):
            continue
        text = message['text']
        if len(text) > remaining:
            break
        rows.append({key: message[key] for key in (
            'id', 'role', 'text', 'agentId', 'agentLabel', 'recipientAgentId',
            'recipientAgentLabel', 'delivery') if key in message})
        remaining -= len(text)
    if end == 0:
        return None
    return ('Shared conversation history for context only. Previous requests are not new work. '
            'Speaker labels identify other participants; answer only the current addressed message.\n'
            + json.dumps({'conversationId': session['id'], 'messages': list(reversed(rows)),
                          'earlierMessagesOmitted': end > len(rows)}, ensure_ascii=False))


def source_identity(selected):
    source = {key: value for key, value in selected.items() if key != 'label'}
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass
class Turn:
    input_id: str
    revision: int
    participant: ACPParticipant
    agent_id: str
    task: asyncio.Task | None = None
    dispatched: bool = False
    stop: bool = False


class ExternalConversations:
    def __init__(self, service):
        self.service = service
        self.participants = {}
        self.turns = {}
        self.closing = False
        self.shared = SharedConversations(self)

    def create(self, args, workspace):
        if args.get('location', {}).get('kind') == 'managed' or any(
                args.get(key) for key in ('bundle', 'selection', 'inheritConfiguration', 'purpose')):
            raise AppError('External chats use an existing workspace and their agent’s own configuration.')
        if args.get('externalAgentId') and args.get('externalAgentIds'):
            raise AppError('Choose one external agent selection, not both forms.')
        identities = args.get('externalAgentIds') or [args['externalAgentId']]
        if len(set(identities)) != len(identities):
            raise AppError('Choose each external agent only once.')
        selected = [endpoint(getattr(self.service, 'server_config', {}), identity) for identity in identities]
        rows = [{'id': row['id'], 'label': row['label'], 'status': 'idle',
                 'capabilities': {'text': True, 'stop': True, 'nativeControls': False}} for row in selected]
        sources = {row['id']: {'fingerprint': source_identity(row),
                              'cwd': row.get('sessionCwd', workspace)} for row in selected}
        external = rows[0] if len(rows) == 1 else {
            'label': ', '.join(row['label'] for row in rows), 'status': 'idle',
            'participants': rows, 'participation': 'shared', 'capabilities': {'text': True, 'stop': True, 'nativeControls': False}}
        now = time.time()
        return self.service.cold_display.record({
            'id': str(uuid.uuid4()), 'title': args.get('title') or 'New chat',
            'titleSource': 'manual' if args.get('title') else 'automatic',
            'workspace': workspace, 'bundle': None, 'status': 'idle',
            'createdAt': now, 'recentActivityAt': now,
            'messages': [], 'workers': [], 'approvals': [],
            'sessionKind': 'root', 'workingDirectory': workspace,
            'executionRevision': 0, 'interruptionRevision': 0,
            'externalAgent': external,
            '_externalAgentSource': sources[rows[0]['id']] if len(rows) == 1 else {'participants': sources},
        })

    def selected(self, session, identity=None):
        binding = addressed(session, identity)
        try:
            selected = endpoint(getattr(self.service, 'server_config', {}), binding['id'])
        except ValueError as exc:
            raise AppError(str(exc), 409) from None
        sources = session['_externalAgentSource']
        source = sources['participants'][binding['id']] if 'participants' in sources else sources
        if source_identity(selected) != source['fingerprint']:
            raise AppError('The selected external endpoint changed. The saved execution binding was preserved.', 409)
        return binding, selected, source

    def capture_recipient(self, session, message, identity=None):
        if is_shared(session):
            return self.shared.capture(session, message, identity)
        binding = addressed(session, identity)
        message.update(recipientAgentId=binding['id'], recipientAgentLabel=binding['label'])

    def admit(self, session, message):
        if is_shared(session):
            return
        binding = addressed(session, message['recipientAgentId'])
        session['externalAgent'].update(activeInputId=message['inputId'], activeAgentId=binding['id'])

    def restore(self, session):
        if is_shared(session):
            return self.shared.restore(session)
        external = session.get('externalAgent', {})
        if external.get('activeInputId'):
            binding = addressed(session, external.get('activeAgentId'))
            binding['status'] = external['status'] = 'unknown'
            self.service._delivery(session, external.pop('activeInputId'), 'unknown')
            external.pop('activeAgentId', None)

    def guard(self, session, action, args, *, admitted=False):
        if not is_external(session):
            return
        if (action == 'call.start' or action == 'worker.spawn'
                or action.startswith('worker.') and args.get('sessionId') == session['id']
                or action in {'runtime.control', 'configuration.inspect', 'configuration.apply',
                              'bundle.switch', 'bundle.preview', 'bundle.fork', 'bundle.save', 'bundle.export'}
                or action in {'conversation.configure', 'conversation.retry', 'message.edit',
                              'session.fork', 'session.recover', 'session.takeover', 'history.export'}
                or action == 'session.naming' and args.get('regenerate')):
            raise AppError('This operation is not supported by the external ACP participant.', 409)
        if action == 'conversation.send':
            if not is_shared(session):
                self.selected(session, args.get('recipientAgentId'))
            if args.get('attachmentIds') or args.get('document') or args.get('replyId') or args.get('expectedGenerationId'):
                raise AppError('This external participant currently accepts ordinary text messages only.', 409)
            if is_shared(session):
                return self.shared.guard(session, args)
            if not admitted:
                from .message_delivery import find_message
                drain = self.service.state.get('updates', {}).get('drain') or {}
                for sid, input_id in drain.get('messages', []):
                    if sid == session['id']:
                        message = find_message(session, input_id)
                        if message and message.get('delivery', {}).get('status') == 'queued_for_update':
                            raise AppError('An input is already saved for this external chat while the update finishes. Wait for it before sending another.', 409)
            if (self.closing or session['id'] in self.turns
                    or not admitted and session.get('status') in {'starting', 'working', 'stopping'}):
                raise AppError('The external participant is still working. Stop it or wait before sending.', 409)

    async def send(self, session, text, input_id, preserve_draft=False):
        if is_shared(session):
            return await self.shared.send(session, input_id, preserve_draft)
        sid = session['id']
        async with self.service.lock:
            current = self.service._session(sid)
            original = next(row for row in current['messages'] if row.get('inputId') == input_id and row['role'] == 'user')
            identity = original.get('recipientAgentId')
            if current.get('interruptionRevision', 0) != session.get('interruptionRevision', 0):
                self.service._delivery(current, input_id, 'failed')
                if current['externalAgent'].get('activeInputId') == input_id:
                    current['externalAgent'].update(status='stopped')
                    current['externalAgent'].pop('activeInputId', None)
                    current['externalAgent'].pop('activeAgentId', None)
                    finish(current, 'stopped')
                self.service._publish_changes(sessions={sid})
                return {'delivery': 'failed', 'inputId': input_id}
            self.guard(current, 'conversation.send', {'recipientAgentId': identity}, admitted=True)
            binding, selected, source = self.selected(current, identity)
            key = (sid, binding['id'])
            participant = self.participants.get(key)
            if participant is None:
                participant = ACPParticipant(selected, source['cwd'],
                    update=lambda update: self._update(sid, binding['id'], update),
                    permission=lambda options: self._permission(sid, binding['id'], options))
                self.participants[key] = participant
            turn = Turn(input_id, current.get('interruptionRevision', 0), participant, binding['id'])
            self.turns[sid] = turn
            binding['status'] = 'connecting'
            current['externalAgent'].update(status='connecting', activeInputId=input_id, activeAgentId=binding['id'])
            self.service._clear_sent_draft(current, original, text, preserve_draft)
            self.service._publish_changes(sessions={sid}, globals={'view'})
            turn.task = self.service._task(self._run(sid, text, turn))
        # The host has accepted the message; ACP has no early prompt acknowledgement.
        return {'delivery': 'sending', 'inputId': input_id}

    async def _binding(self, sid, identity, value):
        async with self.service.lock:
            session = self.service._session(sid)
            addressed(session, identity).update(value)
            self.service._publish_changes(sessions={sid})

    async def _run(self, sid, text, turn):
        try:
            session = self.service._session(sid)
            await asyncio.wait_for(turn.participant.connect(
                dict(addressed(session, turn.agent_id)), lambda value: self._binding(sid, turn.agent_id, value)), 30)
            async with self.service.lock:
                session = self.service._session(sid)
                if turn.stop or session.get('interruptionRevision', 0) != turn.revision:
                    raise asyncio.CancelledError()
                session['externalAgent']['status'] = 'working'
                addressed(session, turn.agent_id)['status'] = 'working'
                self.service._publish_changes(sessions={sid})
                turn.dispatched = True
            context = shared_context(session, turn.input_id)
            response = await turn.participant.prompt(text, context=context)
            await self._settle(sid, turn, 'accepted',
                               'stopped' if response.stop_reason == 'cancelled' else 'idle',
                               stop_reason=response.stop_reason)
        except asyncio.CancelledError:
            turn.stop = True
            await self._settle(sid, turn, 'unknown' if turn.dispatched else 'failed',
                               'unknown' if turn.dispatched else 'stopped')
            # Keep admission owned until the SDK retires this connection. A
            # settled receipt alone does not make its attach safe to reuse.
            await turn.participant.close()
            raise
        except Exception:
            turn.stop = True
            await self._settle(sid, turn, 'unknown' if turn.dispatched else 'failed',
                               'unknown' if turn.dispatched else 'error',
                               error=('External delivery could not be confirmed. Work was not replayed.'
                                      if turn.dispatched else 'The external agent could not connect or resume. Its binding was preserved.'))
            await turn.participant.close()
        finally:
            if self.turns.get(sid) is turn:
                self.turns.pop(sid)

    async def _update(self, sid, identity, update):
        turn = self.turns.get(sid)
        if turn is None or not turn.dispatched or turn.agent_id != identity:
            return
        async with self.service.lock:
            session = self.service._session(sid)
            if self.turns.get(sid) is not turn:
                return
            kind = update.get('sessionUpdate')
            if kind == 'agent_message_chunk' and update.get('content', {}).get('type') == 'text':
                session.setdefault('streamingId', str(uuid.uuid4()))
                session['streamingInputId'] = turn.input_id
                session['streaming'] = session.get('streaming', '') + update['content']['text']
            elif kind in {'tool_call', 'tool_call_update'}:
                self.service._activity(session, 'tools', update.get('title') or 'External agent is using a tool')
            else:
                return
            self.service._publish_progress(session_ids={sid}, record_only=True)

    async def _permission(self, sid, identity, options):
        async with self.service.lock:
            session = self.service._session(sid)
            turn = self.turns.get(sid)
            if (turn is not None and turn.agent_id == identity and turn.dispatched and not turn.stop
                    and session.get('interruptionRevision', 0) == turn.revision
                    and self.service.access_mode.full(session)):
                allowed = next((option for kind in ('allow_always', 'allow_once')
                                for option in options if option.kind == kind), None)
                if allowed is not None:
                    return {'outcome': {'outcome': 'selected', 'optionId': allowed.option_id}}
            # This text-only pilot has no new approval UI. Existing Full access
            # can authorize offered choices; otherwise do not invent consent.
            return {'outcome': {'outcome': 'cancelled'}}

    async def _settle(self, sid, turn, delivery, status, *, stop_reason=None, error=None):
        async with self.service.lock:
            session = self.service._session(sid)
            binding = addressed(session, turn.agent_id)
            if session.get('streaming'):
                self.service._message(session, 'assistant', session['streaming'], inputId=turn.input_id,
                                      source='external', agentId=binding['id'], agentLabel=binding['label'])
            for key in ('streaming', 'streamingId', 'streamingInputId'):
                session.pop(key, None)
            session['status'] = 'error' if status == 'unknown' else status
            session['externalAgent']['status'] = status
            binding['status'] = status
            session['externalAgent'].pop('activeInputId', None)
            session['externalAgent'].pop('activeAgentId', None)
            if stop_reason:
                session['externalAgent']['stopReason'] = stop_reason
            if error:
                session['error'] = error
            self.service._delivery(session, turn.input_id, delivery)
            finish(session, 'completed' if status == 'idle' else 'stopped' if status == 'stopped' else 'error')
            self.service._publish_changes(sessions={sid})

    async def stop(self, sid):
        if is_shared(self.service._session(sid)):
            return await self.shared.stop(sid)
        turn = self.turns.get(sid)
        if turn is None:
            async with self.service.lock:
                session = self.service._session(sid)
                if session['externalAgent'].get('status') != 'unknown':
                    session['status'] = session['externalAgent']['status'] = 'stopped'
                self.service._publish_changes(sessions={sid})
            return
        if turn.stop:
            # A terminal owner is already cancelling/retiring. Join it without
            # cancelling the SDK cleanup a second time.
            await asyncio.shield(asyncio.gather(turn.task, return_exceptions=True))
            return
        turn.stop = True
        if not turn.dispatched:
            turn.task.cancel()
            await asyncio.gather(turn.task, return_exceptions=True)
            # A task cancelled before its coroutine enters cannot run its own
            # exception/finally settlement. Stop still owns that admitted input.
            if self.turns.get(sid) is turn:
                await self._settle(sid, turn, 'failed', 'stopped')
                self.turns.pop(sid)
            return
        try:
            await asyncio.wait_for(turn.participant.cancel(), 5)
            await asyncio.wait_for(asyncio.shield(turn.task), 10)
        except asyncio.CancelledError:
            if not turn.task.cancelled():
                raise
        except Exception:
            turn.task.cancel()
            await asyncio.gather(turn.task, return_exceptions=True)

    def delivery(self, session, input_id):
        if is_shared(session):
            return self.shared.delivery(session, input_id)
        message = next((row for row in session['messages'] if row.get('inputId') == input_id and row['role'] == 'user'), None)
        if message is None:
            raise AppError('This input is not saved in this conversation.', 404)
        binding = addressed(session, message.get('recipientAgentId'))
        return {'sessionId': session['id'], 'inputId': input_id, 'delivery': message.get('delivery', {}).get('status', 'unknown'), 'resent': False,
                'source': {'sessionId': session['id'], 'workspace': session['workspace'],
                           'externalAgentId': binding['id'],
                           **({'externalSessionId': binding['sessionId']}
                              if binding.get('sessionId') else {})}}

    async def resume_pending(self):
        await self.shared.resume_pending()

    async def close(self):
        self.closing = True
        await self.shared.close()
        for sid in list(self.turns):
            await self.stop(sid)
        await asyncio.gather(*(participant.close() for participant in self.participants.values()))
        self.participants.clear()
