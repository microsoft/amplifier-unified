"""Live write defaults at reference-tool admission, not mutable tool instances.

Composition and path intersection stay with the existing host policy owner.
Only the two filesystem write defaults change live. Writer declarations and
their other settings retain the ordinary runtime lifecycle; unrelated settings
are not dependencies of a file invocation.
"""
from __future__ import annotations

import copy
import inspect
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

from .configuration_authority import fingerprint
from .shared_settings import SettingsReadCache, read_settings, settings_paths

WRITE_KEYS = ('allowed_write_paths', 'denied_write_paths')
MODULES = {'tool-filesystem', 'tool-apply-patch', 'tool-image'}
IMAGES = {('amplifier_module_tool_image.workspace', 'WorkspaceImageTool'),
          ('amplifier_module_tool_image.routed', 'RoutedImageTool')}


def _rows(rows):
    return [copy.deepcopy(row) for row in rows if row.get('module') in MODULES]


def _agents(agents):
    from .host.components import agent_roster
    return {name: {'tools': _rows(agent.get('tools', [])),
                   'agents': _agents(agent.get('agents', {}))}
            for name, agent in agent_roster(agents).items() if isinstance(agent, dict)}


def _without_writes(settings):
    result = copy.deepcopy(settings)
    values = result.get('overrides', {}).get('tool-filesystem', {}).get('config', {})
    for key in WRITE_KEYS:
        values.pop(key, None)
    # Reset prunes empty ancestors; absent and empty containers mean the same.
    def prune(value):
        if isinstance(value, dict):
            return {key: cleaned for key, item in value.items()
                    if (cleaned := prune(item)) != {}}
        return value
    return prune(result)


class WritePolicyRefreshRequired(ValueError):
    code = 'file_policy_refresh_required'


class WritePolicySource:
    """Host-only provenance captured before settings obscure declaration limits."""

    def __init__(self, bundle, config, *, snapshot=False, execution_workspace=None):
        self.tools = _rows(getattr(bundle, 'tools', []))
        self.agents = _agents(getattr(bundle, 'agents', {}))
        self.config = config
        self.snapshot = snapshot
        self.execution_workspace = execution_workspace

    def select(self, settings):
        """The settings dependency of build, also used to fence mount changes.

        Keep whole writer declarations/overrides, including source, identity and
        enabled, rather than guessing which config fields a module will consume.
        Providers, decision selection and unrelated tools are not composed here.
        """
        selected = {key: {'tools': _rows(settings.get(key, {}).get('tools', []))}
                    for key in ('modules', 'config')}
        from .access_mode import walk_tools
        rows = [*walk_tools(self), *selected['modules']['tools'], *selected['config']['tools']]
        identities = {row.get('id') or row.get('instance_id') or row['module'] for row in rows}
        selected['overrides'] = {key: copy.deepcopy(value)
            for key, value in settings.get('overrides', {}).items() if key in identities | MODULES}
        # Ordinary mounts also resolve sources.modules, with override sources
        # taking precedence (HostConfig.module_sources). Retain only effective
        # selections for declared writers, including children; snapshots use
        # their saved declaration sources instead (host.session.module_source).
        sources = replace(self.config, settings=settings).module_sources if not self.snapshot else {}
        selected['sources'] = {'modules': {module: copy.deepcopy(sources[module])
            for module in {row['module'] for row in rows} if sources.get(module)}}
        return selected

    def build(self, settings):
        return self.build_selected(self.select(settings))

    def build_selected(self, selected):
        """Compose an already selected dependency without projecting it again."""
        from .host.session import _apply_host_policy, _apply_settings
        from .host.config import runtime_settings
        # Project before relocation/copying: the same selected dependency is
        # checked by refresh. Never compose new providers, hooks or credentials.
        selected = runtime_settings(selected, self.config.home, self.config.registry_home)
        bundle = SimpleNamespace(tools=copy.deepcopy(self.tools), agents=copy.deepcopy(self.agents),
                                 providers=[], hooks=[], session={})
        config = SimpleNamespace(workspace=self.config.workspace, settings=selected,
                                 providers=[], module_sources={}, access_mode=self.config.access_mode)
        if not self.snapshot:
            _apply_settings(bundle, config)
        return _apply_host_policy(bundle, config, execution_workspace=self.execution_workspace)


class LiveWritePolicy:
    def __init__(self, source, coordinator, session_id, *, current=lambda: True):
        self.source, self.coordinator = source, coordinator
        self.session_id, self.current = session_id, current
        self.cache = SettingsReadCache(limit=1)
        self.initial = source.select(getattr(source.config, '_canonical_settings', source.config.settings))
        self.mount_settings = _without_writes(self.initial)
        self.settings = None
        self.revision = None
        self.signature = None
        self.bundle = None

    def refresh(self, *, expected=None):
        if not self.current():
            raise WritePolicyRefreshRequired('The originating runtime is no longer current.')
        config = self.source.config
        paths = tuple(settings_paths(config.workspace, shared_home=config.config_home,
                                     session_id=self.session_id, global_only=config.global_only).values())
        # Reuse the settings owner's metadata witness. Unchanged calls stat
        # only this session's scoped files: no YAML, settings copy or hashing.
        signature = self.cache._signature(paths)
        if self.revision is not None and signature == self.signature:
            if expected is not None and expected != self.revision:
                raise WritePolicyRefreshRequired('File settings changed before the live refresh.')
            return {'settingsRevision': self.revision, 'boundary': 'next-tool-call'}
        settings = read_settings(config.workspace, shared_home=config.config_home,
                                 session_id=self.session_id, global_only=config.global_only, cache=self.cache)
        revision = fingerprint(settings)
        if expected is not None and expected != revision:
            raise WritePolicyRefreshRequired('File settings changed before the live refresh.')
        selected = self.source.select(settings)
        if _without_writes(selected) != self.mount_settings:
            raise WritePolicyRefreshRequired('Writer configuration changed; the runtime must refresh before writing.')
        changed = selected != self.settings
        bundle = self.source.build_selected(selected) if changed else self.bundle
        if self.cache._signature(paths) != signature or not self.current():
            raise WritePolicyRefreshRequired('File settings or runtime changed during the live refresh.')
        if changed:
            # Child planning reads declarations, not mounted tool attributes.
            # Existing children keep their own plans and invocation ceilings.
            def update(rows, fresh, agents, fresh_agents):
                def identity(row):
                    return row.get('module'), row.get('id') or row.get('instance_id') or row.get('module')
                policies = {identity(row): row for row in fresh}
                updated = []
                for row in rows:
                    policy = policies.get(identity(row))
                    if policy:
                        row = {**row, 'config': dict(row.get('config', {}))}
                        for key in WRITE_KEYS:
                            if key in policy.get('config', {}):
                                row['config'][key] = copy.deepcopy(policy['config'][key])
                            else:
                                row['config'].pop(key, None)
                    updated.append(row)
                from .host.components import agent_roster
                children = dict(agents) if isinstance(agents, dict) else agents
                for name, agent in agent_roster(agents).items():
                    if isinstance(agent, dict) and name in fresh_agents:
                        fresh_agent = fresh_agents[name]
                        tools, nested = update(agent.get('tools', []), fresh_agent['tools'],
                                               agent.get('agents', {}), fresh_agent['agents'])
                        children[name] = {**agent, 'tools': tools, 'agents': nested}
                return updated, children
            plan = self.coordinator.config
            # Replace declaration trees, not their captured objects. A child
            # already preparing from this roster must retain its old ceiling.
            # Unrelated module configs and agent bodies need no deep copy.
            tools, agents = update(plan.get('tools', []), bundle.tools, plan.get('agents', {}), bundle.agents)
            plan.update(tools=tools, agents=agents)
            self.settings, self.bundle = selected, bundle
        self.revision = revision
        self.signature = signature
        return {'settingsRevision': revision, 'boundary': 'next-tool-call'}

    def confirm(self, revision):
        """A host receipt requires complete supported-writer coverage."""
        from .file_source_guard import GuardedFileTool, _KNOWN
        coordinators = [self.coordinator]
        children = self.coordinator.get_capability('live.children')
        if children:
            coordinators.extend(session.coordinator for session in children.sessions.values())
        for coordinator in coordinators:
            inspect = coordinator.get_capability('web.work_inspect_tool')
            seen = set()
            for name, mounted in (coordinator.get('tools') or {}).items():
                tool = inspect(name, mounted) if callable(inspect) else mounted
                if isinstance(tool, GuardedFileTool):
                    if (tool.guard is not coordinator.get_capability('web.file_source_guard')
                            or tool.guard.coordinator is not coordinator
                            or tool.kind != _KNOWN.get((type(tool.tool).__module__, type(tool.tool).__name__))
                            or coordinator.get_capability('web.write_policy') is not self):
                        raise ValueError('The file writer policy binding cannot be verified.')
                    tool = tool.tool
                elif isinstance(tool, PolicyImageTool):
                    if (tool.coordinator is not coordinator
                            or coordinator.get_capability('web.write_policy') is not self):
                        raise ValueError('The image writer policy binding cannot be verified.')
                    tool = tool.tool
                else:
                    # Raw reference classes do not enforce the live boundary.
                    kind = _KNOWN.get((type(tool).__module__, type(tool).__name__))
                    if (kind in {'write', 'edit', 'patch'} or (type(tool).__module__, type(tool).__name__) in IMAGES
                            or name in {'write_file', 'edit_file', 'apply_patch', 'image_generate'}):
                        raise ValueError('The mounted writer has no enforcing policy binding.')
                    continue
                kind = _KNOWN.get((type(tool).__module__, type(tool).__name__))
                if coordinator is self.coordinator and (kind in {'write', 'edit', 'patch'}
                        or (type(tool).__module__, type(tool).__name__) in IMAGES):
                    identity = coordinator.get_capability('web.write_policy.tool_identity')
                    if not callable(identity) or identity(tool) is None:
                        raise ValueError('The mounted writer identity has no live policy binding.')
                if kind in {'write', 'edit'}:
                    seen.add('tool-filesystem')
                elif kind == 'patch':
                    seen.add('tool-apply-patch')
                elif (type(tool).__module__, type(tool).__name__) in IMAGES:
                    seen.add('tool-image')
            declared = [row['module'] for row in coordinator.config.get('tools', [])
                        if row.get('module') in MODULES and row.get('enabled', True)]
            if any(module not in seen or declared.count(module) != 1 for module in declared):
                raise ValueError('The mounted writers do not support a complete live refresh.')
        return self.refresh(expected=revision)

    def invocation(self, tool, module, coordinator):
        self.refresh()
        config = copy.deepcopy(tool.config)
        if coordinator is self.coordinator:
            # Instance names come from the mounted declaration, never arguments.
            rows = [row for row in self.bundle.tools if row['module'] == module]
            declarations = [row for row in self.coordinator.config.get('tools', []) if row.get('module') == module]
            identity = coordinator.get_capability('web.write_policy.tool_identity')
            name = identity(tool) if callable(identity) else None
            rows = [row for row in rows if (row.get('id') or row.get('instance_id') or row['module']) == name]
            if len(rows) != 1 or not declarations:
                if self.settings != self.initial:
                    raise WritePolicyRefreshRequired('The writer declaration cannot be refreshed safely.')
                # Ambiguous pre-existing instances remain usable unchanged.
                # A write-list change fails closed and cannot clear its fence.
                policy = config
            else:
                policy = rows[0].get('config', {})
        else:
            # An existing/resumed/preparing child may only become narrower.
            # Reuse the child planner's intersection, including denied subtrees.
            from .host.children import _restrict_child_tools
            from .host.session import _apply_host_policy
            from .host.config import runtime_settings
            shared = SimpleNamespace(tools=[{'module': 'tool-filesystem'}], agents={})
            config_owner = SimpleNamespace(workspace=self.source.config.workspace,
                settings=runtime_settings(self.settings, self.source.config.home, self.source.config.registry_home),
                access_mode=self.source.config.access_mode)
            _apply_host_policy(shared, config_owner, execution_workspace=self.source.execution_workspace)
            parents = {'tool-filesystem'}
            if module != 'tool-filesystem':
                parents.add(module)
            limits = [shared.tools[0]['config'], *(row.get('config', {}) for row in self.bundle.tools
                                                  if row['module'] in parents), config]
            row = {'module': module, 'config': config}
            policy = _restrict_child_tools([row], limits,
                coordinator.get_capability('session.working_dir'), {module})
        defaults = {'allowed_write_paths': [str(Path(tool.config.get('working_dir')
            or self.source.execution_workspace or self.source.config.workspace).resolve())],
            'denied_write_paths': []}
        for key in WRITE_KEYS:
            config[key] = copy.deepcopy(policy.get(key, defaults[key]))
        # A private invocation copy retains hooks/approvals and engine state,
        # but never changes an object currently executing in another task.
        result = copy.copy(tool)
        execute = tool.execute
        if inspect.ismethod(execute) and execute.__self__ is tool:
            # Instance interceptors can be receiver-aware bound methods too.
            # Keep the exact intercepted function, bound to this snapshot;
            # neither class lookup nor closure unwrapping preserves that chain.
            result.execute = MethodType(execute.__func__, result)
        result.config = config
        target = result
        if module == 'tool-apply-patch':
            result._engine = copy.copy(tool._engine)
            result._engine.config = config
            target = result._engine
        for key in WRITE_KEYS:
            # The reference filesystem and both patch engines validate again
            # inside execute using these mount-cached attributes, not config.
            # Bind them to the invocation's single policy, never the raw mount.
            setattr(target, key, config[key])
        return result


def install(coordinator, source, session_id, *, current):
    policy = LiveWritePolicy(source, coordinator, session_id, current=current)
    coordinator.register_capability('web.write_policy', policy)
    # Root module identity is host-owned and fixed at installation. File tools
    # mount several names from one declaration. Ambiguous multiple declarations
    # fail closed rather than guessing which one owns a mounted instance.
    bindings = {}
    from .file_source_guard import _KNOWN
    for tool in (coordinator.get('tools') or {}).values():
        kind = _KNOWN.get((type(tool).__module__, type(tool).__name__))
        image = (type(tool).__module__, type(tool).__name__) in IMAGES
        module = 'tool-image' if image else 'tool-apply-patch' if kind == 'patch' else 'tool-filesystem'
        if not image and kind not in {'write', 'edit', 'patch'}:
            continue
        rows = [row for row in coordinator.config.get('tools', []) if row.get('module') == module]
        if len(rows) == 1:
            bindings[id(tool)] = rows[0].get('id') or rows[0].get('instance_id') or module
    coordinator.register_capability('web.write_policy.tool_identity', lambda tool: bindings.get(id(tool)))
    return policy


class PolicyImageTool:
    def __init__(self, tool, coordinator):
        self.tool, self.coordinator = tool, coordinator

    def __getattr__(self, name):
        return getattr(self.tool, name)

    async def execute(self, arguments):
        from amplifier_core import ToolResult
        if arguments.get('action') not in {'generate', 'edit'}:
            return await self.tool.execute(arguments)
        policy = self.coordinator.get_capability('web.write_policy')
        try:
            tool = policy.invocation(self.tool, 'tool-image', self.coordinator) if policy else self.tool
        except Exception as exc:
            return ToolResult(success=False, error={'code': 'file_policy_refresh_required',
                                                    'message': str(exc), 'effect': 'none'})
        return await tool.execute(arguments)