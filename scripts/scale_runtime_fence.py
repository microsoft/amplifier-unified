"""Fixture-only authority boundary for copied-history concurrency acceptance."""
from dataclasses import dataclass
from types import MappingProxyType


class ReplicaRefusal(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    identity: str
    workspace: str
    bundle: str
    native_project: str

    def matches(self, row):
        return (isinstance(row, dict) and row.get('id') == self.identity
                and row.get('workspace') == self.workspace
                and row.get('bundle') == self.bundle
                and row.get('nativeProject') == self.native_project
                and row.get('nativeIdentity') == self.identity
                and row.get('runtimeSessionId') == self.identity
                and row.get('historyManaged') is False)


class Fence:
    READ_ACTIONS = frozenset({'view.update', 'shell.view.update', 'shell.report',
        'session.select', 'workspace.select', 'session.history', 'session.plans',
        'session.inspect', 'session.titlePreview', 'configuration.defaults',
        'providers.list', 'routing.list', 'schedule.list', 'schedule.read',
        'observation.list', 'observation.read', 'worktree.list', 'worktree.read',
        'notifications.status', 'diagnostics.status', 'updates.status'})
    EFFECTS = frozenset({'conversation.send', 'conversation.stop', 'session.warm'})
    BRIDGES = frozenset({'capacity.admit', 'coordination.admit', 'questions.admit',
                         'coordination.continuation_ready'})

    def __init__(self):
        self.bindings = MappingProxyType({})
        self.lookup = None
        self.frozen = False
        self.refused = 0

    def freeze(self, bindings, lookup):
        if self.frozen or not bindings or len(bindings) > 14:
            raise ReplicaRefusal('Invalid synthetic owner admission')
        rows = {row.identity: row for row in bindings}
        if len(rows) != len(bindings):
            raise ReplicaRefusal('Duplicate synthetic owner')
        self.bindings = MappingProxyType(rows)
        self.lookup = lookup
        self.frozen = True
        for identity in rows:
            self.owner(identity)

    def deny(self):
        self.refused += 1
        raise ReplicaRefusal('This replica permits execution only for its frozen synthetic owners.')

    def owner(self, identity, row=None):
        binding = self.bindings.get(identity)
        if not self.frozen or binding is None:
            return self.deny()
        current = self.lookup(identity)
        if not binding.matches(current) or row is not None and not binding.matches(row):
            return self.deny()
        return binding

    def action(self, action, args):
        args = args or {}
        if action == 'shell.command':
            command = args.get('command', args)
            if not isinstance(command, dict):
                return self.deny()
            inner = command.get('action')
            if inner == 'shell.command':
                return self.deny()
            return self.action(inner, command.get('args') or {})
        if action in self.READ_ACTIONS:
            return
        if action not in self.EFFECTS:
            return self.deny()
        # Never infer an execution target from the currently selected owner.
        self.owner(args.get('sessionId'))

    def bridge(self, operation, args, identity):
        self.owner(identity)
        runtime_id = args.get('_runtimeSessionId')
        if runtime_id is not None and runtime_id != identity:
            return self.deny()
        if operation in {'dispatch', 'action.dispatch'}:
            return self.action(args.get('action'), args.get('args') or {})
        if operation not in self.BRIDGES:
            return self.deny()


def guarded_runtime(runtime_type, fence):
    """Use real runtime logic; refuse before any worker admission or control."""
    class GuardedRuntime(runtime_type):
        async def _start_locked(self, session, *args, **kwargs):
            fence.owner(session.get('id'), session)
            return await super()._start_locked(session, *args, **kwargs)

        async def start(self, session, *args, **kwargs):
            fence.owner(session.get('id'), session)
            return await super().start(session, *args, **kwargs)

        async def send(self, session, *args, **kwargs):
            fence.owner(session.get('id'), session)
            return await super().send(session, *args, **kwargs)

        async def prewarm(self, session, *args, **kwargs):
            fence.owner(session.get('id'), session)
            return await super().prewarm(session, *args, **kwargs)

        async def steer(self, session, *args, **kwargs):
            fence.owner(session.get('id'), session)
            return await super().steer(session, *args, **kwargs)

        async def control(self, identity, operation, arguments=None):
            fence.owner(identity)
            # No test needs arbitrary tools, history mutation or configuration.
            if operation != 'conversation.steer':
                return fence.deny()
            return await super().control(identity, operation, arguments)

        async def stop(self, identity, **kwargs):
            fence.owner(identity)
            return await super().stop(identity, **kwargs)

        async def stop_worker(self, identity, worker_id):
            return fence.deny()

    async def forbidden(self, *args, **kwargs):
        return fence.deny()

    # The workload does not need delegation, takeover, deferred jobs, handoff,
    # tool grants, approvals, or a one-shot shared-state subprocess. Refuse
    # these before they can inspect or mutate a copied native owner.
    for name in ('spawn_worker', 'message_worker', 'steer_worker', 'takeover',
                 'handoff_boundary', 'quiesce_for_handoff', 'scheduled_input',
                 'observation_input', 'collaboration_input', 'collaboration_steer',
                 'collaboration_approval', 'approval', 'resource_access',
                 'shared_state_probe', 'retry'):
        setattr(GuardedRuntime, name, forbidden)
    return GuardedRuntime
