"""Optional ACP profile over existing configuration, commands and task owners.

No public identity is generated here. The operator's existing server configuration
names one continuing agent and its authorized sources. Native roots, command
receipts, task records and access policy keep their original owners.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import uuid

NAMESPACE = 'com.microsoft.amplifier.agent-instance'
METHOD = 'amplifier/instance'
BINDING = 'agent-instance'


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 500 and not any(ord(c) < 32 for c in value)


def source(value):
    if (not isinstance(value, dict) or set(value) != {'sourceId', 'conversationId'}
            or not all(_text(item) for item in value.values())):
        raise ValueError('An exact sourceId and conversationId are required.')
    return copy.deepcopy(value)


def room_source(identity, source_id=None):
    """Use the source's public origin when available; UUID fallback is local scope.

    The fallback follows the room's existing globally allocated identity, not a
    claim of cross-installation authentication or import/copy disambiguation.
    """
    return source({'sourceId': source_id or 'urn:amplifier:conversation:' + identity,
                   'conversationId': identity})


def session_metadata(agent_id, source_ref):
    return {NAMESPACE: {'agentId': agent_id, 'source': source(source_ref)}}


def prompt_metadata(agent_id, source_ref, input_id, *, revision=0, actor,
                    audience, reply_to, interruption_revision):
    return {NAMESPACE: {**session_metadata(agent_id, source_ref)[NAMESPACE],
        'input': {'id': input_id, 'revision': revision, 'actor': actor,
                  'audience': audience, 'replyTo': reply_to},
        'expectedInterruptionRevision': interruption_revision}}


def _identity(*values):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        [NAMESPACE, *values], sort_keys=True, separators=(',', ':'))))


class InstanceProfile:
    """Connection-scoped projection, not a new registry or acceptance ledger."""
    def __init__(self, owner):
        self.owner = owner
        self.selected = self.config()
        self.bindings = {}
        self.execution_sources = {}

    def config(self):
        value = getattr(self.owner.service, 'server_config', {}).get('agentInstance')
        if value is None:
            return None
        if (not isinstance(value, dict) or set(value) - {'id', 'label', 'workspace', 'bundle', 'sources'}
                or not all(_text(value.get(key)) for key in ('id', 'label', 'workspace', 'bundle'))
                or ('sources' in value and not isinstance(value['sources'], list))):
            raise ValueError('agentInstance requires id, label, workspace and bundle; sources is optional.')
        for row in value.get('sources', []):
            source(row)
        return copy.deepcopy(value)

    def applies(self):
        """An offered optional profile need not apply to every ACP launcher."""
        return (self.selected is not None and self.selected['bundle'] == self.owner.bundle
                and Path(self.selected['workspace']).resolve(strict=True) == self.owner.workspace)

    def current(self, source_ref=None):
        self.owner.require_connection()
        # Configuration was validated at negotiation. Recheck relevant current
        # owner fields without copying/validating every unrelated source on each
        # streamed chunk. No cache or second authority is introduced.
        current = getattr(self.owner.service, 'server_config', {}).get('agentInstance')
        if (not self.selected or not isinstance(current, dict) or any(
                current.get(key) != self.selected.get(key) for key in ('id', 'workspace', 'bundle'))):
            raise ValueError('The public agent profile changed or was revoked; inspect before reconnecting.')
        if source_ref is not None and 'sources' in current and (
                not isinstance(current['sources'], list) or source_ref not in current['sources']):
            raise ValueError('This source conversation is not authorized for the public agent.')
        return current

    def describe(self):
        current = self.current()
        return {'version': 1, 'agentId': current['id'], 'label': current['label'],
            'method': '_' + METHOD, 'interactions': ['text'],
            'inputInspection': True, 'workControls': ['read', 'update', 'pause', 'stop', 'workReceipt'],
            'limits': {'sourceAuthorization': 'trusted-owner-attachment-not-multi-tenant-auth',
                       'executionMapping': 'one-default-root-per-source-pair',
                       'actor': 'source-attribution-not-a-grant',
                       'inputRevision': 'immutable-source-revision',
                       'retention': 'existing-command-and-task-retention',
                       'missingInput': 'unknown-not-proof-of-non-delivery',
                       'stop': 'native-Stop-and-task-pause-not-effect-rollback'}}

    def request(self, value):
        if not isinstance(value, dict):
            raise ValueError('The exact negotiated public agent identity is required.')
        ref = source(value.get('source'))
        if value.get('agentId') != self.current(ref)['id']:
            raise ValueError('The exact negotiated public agent identity is required.')
        return {'agentId': value['agentId'], 'source': ref}

    async def connect(self, cwd, value, servers, identity=None):
        if not isinstance(value, dict) or set(value) != {'agentId', 'source'}:
            raise ValueError('Session metadata contains unsupported fields.')
        binding = self.request(value)
        expected = _identity('execution', binding)
        if identity is not None and identity != expected:
            raise ValueError('This execution does not belong to the exact agent/source binding.')
        if Path(cwd).resolve(strict=True) != self.owner.workspace:
            raise ValueError('This public agent is bound to a different workspace.')
        if identity is None:
            await self.owner.service.dispatch('session.create', {
                'id': expected, 'workspace': str(self.owner.workspace),
                'bundle': self.owner.bundle, 'select': False,
            }, origin=self.owner.origin, command_id=_identity('create', binding),
                include_state=False, admission_check=lambda: self.current(binding['source']))
        async with self.owner.service.lock:
            self.current(binding['source'])
            session = self.owner.service._session(expected, hydrate=False)
            execution_source = self.execution_source(session)
            saved = session.setdefault('messageAnnotations', {}).get(BINDING)
            if saved is not None and saved != binding:
                raise ValueError('The saved execution binding changed.')
            if saved is None:
                # Only this profile's deterministic creation receipt can bind a
                # root. Arbitrary saved roots and private children are not agents.
                receipt = self.owner.service.db.execute(
                    'SELECT receipt FROM commands WHERE id=?', (_identity('create', binding),)).fetchone()
                if not receipt or json.loads(receipt[0]).get('result', {}).get('sessionId') != expected:
                    raise ValueError('The original source creation receipt is unavailable.')
                session['messageAnnotations'][BINDING] = copy.deepcopy(binding)
                self.owner.service._save_changes(sessions={expected})
        await self.owner.attach(expected, cwd, mcp_servers=servers)
        try:
            self.current(binding['source'])  # Recheck authorization after MCP binding.
            current = self.owner.service._session(expected, hydrate=False)
            if self.execution_source(current) != execution_source:
                raise ValueError('The execution source or location changed during attachment.')
        except ValueError:
            await self.owner.release(expected)
            raise
        self.bindings[expected] = binding
        self.execution_sources[expected] = execution_source
        return expected, self.session_info(expected)

    @staticmethod
    def execution_source(session):
        from .session_identity import native_id
        # Directory/history refresh fills aliases with the same native identity.
        # That is not a writer/source replacement; a genuinely different native
        # identity or effective execution location still changes the witness.
        native = native_id(session)
        if session.get('runtimeSessionId') and session['runtimeSessionId'] != native:
            raise ValueError('The execution source or location changed: conflicting native identities.')
        return {'workspace': session.get('workspace'),
                'workingDirectory': session.get('workingDirectory') or session.get('workspace'),
                'nativeIdentity': native}

    def bound(self, identity, value):
        binding = self.request(value)
        self.owner.require_attachment(identity)
        if self.bindings.get(identity) != binding:
            raise ValueError('The source is not bound to this attached execution.')
        session = self.owner.service._session(identity, hydrate=False)
        if session.get('messageAnnotations', {}).get(BINDING) != binding:
            raise ValueError('The saved execution binding changed.')
        if self.execution_source(session) != self.execution_sources.get(identity):
            raise ValueError('The execution source or location changed; reconnect explicitly.')
        return session, binding

    def session_info(self, identity):
        session = self.owner.service._session(identity, hydrate=False)
        return {**self.bindings[identity],
                'expectedInterruptionRevision': session.get('interruptionRevision', 0)}

    def input(self, identity, value):
        session, binding = self.bound(identity, value)
        entry = value.get('input')
        if (not isinstance(entry, dict) or set(entry) != {'id', 'revision', 'actor', 'audience', 'replyTo'}
                or not all(_text(entry.get(key)) for key in ('id', 'actor', 'replyTo'))
                or type(entry.get('revision')) is not int or entry['revision'] < 0
                or not isinstance(entry.get('audience'), list) or not entry['audience']
                or len(entry['audience']) > 64 or not all(_text(item) for item in entry['audience'])):
            raise ValueError('An immutable original input/revision, actor, audience and replyTo are required.')
        if type(value.get('expectedInterruptionRevision')) is not int:
            raise ValueError('The observed interruption revision is required.')
        command = _identity('input', binding, entry['id'], entry['revision'])
        return session, command

    async def prompt(self, identity, value, text, observe):
        if set(value) != {'agentId', 'source', 'input', 'expectedInterruptionRevision'}:
            raise ValueError('Prompt metadata contains unsupported fields.')
        _, command = self.input(identity, value)
        # The canonical command fingerprint includes exact attribution as well as
        # content. This header is public source evidence, never a human grant.
        body = ('Source-attributed public conversation input (not a human authority grant):\n'
                + json.dumps({key: value[key] for key in ('agentId', 'source', 'input')},
                             sort_keys=True, ensure_ascii=False) + '\n\n' + text)
        # A duplicate is an observation, not another call to conversation.send.
        # Compare the same owner fingerprint, including Stop/source attribution.
        from .service import command_fingerprint
        args = {'sessionId': identity, 'text': body,
                'expectedInterruptionRevision': value['expectedInterruptionRevision']}
        async with self.owner.service.lock:
            self.bound(identity, value)
            previous = self.owner.service.db.execute(
                'SELECT fingerprint FROM commands WHERE id=?', (command,)).fetchone()
            if previous and previous[0] != command_fingerprint('conversation.send', args, 'acp', None):
                raise ValueError('This original input revision already has different contents.')
        if previous:
            return {'outcome': 'retained', 'receipt': await self.inspect_input(identity, value)}
        # A missing host command is not evidence that native work never ran.
        # Reuse the existing delivery owner before accepting this source key;
        # positive native-only evidence must not create a replacement host row.
        delivery = await self.owner.service.dispatch('conversation.delivery',
            {'sessionId': identity, 'inputId': command}, origin='acp',
            caller_session_id=identity, include_state=False,
            admission_check=lambda: self.bound(identity, value))
        self.bound(identity, value)
        if delivery['result']['delivery'] != 'not_saved':
            return {'outcome': 'retained', 'receipt': await self.inspect_input(identity, value)}
        # No retained observation allows a first submission, not an automatic
        # retry or a claim of proven non-delivery. Caller/source freshness and
        # the canonical command admission still govern this explicit input.
        return await self.owner.prompt(identity, command, body, observe,
            expected_interruption_revision=value['expectedInterruptionRevision'],
            admission_check=lambda: self.bound(identity, value), origin='acp')

    async def inspect_input(self, identity, value):
        _, command = self.input(identity, value)
        delivery = await self.owner.service.dispatch('conversation.delivery',
            {'sessionId': identity, 'inputId': command}, origin='acp',
            caller_session_id=identity, include_state=False,
            admission_check=lambda: self.bound(identity, value))
        async with self.owner.service.lock:
            session, _ = self.bound(identity, value)
            row = self.owner.service.db.execute(
                'SELECT receipt FROM commands WHERE id=?', (command,)).fetchone()
            receipt = json.loads(row[0]) if row else {}
            if receipt and (receipt.get('sessionId') != identity or receipt.get('inputId') != command):
                raise ValueError('The original command binding cannot be verified.')
            from .input_status import PREFIX, public
            status = public(session.get('messageAnnotations', {}).get(PREFIX + command, {}).get('inputStatus'), command)
            result = {'agentId': self.selected['id'], 'source': copy.deepcopy(value['source']),
                'input': {key: value['input'][key] for key in ('id', 'revision')},
                'attribution': 'lookup-key-only-original-attribution-owned-by-source',
                'commandId': command,
                'fingerprintAvailable': row is not None,
                'delivery': ('unknown' if delivery['result']['delivery'] == 'not_saved'
                             else delivery['result']['delivery']), 'status': status,
                'replayed': False}
        work = await self.owner.service.dispatch('task.get', {'sessionId': identity, 'sourceInputId': command},
            origin='acp', caller_session_id=identity, include_state=False)
        self.bound(identity, value)
        task = work['result'].get('task')
        result['work'] = ({'sessionId': identity, 'taskId': task['id'],
                           'revision': task['revision'], 'operation': 'read'}
                          if task and work['result'].get('sourceInputAssociated') else None)
        result['workAssociation'] = 'current-task-creation-input-only-not-complete-history'
        result['workAvailability'] = work['result'].get('status', 'unknown')
        return result

    async def control(self, params):
        if not isinstance(params, dict) or not {'sessionId', 'binding', 'operation'} <= params.keys():
            raise ValueError('An exact attached session, binding and operation are required.')
        identity, binding, operation = params['sessionId'], params['binding'], params['operation']
        self.bound(identity, binding)
        if operation == 'inspectInput' and set(params) == {'sessionId', 'binding', 'operation'}:
            return await self.inspect_input(identity, binding)
        if operation == 'read' and set(params) == {'sessionId', 'binding', 'operation'}:
            result = await self.owner.service.dispatch('task.get', {'sessionId': identity},
                origin='acp', caller_session_id=identity, include_state=False)
            session, _ = self.bound(identity, binding)
            from .message_delivery import execution_boundary
            return {'agentId': self.selected['id'], 'source': copy.deepcopy(binding['source']),
                    'work': result['result'], 'executionStatus': session.get('status'),
                    'expectedExecution': execution_boundary(session)}
        if operation == 'workReceipt' and set(params) == {'sessionId', 'binding', 'operation', 'commandId'}:
            if not _text(params['commandId']):
                raise ValueError('An exact original commandId is required.')
            result = await self.owner.service.dispatch('task.receipt', {
                'sessionId': identity, 'commandId': _identity('control', self.bindings[identity], params['commandId']),
            }, origin='acp', caller_session_id=identity, include_state=False)
            self.bound(identity, binding)
            row = self.owner.service.db.execute('SELECT receipt FROM commands WHERE id=?',
                (_identity('stop', self.bindings[identity], params['commandId']),)).fetchone()
            return {'task': result['result'], 'stop': json.loads(row[0]) if row else None}
        # Work amendments use the native task's own CAS, command receipt and
        # resource guard. No task is created or resumed by this profile.
        if operation not in {'update', 'pause', 'stop'}:
            raise ValueError('Unsupported instance operation; use advertised native controls.')
        keys = {'sessionId', 'binding', 'operation', 'commandId', 'taskId', 'expectedRevision', 'changes'}
        if set(params) != keys | ({'expectedExecution'} if operation == 'stop' else set()):
            raise ValueError('Work changes require an exact task, revision, command and changes.')
        if not _text(params['commandId']) or not (
                _text(params['taskId']) or operation == 'stop' and params['taskId'] is None):
            raise ValueError('Exact command and task identities are required.')
        changes = params['changes']
        if not isinstance(changes, dict) or (operation in {'pause', 'stop'} and changes) or set(changes) - {
                'objective', 'constraints', 'correction', 'maxTurns'}:
            raise ValueError('Unsupported work amendment.')
        if operation == 'stop' and params['taskId'] is None and (
                type(params['expectedRevision']) is not int or params['expectedRevision'] != 0):
            raise ValueError('A taskless Stop has no task revision; this command has different contents.')
        def guard():
            session, _ = self.bound(identity, binding)
            if operation == 'stop':
                from .message_delivery import execution_boundary
                if params['expectedExecution'] != execution_boundary(session):
                    raise ValueError('New input or another interruption superseded this Stop intent.')
        result = None
        arguments = {
            'sessionId': identity, 'expectedTaskId': params['taskId'],
            'expectedRevision': params['expectedRevision'], **changes,
            **({'expectedExecution': params['expectedExecution']} if operation == 'stop' else {})}
        command = _identity('control', self.bindings[identity], params['commandId'])
        if operation == 'stop':
            # Reconcile BOTH owners before either fresh leg. In particular an
            # earlier taskless Stop must not pause a newly supplied task before
            # discovering that its host Stop command already has other contents.
            retained = await self.owner.service.dispatch('task.receipt', {
                'sessionId': identity, 'commandId': command},
                origin='acp', caller_session_id=identity, include_state=False)
            async with self.owner.service.lock:
                self.bound(identity, binding)
                saved = retained['result'].get('receipt')
                stop_row = self.owner.service.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?',
                    (_identity('stop', self.bindings[identity], params['commandId']),)).fetchone()
                if stop_row:
                    from .service import command_fingerprint as host_fingerprint
                    if (stop_row[0] != host_fingerprint('conversation.stop', {
                            'sessionId': identity, 'expectedExecution': params['expectedExecution']}, 'acp', None)
                            or saved is None and params['taskId'] is not None):
                        raise ValueError('This Stop command ID already has different contents.')
                if saved is not None:
                    if params['taskId'] is None:
                        raise ValueError('This Stop command ID already has different contents.')
                    forwarded = {key: item for key, item in arguments.items() if key != 'sessionId'}
                    forwarded.update(commandId=command, origin='acp-instance-stop')
                    from .task_continuity import command_fingerprint
                    digest = command_fingerprint('task.pause', forwarded)
                    if saved.get('fingerprint') != digest:
                        raise ValueError('This task command ID already has different contents.')
                    result = {'accepted': True, 'result': {**retained['result'], 'duplicate': True}}
                if stop_row:
                    return {'task': result, 'stop': {**json.loads(stop_row[1]), 'duplicate': True},
                            'settlement': 'inspect-native-state'}
        if params['taskId'] is not None:
            if result is None:
                result = await self.owner.service.dispatch('task.' + ('pause' if operation == 'stop' else operation),
                    arguments, origin='acp-instance-stop' if operation == 'stop' else 'acp',
                    command_id=command, caller_session_id=identity, include_state=False, admission_check=guard)
        self.bound(identity, binding)
        if operation == 'stop':
            # Pause settles in the existing task owner before native Stop. If
            # either acknowledgement is lost, exact command receipts reconcile;
            # cancellation of one prompt is never advertised as work revocation.
            task_witness = None
            if params['taskId'] is None:
                current = await self.owner.service.dispatch('task.get', {'sessionId': identity},
                    origin='acp', caller_session_id=identity, include_state=False)
                task = current['result'].get('task')
                if current['result'].get('status') != 'available' or task and task.get('status') != 'completed':
                    raise ValueError('Inspect the current saved task before stopping this execution.')
                task_witness = {key: (task or {}).get(key) for key in ('id', 'revision', 'status')}
            def stop_guard():
                guard()
                # Read the canonical saved authority, not a potentially older
                # display projection, at the final conditional Stop admission.
                from .task_continuity import _load_control
                session = self.owner.service._session(identity, hydrate=False)
                task = _load_control(self.owner.service, session, current_only=True)['task'] or {}
                if params['taskId'] is not None:
                    if (task.get('id') != params['taskId']
                            or task.get('revision') != params['expectedRevision'] + 1
                            or task.get('status') != 'paused'):
                        raise ValueError('The saved task changed after pause; no successor work was stopped.')
                elif {key: task.get(key) for key in ('id', 'revision', 'status')} != task_witness:
                    raise ValueError('The saved task changed before Stop; inspect its current responsibility.')
            stopped = await self.owner.service.dispatch('conversation.stop',
                {'sessionId': identity, 'expectedExecution': params['expectedExecution']},
                origin='acp', command_id=_identity('stop', self.bindings[identity], params['commandId']),
                caller_session_id=identity, include_state=False, admission_check=stop_guard)
            return {'task': result, 'stop': stopped, 'settlement': 'inspect-native-state'}
        return result