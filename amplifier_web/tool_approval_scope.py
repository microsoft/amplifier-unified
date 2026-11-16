"""Exact native tool-call descriptions; Instance remains the only rule store.

Only known mounted tools in the existing enforcing Work wrapper are supported.
Approval witnesses are weakly owned by the dispatch call, never a remembered rule.
"""
from __future__ import annotations

import hashlib
import asyncio
import importlib.metadata
import inspect
import json
import marshal
from pathlib import Path
import sys
import unicodedata
from weakref import WeakKeyDictionary, WeakValueDictionary

from .approval_policy import PRODUCER


_KNOWN = {
    ('amplifier_module_tool_bash', 'BashTool'): ('amplifier-module-tool-bash', 'bash'),
    ('amplifier_module_tool_filesystem.read', 'ReadTool'): ('amplifier-module-tool-filesystem', 'read'),
    ('amplifier_module_tool_filesystem.write', 'WriteTool'): ('amplifier-module-tool-filesystem', 'write'),
    ('amplifier_module_tool_filesystem.edit', 'EditTool'): ('amplifier-module-tool-filesystem', 'edit'),
    ('amplifier_module_tool_apply_patch.tool', 'ApplyPatchTool'): ('amplifier-module-tool-apply-patch', 'patch'),
}


def canonical(value):
    """No coercion, prose parsing, default filling or argument equivalence."""
    def check(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError('The request has no exact JSON representation.')
    check(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _source(tool):
    cls = type(tool)
    selected = _KNOWN.get((cls.__module__, cls.__name__))
    module = sys.modules.get(cls.__module__)
    if not selected or module is None or vars(module).get(cls.__name__) is not cls:
        raise ValueError('The actual tool implementation is unsupported.')
    execute = tool.execute
    if not inspect.ismethod(execute) or execute.__func__ is not cls.execute:
        raise ValueError('The tool execution implementation was replaced.')
    distribution = importlib.metadata.distribution(selected[0])
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    if direct.get('dir_info', {}).get('editable'):
        raise ValueError('Editable tool implementations have no reusable scope.')
    revision = direct.get('vcs_info', {}).get('commit_id')
    digest = direct.get('archive_info', {}).get('hashes', {}).get('sha256')
    if not ((isinstance(revision, str) and len(revision) in {40, 64}
             and all(char in '0123456789abcdef' for char in revision))
            or (isinstance(digest, str) and len(digest) == 64
                and all(char in '0123456789abcdef' for char in digest))):
        raise ValueError('The tool has no exact installed source selection.')
    origin = Path(vars(module)['__file__']).resolve()
    package_file = cls.__module__.replace('.', '/')
    candidates = [distribution.locate_file(package_file + '.py'),
                  distribution.locate_file(package_file + '/__init__.py')]
    if not any(Path(path).resolve() == origin for path in candidates):
        raise ValueError('The imported tool does not match its selected distribution.')
    return {'module': cls.__module__, 'class': cls.__name__, 'origin': str(origin),
            'distribution': selected[0], 'version': distribution.version, 'selection': direct,
            # Hash the loaded entrypoint, not a claim that current disk bytes
            # prove what was imported. The package selection remains explicit.
            'implementation': hashlib.sha256(marshal.dumps(execute.__func__.__code__)).hexdigest()}, selected[1]


def _mode(coordinator):
    state = coordinator.session_state
    name = state.get('active_mode')
    result = {'name': name, 'requireApproval': sorted(state.get('require_approval_tools', ())) }
    if name:
        discovery = state.get('mode_discovery')
        definition = discovery.find(name) if discovery else None
        if definition is None:
            raise ValueError('The current mode cannot be resolved.')
        result.update({key: sorted(getattr(definition, key)) for key in
                       ('safe_tools', 'warn_tools', 'confirm_tools', 'block_tools')})
        result['defaultAction'] = definition.default_action
    return result


class PreparedToolApproval:
    def __init__(self, owner, identity, call_id, fact, epoch):
        self.owner, self.identity, self.fact, self.epoch = owner, identity, fact, epoch
        self.call_id = call_id
        self.claimed = False
        # Generic hook runners may log a callback exception and continue. A
        # known typed request must therefore block at the effect boundary until
        # this exact call has a successful approval, including after an error.
        owner.accepted[identity] = None
        owner.calls[call_id] = identity

    def claim(self):
        """One actual Worker.ask may carry this typed request's private fact."""
        if self.claimed:
            return False
        try:
            current, fact, epoch = self.owner.snapshot()
            if current.identity is not self.identity or fact != self.fact or epoch != self.epoch:
                return False
        except Exception:
            return False
        self.claimed = True
        return True

    def approve(self):
        current, fact, epoch = self.owner.snapshot()
        if (current.identity is not self.identity or fact != self.fact or epoch != self.epoch):
            raise ValueError('The tool request or execution context changed during approval.')
        self.owner.accepted[self.identity] = (self.fact['descriptor']['key'], self.epoch)


class ToolApprovalScope:
    def __init__(self, coordinator, inspect_tool):
        self.coordinator, self.inspect_tool = coordinator, inspect_tool
        self.accepted = WeakKeyDictionary()
        self.calls = WeakValueDictionary()

    def current(self):
        dispatcher = self.coordinator.get_capability('tools.dispatch')
        observe = getattr(dispatcher, 'current_call', None)
        return observe(self.coordinator) if callable(observe) else None

    def snapshot(self):
        coordinator = self.coordinator
        current = self.current()
        if current is None:
            raise ValueError('No current registered tool call.')
        mounted = current.tool
        tool = self.inspect_tool(current.name, mounted)
        if tool is mounted:
            raise ValueError('The tool has no enforcing host execution wrapper.')
        from .file_source_guard import GuardedFileTool, _KNOWN as FILES
        guard = None
        if isinstance(tool, GuardedFileTool):
            guard = tool.guard
            if (guard is not coordinator.get_capability('web.file_source_guard')
                    or guard.coordinator is not coordinator
                    or tool.kind != FILES.get((type(tool.tool).__module__, type(tool.tool).__name__))):
                raise ValueError('The file guard binding cannot be verified.')
            tool = tool.tool
        source, kind = _source(tool)
        if kind != 'bash' and guard is None:
            raise ValueError('The file tool has no enforcing source guard.')
        if kind == 'bash':
            raise ValueError('The shell has no supported exact execution-environment witness.')
        import jsonschema
        from referencing import Registry
        def no_retrieval(uri):
            raise ValueError('External schema references cannot describe an exact request.')
        jsonschema.Draft202012Validator.check_schema(current.input_schema)
        jsonschema.Draft202012Validator(current.input_schema,
            registry=Registry(retrieve=no_retrieval)).validate(current.arguments)
        context = coordinator.get_capability('web.tool_approval.context')
        facts = context() if callable(context) else None
        if not isinstance(facts, dict) or facts.get('accessMode') not in {'ask', 'full'}:
            raise ValueError('The native execution context cannot be verified.')
        policy = coordinator.get_capability('web.write_policy')
        effective = tool
        if kind in {'write', 'edit', 'patch'} and policy:
            effective = policy.invocation(tool, 'tool-apply-patch' if kind == 'patch'
                                           else 'tool-filesystem', coordinator)
        resources = ({str(path): operation for path, operation in
                      guard._targets(effective, kind, current.arguments).items()} if guard else {})
        config = effective.config
        cwd = str(Path(getattr(effective, 'working_dir', None)
                       or config.get('working_dir')
                       or coordinator.get_capability('session.working_dir')).resolve())
        security_tool = effective._engine if kind == 'patch' else effective
        roots = {key: getattr(security_tool, key) for key in
                 ('allowed_read_paths', 'denied_read_paths', 'allowed_write_paths',
                  'denied_write_paths', 'allowed_paths', 'denied_paths', 'allowed_commands',
                  'denied_commands', 'require_approval') if hasattr(security_tool, key)}
        meaning = {'producer': PRODUCER, 'operation': 'tool.invoke',
                   'owner': facts['owner'], 'workspace': facts['workspace'],
                   'tool': current.name, 'source': source, 'schema': current.input_schema,
                   'arguments': current.arguments, 'workingDirectory': cwd,
                   'sessionWorkingDirectory': coordinator.get_capability('session.working_dir'),
                   'accessMode': facts['accessMode'], 'mode': _mode(coordinator),
                   'config': config, 'roots': roots, 'resources': resources}
        description = (f'Exact {current.name} request in {cwd}. Same tool, arguments, mode and '
                       "security limits across this agent's chats. Each decision authorizes only its current request.")
        if (len(description.encode()) > 512
                or any(unicodedata.category(char) == 'Cc' for char in description)):
            raise ValueError('The exact request cannot be described within the wire bound.')
        descriptor = {'version': 1, 'producer': PRODUCER, 'key': fingerprint(meaning),
                      'description': description, 'allowOptionId': 'allow', 'authorityLifetime': 'request'}
        fact = {'owner': facts['owner'], 'workspace': facts['workspace'], 'descriptor': descriptor}
        return current, fact, canonical(facts['epoch'])

    def request(self, request):
        try:
            current, fact, epoch = self.snapshot()
            if (request.tool_name != current.name
                    or canonical(request.details) != canonical(current.arguments)):
                return None
            return PreparedToolApproval(self, current.identity, current.call_id, fact, epoch)
        except Exception:
            # Unsupported metadata never upgrades an ordinary once/deny request.
            return None

    def check(self, name, mounted, arguments):
        dispatch = getattr(self.coordinator, '_tool_dispatch_contexts', {}).get(asyncio.current_task(), {})
        identity = self.calls.get(dispatch.get('tool_call_id'))
        current = self.current()
        # The existing Work guard rejects execution without a genuine dispatch.
        # If an approved call is still alive but has lost its valid engine view
        # (for example cancellation while awaiting Work), refuse before effects.
        if identity is None and current is not None:
            identity = current.identity
        if identity is None or identity not in self.accepted:
            return
        accepted = self.accepted[identity]
        if accepted is None:
            raise ValueError('This exact tool call has not been approved.')
        if current is None or current.identity is not identity:
            raise ValueError('The approved tool call is no longer active.')
        fresh, fact, epoch = self.snapshot()
        if (fresh.tool is not mounted or fresh.name != name
                or canonical(fresh.arguments) != canonical(arguments)
                or accepted != (fact['descriptor']['key'], epoch)):
            raise ValueError('The approved tool request or execution context changed before execution.')

    def check_current(self, arguments):
        current = self.current()
        self.check(current.name if current else None, current.tool if current else None, arguments)


def install(coordinator, inspect_tool):
    owner = ToolApprovalScope(coordinator, inspect_tool)
    coordinator.register_capability('web.tool_approval.scope', owner)
    return owner
