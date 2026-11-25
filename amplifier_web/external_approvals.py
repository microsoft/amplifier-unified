"""Current ACP permission requests, with no local remembered-policy store."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
import unicodedata
import uuid

from .approval_policy import NAMESPACE
from .errors import AppError


CANCELLED = {'outcome': {'outcome': 'cancelled'}}


class ExternalApprovals:
    def __init__(self, owner):
        self.owner, self.service = owner, owner.service
        self.pending = {}

    def _current(self, session, agent_id, turn, remote):
        from .shared_conversation import is_shared

        if self.owner.closing or turn.participant.session_id != remote:
            return False
        try:
            binding, _, _ = self.owner.selected(session, agent_id)
        except (AppError, ValueError):
            return False
        if binding.get('sessionId') != remote:
            return False
        if is_shared(session):
            shared = self.owner.shared
            return (shared.turns.get((session['id'], agent_id)) is turn
                    and turn.speaking and shared._valid(session, turn))
        return (self.owner.turns.get(session['id']) is turn and turn.agent_id == agent_id
                and turn.dispatched and not turn.stop
                and session.get('interruptionRevision', 0) == turn.revision)

    @staticmethod
    def _stopped(session, turn):
        return turn.stop or session.get('interruptionRevision', 0) != turn.revision

    async def request(self, sid, agent_id, turn, tool_call, options):
        tool = tool_call.model_dump(mode='json', by_alias=True, exclude_none=True)
        choices = [option.model_dump(mode='json', by_alias=True, exclude_none=True) for option in options]
        identifiers = [row['optionId'] for row in choices]
        if not choices or len(set(identifiers)) != len(identifiers):
            return copy.deepcopy(CANCELLED)
        remote = turn.participant.session_id
        identity = 'external:' + str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        try:
            async with self.service.lock:
                session = self.service._session(sid)
                if not self._current(session, agent_id, turn, remote):
                    if self.owner.closing or self._stopped(session, turn):
                        # A stopped RPC is cancellation, not a refusal that an
                        # agent may treat as permission to try another action.
                        raise asyncio.CancelledError()
                    return copy.deepcopy(CANCELLED)
                self.expire(session, turn=turn, tool_call_id=tool['toolCallId'])
                if self.service.access_mode.full(session):
                    # Host Full access authorizes this action, never a new
                    # remembered rule in another agent's policy store.
                    once = next((row for row in choices if row['kind'] == 'allow_once'), None)
                    if once:
                        return {'outcome': {'outcome': 'selected', 'optionId': once['optionId']}}
                binding, _, _ = self.owner.selected(session, agent_id)
                card = {'id': identity, 'source': 'external', 'status': 'pending',
                        'agentId': agent_id, 'agentLabel': binding['label'],
                        'title': tool.get('title') or 'Permission requested',
                        'prompt': tool.get('title') or 'Review this agent’s requested action.',
                        'options': choices, 'toolCall': tool, 'createdAt': time.time()}
                if 'rawInput' in tool:
                    card['details'] = tool['rawInput']
                metadata = tool.get('_meta')
                scope = metadata.get(NAMESPACE) if isinstance(metadata, dict) else None
                scope = scope if isinstance(scope, dict) else {}
                description = scope.get('description')
                profile = turn.participant.initialization_metadata.get(NAMESPACE)
                profile = profile if isinstance(profile, dict) else {}
                if (type(profile.get('version')) is int and profile['version'] == 1
                        and profile.get('owner') == 'instance' and profile.get('reusableRequests') is True
                        and profile.get('producer') == scope.get('producer')
                        and type(scope.get('version')) is int and scope['version'] == 1
                        and isinstance(scope.get('producer'), str) and scope['producer']
                        and isinstance(scope.get('key'), str) and len(scope['key']) == 64
                        and all(c in '0123456789abcdef' for c in scope['key'])
                        and scope.get('authorityLifetime') in {'request', 'conversation'}
                        and any(row['optionId'] == scope.get('allowOptionId') and row['kind'] == 'allow_once' for row in choices)
                        and isinstance(description, str)
                        and len(description.encode('utf-8', errors='replace')) <= 512
                        and not any(unicodedata.category(c) == 'Cc' for c in description)):
                    card['policyDescription'] = description
                    if scope.get('authorityLifetime') in {'request', 'conversation'}:
                        card['authorityLifetime'] = scope['authorityLifetime']
                card['requestHash'] = hashlib.sha256(json.dumps(
                    {'card': card, 'sessionId': sid, 'remoteSessionId': remote,
                     'inputId': turn.input_id, 'revision': turn.revision},
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                self.pending[identity] = (sid, agent_id, turn, remote, future, card)
                session['approvals'].append(card)
                self.service._activity(session, 'approval', 'Waiting for your approval')
                self.service._publish_changes(sessions={sid})
            return await future
        finally:
            async with self.service.lock:
                current = self.pending.pop(identity, None)
                if current and current[-1]['status'] == 'pending':
                    current[-1].update(status='expired', resolutionReason='external_request_ended')
                    self.service._publish_changes(sessions={sid})

    async def decide(self, args, origin, command_id, *, admission_check, expected_revision):
        identity = command_id or str(uuid.uuid4())
        fingerprint = hashlib.sha256(json.dumps(['approval.respond', args, origin], sort_keys=True).encode()).hexdigest()
        async with self.service.lock:
            if origin not in {'ui', 'user'}:
                raise AppError('This permission request must be answered by the user.', 403)
            previous = self.service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (identity,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError('This command ID was already used with different contents.', 409)
                return {**json.loads(previous[1]), 'duplicate': True}
            if admission_check is not None:
                admission_check()
            if expected_revision is not None and expected_revision != self.service.state['revision']:
                raise AppError('The app changed. Refresh its state and retry.', 409)
            session = self.service._session(args.get('sessionId'))
            current = self.pending.get(args['id'])
            card = current[-1] if current else None
            before = copy.deepcopy(card)
            values = self.respond(session, args, origin)
            receipt = {'accepted': True, 'commandAction': 'approval.respond',
                       'result': {'id': args['id'], 'optionId': args['optionId'], 'delivery': 'selected'}}
            tasks = self.commit(session, [values], [(card, before)], identity, fingerprint, receipt)
        for task in tasks:
            await asyncio.shield(task)
        return receipt

    def commit(self, session, replies, snapshots, identity, fingerprint, receipt):
        """Save decisions, then transfer their callbacks to the host's lifetime.

        Called under the host lock. Neither the requesting browser's lifetime
        nor a failed projection can discard a committed choice. The ordinary
        command receipt distinguishes a failed write from post-commit failure;
        it is not a policy store or authority to recreate an expired callback.
        """
        try:
            self.service.db.execute('INSERT INTO commands VALUES(?,?,?)', (identity, fingerprint, json.dumps(receipt)))
            self.service._save_changes(sessions={session['id']})
        except BaseException:
            self.service.db.rollback()
            if not self.service.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone():
                for card, before in snapshots:
                    card.clear()
                    card.update(before)
                raise
        tasks = [self.service._task(self.deliver(*reply)) for reply in replies]
        try:
            self.service._publish_progress(session_ids={session['id']}, record_only=True)
        except Exception:
            logging.getLogger(__name__).warning(
                'External approval saved; display notification deferred.', exc_info=True)
        return tasks

    def commit_full(self, session, replies, snapshots):
        identity = 'external-approval:' + str(uuid.uuid4())
        fingerprint = hashlib.sha256(json.dumps(
            ['access.full.settle', session['id'], replies,
             self.service.access_mode.snapshot(session)['revision']], sort_keys=True).encode()).hexdigest()
        receipt = {'accepted': True, 'commandAction': 'access.full.settle',
                   'result': {'selected': [{'id': reply[1], 'optionId': reply[2]} for reply in replies]}}
        return self.commit(session, replies, snapshots, identity, fingerprint, receipt)

    def respond(self, session, args, origin):
        """Validate one current choice under the host lock, before saving it."""
        if origin not in {'ui', 'user'}:
            raise AppError('This permission request must be answered by the user.', 403)
        current = self.pending.get(args['id'])
        if current is None or current[0] != session['id']:
            raise AppError('This permission request is no longer pending.', 409)
        _, agent_id, turn, remote, future, card = current
        if (card['status'] != 'pending' or future.done()
                or not self._current(session, agent_id, turn, remote)):
            raise AppError('This permission request is no longer pending.', 409)
        if args.get('expectedRequestHash') != card['requestHash']:
            raise AppError('The request changed. Review it before deciding.', 409)
        choice = next((row for row in card['options'] if row['optionId'] == args.get('optionId')), None)
        if choice is None:
            raise AppError('Choose an option offered by this agent.', 409)
        decision = 'allow' if choice['kind'].startswith('allow_') else 'deny'
        expected = 'allow' if args['decision'] in {'allow', 'approve'} else 'deny'
        if decision != expected or args.get('comment'):
            raise AppError('This agent requires its exact offered choice, without a separate note.', 409)
        card.update(status=decision, selectedOptionId=choice['optionId'], selectedOptionKind=choice['kind'])
        # Dispatch publishes the durable selected choice before waking the
        # callback. This records a decision, not proof of an executed effect.
        return session['id'], args['id'], choice['optionId']

    def allow_once(self, session, card):
        current = self.pending.get(card['id'])
        if current is None or current[-1] is not card or current[0] != session['id']:
            return None
        _, agent_id, turn, remote, future, _ = current
        if (card['status'] != 'pending' or future.done()
                or not self._current(session, agent_id, turn, remote)):
            return None
        choice = next((row for row in card['options'] if row['kind'] == 'allow_once'), None)
        if choice is None:
            return None
        card.update(status='allow', selectedOptionId=choice['optionId'], selectedOptionKind='allow_once',
                    approvedBy='full-access', accessRevision=self.service.access_mode.snapshot(session)['revision'])
        return session['id'], card['id'], choice['optionId']

    async def deliver(self, sid, identity, option_id):
        async with self.service.lock:
            current = self.pending.get(identity)
            if current is None or current[0] != sid:
                return
            _, agent_id, turn, remote, future, card = current
            if future.done():
                return
            session = self.service._session(sid)
            if (not self._current(session, agent_id, turn, remote)
                    or card.get('approvedBy') == 'full-access'
                    and not self.service.access_mode.full(session, card['accessRevision'])):
                card.update(status='expired', resolutionReason='external_request_ended')
                if not self.owner.closing and not self._stopped(session, turn):
                    future.set_result(copy.deepcopy(CANCELLED))
                self.service._publish_changes(sessions={sid})
                return
            future.set_result({'outcome': {'outcome': 'selected', 'optionId': option_id}})

    def expire(self, session, *, turn=None, tool_call_id=None, resolve=True, terminal=False):
        """End callbacks when their owning turn/request ends; never recreate one."""
        for sid, _, current, _, future, card in list(self.pending.values()):
            if (sid != session['id'] or turn is not None and current is not turn
                    or tool_call_id is not None and card['toolCall'].get('toolCallId') != tool_call_id):
                continue
            if not future.done():
                card.update(status='expired', resolutionReason='external_request_ended')
                # Stop sends session/cancel first. Its remote cancellation (or
                # bounded transport retirement) owns the pending RPC lifetime;
                # do not wake a waiting agent with an ordinary refusal first.
                if terminal:
                    # A terminal turn cannot leave an incoming permission RPC
                    # resident on a reusable ACP connection. Cancel that RPC,
                    # without manufacturing an ordinary permission refusal.
                    future.cancel()
                elif resolve and not self.owner.closing and not self._stopped(session, current):
                    future.set_result(copy.deepcopy(CANCELLED))

    def observe(self, session, turn, update):
        identity = update.get('toolCallId')
        for sid, _, current, _, _, card in list(self.pending.values()):
            if sid != session['id'] or current is not turn or card['toolCall'].get('toolCallId') != identity:
                continue
            changed = any(key in update and update[key] != card['toolCall'].get(key)
                          for key in ('rawInput', 'name', 'kind', 'locations', '_meta', 'title', 'content'))
            if changed or update.get('status') in {'completed', 'failed'}:
                self.expire(session, turn=turn, tool_call_id=identity)

    @staticmethod
    def restore(session):
        for card in session.get('approvals', []):
            if card.get('source') == 'external' and card.get('status') == 'pending':
                card.update(status='expired', resolutionReason='host_restarted')
