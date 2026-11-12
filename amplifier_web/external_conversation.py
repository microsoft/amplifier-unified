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


def is_external(session):
    return isinstance(session.get('externalAgent'), dict)


def source_identity(selected):
    source = {key: value for key, value in selected.items() if key != 'label'}
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass
class Turn:
    input_id: str
    revision: int
    participant: ACPParticipant
    task: asyncio.Task | None = None
    dispatched: bool = False
    stop: bool = False


class ExternalConversations:
    def __init__(self, service):
        self.service = service
        self.participants = {}
        self.turns = {}
        self.closing = False

    def create(self, args, workspace):
        if args.get('location', {}).get('kind') == 'managed' or any(
                args.get(key) for key in ('bundle', 'selection', 'inheritConfiguration', 'purpose')):
            raise AppError('External chats use an existing workspace and their agent’s own configuration.')
        selected = endpoint(getattr(self.service, 'server_config', {}), args['externalAgentId'])
        now = time.time()
        return self.service.cold_display.record({
            'id': str(uuid.uuid4()), 'title': args.get('title') or 'New chat',
            'titleSource': 'manual' if args.get('title') else 'automatic',
            'workspace': workspace, 'bundle': None, 'status': 'idle',
            'createdAt': now, 'recentActivityAt': now,
            'messages': [], 'workers': [], 'approvals': [],
            'sessionKind': 'root', 'workingDirectory': workspace,
            'executionRevision': 0, 'interruptionRevision': 0,
            'externalAgent': {'id': selected['id'], 'label': selected['label'], 'status': 'idle',
                              'capabilities': {'text': True, 'stop': True, 'nativeControls': False}},
            '_externalAgentSource': {'fingerprint': source_identity(selected),
                                     'cwd': selected.get('sessionCwd', workspace)},
        })

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
            try:
                selected = endpoint(getattr(self.service, 'server_config', {}), session['externalAgent']['id'])
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            if source_identity(selected) != session['_externalAgentSource']['fingerprint']:
                raise AppError('The selected external endpoint changed. The saved execution binding was preserved.', 409)
            if args.get('attachmentIds') or args.get('document') or args.get('replyId') or args.get('expectedGenerationId'):
                raise AppError('This external participant currently accepts ordinary text messages only.', 409)
            if (self.closing or session['id'] in self.turns
                    or not admitted and session.get('status') in {'starting', 'working', 'stopping'}):
                raise AppError('The external participant is still working. Stop it or wait before sending.', 409)

    async def send(self, session, text, input_id, preserve_draft=False):
        sid = session['id']
        async with self.service.lock:
            current = self.service._session(sid)
            if current.get('interruptionRevision', 0) != session.get('interruptionRevision', 0):
                self.service._delivery(current, input_id, 'failed')
                if current['externalAgent'].get('activeInputId') == input_id:
                    current['externalAgent'].update(status='stopped')
                    current['externalAgent'].pop('activeInputId', None)
                    finish(current, 'stopped')
                self.service._publish_changes(sessions={sid})
                return {'delivery': 'failed', 'inputId': input_id}
            self.guard(current, 'conversation.send', {}, admitted=True)
            selected = endpoint(getattr(self.service, 'server_config', {}), current['externalAgent']['id'])
            if source_identity(selected) != current['_externalAgentSource']['fingerprint']:
                raise AppError('The selected external endpoint changed. The saved execution binding was preserved.', 409)
            participant = self.participants.get(sid)
            if participant is None:
                participant = ACPParticipant(selected, current['_externalAgentSource']['cwd'],
                    update=lambda update: self._update(sid, update),
                    permission=lambda options: self._permission(sid, options))
                self.participants[sid] = participant
            turn = Turn(input_id, current.get('interruptionRevision', 0), participant)
            self.turns[sid] = turn
            current['externalAgent'].update(status='connecting', activeInputId=input_id)
            original = next(row for row in current['messages'] if row.get('inputId') == input_id and row['role'] == 'user')
            self.service._clear_sent_draft(current, original, text, preserve_draft)
            self.service._publish_changes(sessions={sid}, globals={'view'})
            turn.task = self.service._task(self._run(sid, text, turn))
        # The host has accepted the message; ACP has no early prompt acknowledgement.
        return {'delivery': 'sending', 'inputId': input_id}

    async def _binding(self, sid, value):
        async with self.service.lock:
            session = self.service._session(sid)
            session['externalAgent'].update(value)
            self.service._publish_changes(sessions={sid})

    async def _run(self, sid, text, turn):
        try:
            session = self.service._session(sid)
            await asyncio.wait_for(turn.participant.connect(
                dict(session['externalAgent']), lambda value: self._binding(sid, value)), 30)
            async with self.service.lock:
                session = self.service._session(sid)
                if turn.stop or session.get('interruptionRevision', 0) != turn.revision:
                    raise asyncio.CancelledError()
                session['externalAgent']['status'] = 'working'
                self.service._publish_changes(sessions={sid})
                turn.dispatched = True
            response = await turn.participant.prompt(text)
            await self._settle(sid, turn, 'accepted',
                               'stopped' if response.stop_reason == 'cancelled' else 'idle',
                               stop_reason=response.stop_reason)
        except asyncio.CancelledError:
            await self._settle(sid, turn, 'unknown' if turn.dispatched else 'failed',
                               'unknown' if turn.dispatched else 'stopped')
            raise
        except Exception:
            await self._settle(sid, turn, 'unknown' if turn.dispatched else 'failed',
                               'unknown' if turn.dispatched else 'error',
                               error=('External delivery could not be confirmed. Work was not replayed.'
                                      if turn.dispatched else 'The external agent could not connect or resume. Its binding was preserved.'))
            await turn.participant.close()
        finally:
            if self.turns.get(sid) is turn:
                self.turns.pop(sid)

    async def _update(self, sid, update):
        turn = self.turns.get(sid)
        if turn is None or not turn.dispatched:
            return
        async with self.service.lock:
            session = self.service._session(sid)
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

    async def _permission(self, sid, options):
        async with self.service.lock:
            session = self.service._session(sid)
            turn = self.turns.get(sid)
            if (turn is not None and turn.dispatched and not turn.stop
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
            if session.get('streaming'):
                self.service._message(session, 'assistant', session['streaming'], inputId=turn.input_id,
                                      source='external', agentId=session['externalAgent']['id'])
            for key in ('streaming', 'streamingId', 'streamingInputId'):
                session.pop(key, None)
            session['status'] = 'error' if status == 'unknown' else status
            session['externalAgent']['status'] = status
            session['externalAgent'].pop('activeInputId', None)
            if stop_reason:
                session['externalAgent']['stopReason'] = stop_reason
            if error:
                session['error'] = error
            self.service._delivery(session, turn.input_id, delivery)
            finish(session, 'completed' if status == 'idle' else 'stopped' if status == 'stopped' else 'error')
            self.service._publish_changes(sessions={sid})

    async def stop(self, sid):
        turn = self.turns.get(sid)
        if turn is None:
            async with self.service.lock:
                session = self.service._session(sid)
                if session['externalAgent'].get('status') != 'unknown':
                    session['status'] = session['externalAgent']['status'] = 'stopped'
                self.service._publish_changes(sessions={sid})
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
            await turn.participant.close()

    def delivery(self, session, input_id):
        message = next((row for row in session['messages'] if row.get('inputId') == input_id and row['role'] == 'user'), None)
        if message is None:
            raise AppError('This input is not saved in this conversation.', 404)
        return {'sessionId': session['id'], 'inputId': input_id, 'delivery': message.get('delivery', {}).get('status', 'unknown'), 'resent': False,
                'source': {'sessionId': session['id'], 'workspace': session['workspace'],
                           'externalAgentId': session['externalAgent']['id'],
                           **({'externalSessionId': session['externalAgent']['sessionId']}
                              if session['externalAgent'].get('sessionId') else {})}}

    async def close(self):
        self.closing = True
        for sid in list(self.turns):
            await self.stop(sid)
        await asyncio.gather(*(participant.close() for participant in self.participants.values()))
        self.participants.clear()
