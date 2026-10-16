"""Offline installed-wheel routing seam; execute only in a qualified runtime.

The pytest driver supplies UNIFIED_RUNTIME_PYTHON, WARM_FOUNDATION_PATH and
WARM_ROUTING_PATH. Warmed sources verify installed delegate/router wheel bytes;
only installed package paths are supplied to Core. No source-checkout fallback,
bundle.prepare(), activation, installation or live provider calls are permitted.
Core, Foundation, context, loop-live's provider selector, hooks-routing,
DelegateTool and Unified's child launcher are real. Providers, the finite turn
body and the no-effect desktop configuration probe are synthetic. Desktop
ceilings cross actual nested Core sessions and saved intermediate-child resume;
this is not a test of a desktop backend or desktop action enforcement.

--preflight exits 77 for absent prerequisites, not for incompatible APIs.
--negative-control corrupts mounted models in a child lifecycle hook; the same
execution-boundary assertions MUST then fail (nonzero exit, no success record).
"""
from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile


PREFIX = "DEFAULT_ROUTING_PROBE "
MATRIX_NAME = "offline-routing-probe"
PROVIDER_MODULE = "provider-routing-probe"
PROVIDER_PACKAGE = "amplifier_module_provider_routing_probe"
DESKTOP_MODULE = "tool-computer-use"
DESKTOP_PACKAGE = "amplifier_module_tool_desktop_ceiling_probe"
DESKTOP_CEILING = {"read_only": True, "gate_writes": True}
UPSTREAM_PACKAGES = {
    "tool-delegate": ("amplifier_module_tool_delegate", "amplifier-module-tool-delegate"),
    "hooks-routing": ("amplifier_module_hooks_routing", "amplifier-module-hooks-routing"),
}
ALPHA_MODELS = ["alpha-default-v1", "agent-pin-v1", "caller-alt-v1",
                "matrix-review-v1", "conversation-v1", "conversation-v2"]
BETA_MODELS = ["beta-default-v1", "declared-pin-v1", "call-pin-v1", "matrix-coding-v1"]

# A filesystem module, loaded/validated/mounted by the real Core loader for
# every session. Model listing is deterministic even if a runtime queries it.
PROVIDER_SOURCE = '''\
from amplifier_core import ModelInfo, ProviderInfo

__amplifier_module_type__ = "provider"
COMPLETION_ATTEMPTS = []

class ProbeProvider:
    name = "routing-probe"

    def __init__(self, config):
        self.config = dict(config)
        self.default_model = config["default_model"]
        self.priority = config.get("priority", 100)
        self.model_ids = tuple(config["probe_models"])

    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Offline routing probe",
                            defaults={"model": self.default_model},
                            credential_env_vars=[], config_fields=[])

    async def list_models(self):
        return [ModelInfo(id=model, display_name=model, context_window=8192,
                          max_output_tokens=1024) for model in self.model_ids]

    async def complete(self, request, **kwargs):
        COMPLETION_ATTEMPTS.append(self.default_model)
        raise AssertionError("The routing probe must not invoke a model")

    def parse_tool_calls(self, response):
        return []

async def mount(coordinator, config=None):
    provider = ProbeProvider(config)
    provider.policy_at_mount = (
        coordinator.get_capability("routing.defer_agent_resolution") is True
        and coordinator.get_capability("web.default_routing_policy") is True
    )
    await coordinator.mount("providers", provider, name="routing-probe")
'''

# Deliberately a distinct package, resolved under the real desktop module ID.
# Core validates/mounts this fixture, never the installed desktop implementation.
# It does NOT enforce or repair policy: the host must supply the restricted config.
DESKTOP_SOURCE = '''\
from amplifier_core import ToolResult

__amplifier_module_type__ = "tool"

class DesktopProbeTool:
    name = "desktop_policy_probe"
    description = "Inspect synthetic desktop configuration without desktop effects."
    input_schema = {"type": "object", "properties": {"action": {"const": "inspect"}},
                    "required": ["action"], "additionalProperties": False}

    def __init__(self, config):
        self.config = dict(config)

    async def execute(self, input):
        assert input == {"action": "inspect"}, "No desktop actions exist in this fixture"
        return ToolResult(success=True, output=dict(self.config))

async def mount(coordinator, config=None):
    await coordinator.mount("tools", DesktopProbeTool(config or {}), name="desktop_policy_probe")
'''


def emit(value):
    print(PREFIX + json.dumps(value, sort_keys=True), flush=True)


def offline_guard():
    """Tripwire, not an OS sandbox; never allow a swallowed attempt to pass."""
    attempts = []

    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.bind", "socket.sendto",
                     "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec"}:
            attempts.append(event)
            raise RuntimeError(f"Offline routing probe forbids {event}")

    sys.addaudithook(audit)
    return attempts


def prerequisite_paths():
    missing = []
    for name in ("UNIFIED_RUNTIME_PYTHON", "WARM_FOUNDATION_PATH", "WARM_ROUTING_PATH"):
        if not os.environ.get(name):
            missing.append(name)
    if missing:
        return None, missing
    foundation = Path(os.environ["WARM_FOUNDATION_PATH"]).expanduser().resolve()
    routing = Path(os.environ["WARM_ROUTING_PATH"]).expanduser().resolve()
    warmed_paths = {
        "tool-delegate": foundation / "modules/tool-delegate/amplifier_module_tool_delegate",
        "hooks-routing": routing / "modules/hooks-routing/amplifier_module_hooks_routing",
    }
    paths = {}
    for name, path in warmed_paths.items():
        if not (path / "__init__.py").is_file():
            missing.append(f"{name} package at the exact warmed path: {path}")
        package, distribution_name = UPSTREAM_PACKAGES[name]
        spec = importlib.util.find_spec(package)
        if spec is None:
            missing.append(f"runtime package {package}")
        else:
            assert spec.origin, f"Installed package has no file origin: {package}"
            paths[name] = Path(spec.origin).resolve().parent
        try:
            importlib.metadata.distribution(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(f"installed {distribution_name} distribution")
    for name in ("amplifier_web", "amplifier_core", "amplifier_foundation",
                 "amplifier_module_loop_live", "amplifier_module_loop_streaming",
                 "amplifier_module_context_simple"):
        spec = importlib.util.find_spec(name)
        if spec is None:
            missing.append(f"runtime package {name}")
        elif name in {"amplifier_module_loop_live", "amplifier_module_context_simple"}:
            paths[name.removeprefix("amplifier_module_").replace("_", "-")] = Path(spec.origin).parent
    try:
        distribution = importlib.metadata.distribution("amplifier-unified")
    except importlib.metadata.PackageNotFoundError:
        missing.append("installed amplifier-unified distribution")
        distribution = None
    return (paths, warmed_paths, routing, distribution), missing


def verify_wheel(distribution):
    """Reject editable/shadowed/modified host code, not merely repo sys.path use."""
    assert distribution.read_text("WHEEL"), "amplifier-unified must be installed from a wheel"
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert not direct_url.get("dir_info", {}).get("editable"), "Editable Unified is not wheel qualification"
    recorded = {str(path): path for path in distribution.files or []}
    entry = recorded.get("amplifier_web/__init__.py")
    assert entry is not None, "Installed wheel has no amplifier_web RECORD entry"
    expected_init = Path(distribution.locate_file(entry)).resolve()
    spec = importlib.util.find_spec("amplifier_web")
    assert Path(spec.origin).resolve() == expected_init, "amplifier_web is shadowed by source code"
    checked = 0
    for name, module in list(sys.modules.items()):
        if name != "amplifier_web" and not name.startswith("amplifier_web."):
            continue
        origin = getattr(module, "__file__", None)
        if origin is None:
            continue
        path = Path(origin).resolve()
        relative = path.relative_to(expected_init.parent.parent).as_posix()
        record = recorded.get(relative)
        assert record is not None and record.hash is not None, f"Unrecorded host module: {name}"
        assert record.hash.mode == "sha256", f"Unexpected wheel hash for {name}"
        actual = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip("=")
        assert actual == record.hash.value, f"Installed host module differs from wheel RECORD: {name}"
        checked += 1
    return {"version": distribution.version, "origin": str(expected_init), "checked_modules": checked}


def verify_upstream(paths, warmed_paths):
    """Attest every installed delegate/router Python file, not just imports."""
    evidence = {}
    for module_id, (package, distribution_name) in UPSTREAM_PACKAGES.items():
        distribution = importlib.metadata.distribution(distribution_name)
        assert distribution.read_text("WHEEL"), f"{distribution_name} must be a wheel"
        assert distribution.read_text("RECORD"), f"{distribution_name} has no RECORD"
        direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
        assert not direct_url.get("dir_info", {}).get("editable"), (
            f"Editable {distribution_name} is not wheel qualification")
        recorded = {str(path): path for path in distribution.files or []}
        entry = recorded.get(package + "/__init__.py")
        assert entry is not None, f"No installed package RECORD entry: {package}"
        expected_init = Path(distribution.locate_file(entry)).resolve(strict=True)
        installed = paths[module_id].resolve(strict=True)
        source = warmed_paths[module_id].resolve(strict=True)
        assert expected_init == installed / "__init__.py", (
            f"{package} resolves outside its installed distribution")
        assert not installed.is_relative_to(source) and not source.is_relative_to(installed), (
            f"{package} must run from its installed wheel, not the warmed checkout")
        spec = importlib.util.find_spec(package)
        assert spec is not None and spec.origin, f"No runtime package origin: {package}"
        assert Path(spec.origin).resolve(strict=True) == expected_init, (
            f"{package} is shadowed by another package")

        expected = {path.relative_to(source).as_posix() for path in source.rglob("*.py")}
        actual = {path.relative_to(installed).as_posix() for path in installed.rglob("*.py")}
        package_records = {
            name.removeprefix(package + "/"): record
            for name, record in recorded.items()
            if name.startswith(package + "/") and name.endswith(".py")
        }
        assert actual == expected == set(package_records), (
            f"{package} Python file sets differ between wheel, RECORD and warmed source")
        hashes = {}
        for relative in sorted(expected):
            path = (installed / relative).resolve(strict=True)
            source_path = (source / relative).resolve(strict=True)
            assert path.is_relative_to(installed), f"Installed file escapes package: {relative}"
            assert source_path.is_relative_to(source), f"Warmed file escapes package: {relative}"
            record = package_records[relative]
            assert Path(distribution.locate_file(record)).resolve(strict=True) == path, relative
            assert record.hash is not None and record.hash.mode == "sha256", (
                f"Missing SHA-256 RECORD hash: {package}/{relative}")
            data = path.read_bytes()
            digest = hashlib.sha256(data)
            encoded = base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=")
            assert encoded == record.hash.value and len(data) == record.size, (
                f"Installed file differs from wheel RECORD: {package}/{relative}")
            assert data == source_path.read_bytes(), (
                f"Installed file differs from warmed source: {package}/{relative}")
            hashes[relative] = digest.hexdigest()
        for name, module in list(sys.modules.items()):
            if name != package and not name.startswith(package + "."):
                continue
            origin = getattr(module, "__file__", None)
            assert origin is not None, f"Loaded upstream module has no origin: {name}"
            path = Path(origin).resolve(strict=True)
            assert path.is_relative_to(installed), f"Shadowed upstream module: {name}"
            assert path.relative_to(installed).as_posix() in hashes, (
                f"Loaded upstream module is not verified Python source: {name}")
        evidence[module_id] = {
            "distribution": distribution_name, "version": distribution.version,
            "origin": str(expected_init), "source": str(source), "python_sha256": hashes,
        }
    return evidence


def exact_package(name, package_path):
    module = importlib.import_module(name)
    package_path = package_path.resolve()
    assert Path(module.__file__).resolve() == package_path / "__init__.py", f"Wrong runtime origin for {name}"
    for imported_name, imported in list(sys.modules.items()):
        if imported_name.startswith(name + ".") and getattr(imported, "__file__", None):
            assert Path(imported.__file__).resolve().is_relative_to(package_path), imported_name
    return module


def provider(instance, model, priority, models):
    return {"module": PROVIDER_MODULE, "id": instance, "instance_id": instance,
            "config": {"default_model": model, "priority": priority, "probe_models": models}}


def preference(instance, model):
    return [{"provider": instance, "model": model}]


async def run(paths, warmed_paths, routing_root, distribution, attempts, negative_control=False):
    upstream = verify_upstream(paths, warmed_paths)
    from amplifier_core import AmplifierSession, HookResult
    from amplifier_core._engine import RustSession
    from amplifier_core.events import MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import PersistentDelegate, install_children
    from amplifier_web.host.model_selection import prepare_default_routing
    from amplifier_web.host.session import SelectedProvider
    from amplifier_web.host.storage import SessionStore

    assert AmplifierSession is RustSession, "This probe requires real Rust Core sessions"
    assert "before_initialize" in inspect.signature(PreparedBundle.create_session).parameters, (
        "The warmed Foundation lacks the host's early before_initialize API")
    verify_wheel(distribution)
    executions = []
    creations = []
    expected = {
        "root-before": ("alpha", "conversation-v1"),
        "agent-pin-vs-role": ("alpha", "agent-pin-v1"),
        "call-pin-vs-agent-pin": ("beta", "call-pin-v1"),
        "call-role-vs-agent-pin": ("alpha", "agent-pin-v1"),
        "role-only-custom-matrix": ("beta", "matrix-coding-v1"),
        "call-role-vs-agent-role": ("alpha", "matrix-review-v1"),
        "declared-provider-vs-role": ("beta", "declared-pin-v1"),
        "call-role-vs-declared-provider": ("beta", "declared-pin-v1"),
        "call-pin-vs-declared-provider": ("alpha", "caller-alt-v1"),
        "unspecified-conversation": ("alpha", "conversation-v1"),
        "root-after-change": ("alpha", "conversation-v2"),
        "resume-agent-pin": ("alpha", "agent-pin-v1"),
        "resume-call-pin": ("beta", "call-pin-v1"),
        "resume-role-only": ("beta", "matrix-coding-v1"),
        "resume-unspecified": ("alpha", "conversation-v1"),
    }

    class FiniteOrchestrator(BundleLiveOrchestrator):
        """Retain the real loop provider selector, never run its model loop."""
        def __init__(self, config, audit):
            super().__init__(config)
            self.audit = audit

        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            audit = self.audit
            assert not audit["failures"], audit["failures"]
            lifecycle = "session:resume" if audit["resumed"] else "session:start"
            assert audit["lifecycle"] == [lifecycle], audit
            assert len(audit["matrix_events"]) == 1, "Real routing lifecycle hook did not run exactly once"
            assert coordinator.get_capability("routing.defer_agent_resolution") is True
            assert coordinator.get_capability("web.default_routing_policy") is True
            resolver = coordinator.get_capability("model_role_resolver")
            assert type(resolver).__module__ == "amplifier_module_hooks_routing.resolver_class"
            assert resolver.name == MATRIX_NAME and resolver.matrix_source == "user"
            assert Path(resolver.matrix_path).resolve() == matrix_file
            assert coordinator.config["agents"]["pinned"]["provider_preferences"] == agent_pin, (
                "Lifecycle routing overwrote the named agent pin")
            assert "provider_preferences" not in coordinator.config["agents"]["role-only"], (
                "The early policy failed to defer unrelated agent resolution")
            selected = self._select_provider(providers)
            underlying = selected.original if isinstance(selected, SelectedProvider) else selected
            assert all(value.policy_at_mount for value in providers.values()), (
                "The default routing policy was not present at real provider mount time")
            instance, = [name for name, value in providers.items() if value is underlying]
            actual = (instance, selected.default_model)
            assert actual == expected[prompt], (
                f"effective provider/model mismatch for {prompt}: expected {expected[prompt]!r}, got {actual!r}")
            assert selected.get_info().defaults["model"] == actual[1], "Provider metadata disagrees with default_model"
            row = {"case": prompt, "session_id": coordinator.session_id,
                   "provider": instance, "default_model": actual[1],
                   "lifecycle": lifecycle, "matrix_events": len(audit["matrix_events"])}
            executions.append(row)
            await context.add_message({"role": "user", "content": prompt})
            output = json.dumps(row, sort_keys=True)
            await context.add_message({"role": "assistant", "content": output})
            await hooks.emit("orchestrator:complete", {"status": "success", "turn_count": 1})
            return output

    @dataclass
    class ProbePrepared(PreparedBundle):
        async def create_session(self, **kwargs):
            # Do not repair a missing callback here: the real host child/resume
            # path must supply it. Observers only record lifecycle evidence.
            original = kwargs.pop("before_initialize", None)
            assert original is prepare_default_routing, "Host omitted early default-routing policy"
            audit = {"session_id": kwargs.get("session_id"), "parent_id": kwargs.get("parent_id"),
                     "resumed": kwargs.get("is_resumed", False), "lifecycle": [],
                     "matrix_events": [], "failures": []}

            async def before_initialize(session):
                assert not session.initialized, "Routing policy was installed after initialization"
                await original(session)
                assert session.coordinator.get_capability("routing.defer_agent_resolution") is True

                async def observe(event, data):
                    if event in {"session:start", "session:resume"}:
                        audit["lifecycle"].append(event)
                    elif event == "routing:matrix-loaded":
                        audit["matrix_events"].append(copy.deepcopy(data))
                    else:
                        audit["failures"].append({"event": event, "data": str(data)})
                    return HookResult()

                for event in ("session:start", "session:resume", "routing:matrix-loaded",
                              MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED):
                    session.coordinator.hooks.register(event, observe, priority=999, name="probe-" + event)
                if negative_control and kwargs.get("parent_id"):
                    async def corrupt(event, data):
                        for mounted in session.coordinator.get("providers").values():
                            mounted.default_model = "synthetic-regression-v1"
                        return HookResult()
                    session.coordinator.hooks.register("session:start", corrupt, priority=1000,
                                                       name="probe-negative-control")

            session = await super().create_session(before_initialize=before_initialize, **kwargs)
            assert isinstance(session, RustSession)
            creations.append(audit)
            config = session.coordinator.config["session"]["orchestrator"].get("config", {})
            await session.coordinator.mount("orchestrator", FiniteOrchestrator(config, audit))
            return session

    async def exercise_nested_desktop(plan, module_paths, root):
        """Observe policy at execution, across Core and disk, without repairing it."""
        package = root / DESKTOP_PACKAGE
        package.mkdir()
        (package / "__init__.py").write_text(DESKTOP_SOURCE, encoding="utf-8")
        desktop_paths = {**module_paths, DESKTOP_MODULE: package}
        observations = []
        checkpoints = []
        desktop_store = SessionStore(root / "desktop-sessions")

        def desktop_rows(config):
            return [row for row in config.get("tools", []) if row["module"] == DESKTOP_MODULE]

        class DesktopOrchestrator(BundleLiveOrchestrator):
            def __init__(self, config, audit):
                super().__init__(config)
                self.audit = audit

            async def execute(self, prompt, context, providers, tools, hooks, coordinator):
                audit = self.audit
                lifecycle = "session:resume" if audit["resumed"] else "session:start"
                assert not audit["failures"], audit["failures"]
                assert audit["lifecycle"] == [lifecycle], audit
                assert len(audit["matrix_events"]) == 1
                registry = coordinator.get_capability("live.children")
                session = registry.sessions[coordinator.session_id]
                assert isinstance(session, RustSession) and session.initialized
                assert session.coordinator is coordinator
                policy = copy.deepcopy(coordinator.config.get("_unified_desktop_policy"))
                declarations = desktop_rows(coordinator.config)
                fake = exact_package(DESKTOP_PACKAGE, package)
                mounted = [tool for tool in tools.values() if isinstance(tool, fake.DesktopProbeTool)]
                middle = prompt in {"desktop-middle", "desktop-middle-resumed"}
                grandchild = prompt in {"desktop-grandchild", "desktop-grandchild-after-resume"}
                assert middle or grandchild or prompt in {"desktop-root", "desktop-root-without-tool"}
                if middle or grandchild:
                    assert policy == DESKTOP_CEILING, (
                        f"Desktop ceiling lost through Core/PreparedBundle at {prompt}: {policy!r}")
                if middle or prompt == "desktop-root-without-tool":
                    assert declarations == [] and mounted == [], (
                        f"{prompt} must omit desktop rather than retain a restrictive tool")
                    assert "desktop_policy_probe" not in tools and "computer" not in tools
                    effective = None
                    instance = None
                    if not middle:
                        assert policy is None, "Fresh root must not supply the saved child's ceiling"
                else:
                    assert len(declarations) == len(mounted) == 1
                    instance = declarations[0]["instance_id"]
                    assert instance == ("desktop-grandchild-instance" if grandchild else "desktop-root-instance")
                    result = await mounted[0].execute({"action": "inspect"})
                    assert result.success, result.error
                    effective = result.output
                    assert effective["read_only"] is True and effective["gate_writes"] is True, (
                        f"Mounted desktop ceiling widened at {prompt}: {effective!r}")
                    assert effective == declarations[0]["config"]
                row = {"case": prompt, "session_id": coordinator.session_id,
                       "parent_id": audit["parent_id"], "lifecycle": lifecycle,
                       "policy": policy, "mounted_config": effective, "instance_id": instance}
                observations.append(row)
                if middle:
                    delegate = tools["delegate"]
                    assert isinstance(delegate, PersistentDelegate)
                    assert isinstance(delegate.original, exact_package(
                        "amplifier_module_tool_delegate", paths["tool-delegate"]).DelegateTool)
                    nested_prompt = ("desktop-grandchild-after-resume" if audit["resumed"]
                                     else "desktop-grandchild")
                    result = await delegate.execute({
                        "agent": "desktop-grandchild", "instruction": nested_prompt,
                        "context_depth": "none",
                    })
                    assert result.success, f"Nested real delegate failed: {result.error}"
                    nested = json.loads(result.output["response"])
                    assert nested == observations[-1] and nested["case"] == nested_prompt
                    assert nested["parent_id"] == coordinator.session_id
                    assert nested["session_id"] == result.output["session_id"]
                    row["grandchild_id"] = nested["session_id"]
                    saved_messages, saved = desktop_store.load(nested["session_id"])
                    assert saved["parent_id"] == coordinator.session_id
                    assert saved["mount_plan"]["_unified_desktop_policy"] == DESKTOP_CEILING
                    declared = saved["agent_overlay"]["tools"][0]
                    assert declared["instance_id"] == "desktop-grandchild-instance"
                    assert declared["config"] == {"read_only": False, "gate_writes": False}
                    assert saved["agent_overlay"]["_unified_desktop_policy"] == {}
                    assert nested["mounted_config"] == DESKTOP_CEILING
                    assert desktop_rows(saved["mount_plan"])[0]["config"] == nested["mounted_config"]
                    assert json.loads(saved_messages[-1]["content"]) == nested
                await context.add_message({"role": "user", "content": prompt})
                output = json.dumps(row, sort_keys=True)
                await context.add_message({"role": "assistant", "content": output})
                await hooks.emit("orchestrator:complete", {"status": "success", "turn_count": 1})
                return output

        @dataclass
        class DesktopPrepared(ProbePrepared):
            async def create_session(self, **kwargs):
                # Keep the same real initialization and lifecycle observers.
                # Replace only the finite turn body; never copy policy into Core.
                session = await super().create_session(**kwargs)
                audit = creations[-1]
                assert audit["session_id"] == session.session_id
                config = session.coordinator.config["session"]["orchestrator"].get("config", {})
                await session.coordinator.mount("orchestrator", DesktopOrchestrator(config, audit))
                return session

        desktop_plan = copy.deepcopy(plan)
        desktop_plan["tools"].append({
            "module": DESKTOP_MODULE, "instance_id": "desktop-root-instance",
            "config": copy.deepcopy(DESKTOP_CEILING),
        })
        # Exclude desktop from inherited tools, including when the intermediate
        # child resumes. The grandchild explicitly redeclares it after exclusion.
        desktop_plan["spawn"] = {"exclude_tools": [DESKTOP_MODULE]}
        delegate_declaration = copy.deepcopy(plan["tools"][0])
        delegate_declaration["config"]["settings"]["exclude_tools"] = []
        desktop_plan["agents"].update({
            # The ordinary root delegate excludes itself; this overlay explicitly
            # reinstates the real delegate so this child can invoke its own child.
            "desktop-middle": {"tools": [delegate_declaration]},
            "desktop-grandchild": {
                "tools": [{"module": DESKTOP_MODULE, "instance_id": "desktop-grandchild-instance",
                           "config": {"read_only": False, "gate_writes": False}}],
                "_unified_desktop_policy": {},
            },
        })
        middle_id = None
        previous_messages = None
        for resumed in (False, True):
            current_plan = copy.deepcopy(desktop_plan)
            if resumed:
                # A genuinely fresh Core root/registry has no desktop tool or
                # policy. Only saved child metadata can retain the old ceiling.
                current_plan["tools"] = [row for row in current_plan["tools"]
                                         if row["module"] != DESKTOP_MODULE]
                assert "_unified_desktop_policy" not in current_plan
            prepared = DesktopPrepared(
                current_plan, BundleModuleResolver(desktop_paths),
                Bundle.from_dict(copy.deepcopy(current_plan), base_path=root),
            )
            desktop_parent = await prepared.create_session(
                session_id="desktop-parent", session_cwd=root, approval_system=approvals,
                before_initialize=prepare_default_routing,
            )
            try:
                registry = await install_children(
                    desktop_parent, prepared, Runtime(desktop_parent.session_id), desktop_store, approvals)
                assert registry.rows == {}
                await desktop_parent.execute("desktop-root-without-tool" if resumed else "desktop-root")
                supplied = {"session_id": middle_id} if resumed else {"agent": "desktop-middle"}
                prompt = "desktop-middle-resumed" if resumed else "desktop-middle"
                result = await desktop_parent.coordinator.get("tools")["delegate"].execute({
                    **supplied, "instruction": prompt, "context_depth": "none",
                })
                assert result.success, f"{prompt}: real delegate failed: {result.error}"
                observation = json.loads(result.output["response"])
                assert observation == next(row for row in observations if row["case"] == prompt)
                identity = result.output["session_id"]
                assert observation["session_id"] == identity
                assert observation["parent_id"] == desktop_parent.session_id
                assert observation["lifecycle"] == ("session:resume" if resumed else "session:start")
                if resumed:
                    assert identity == middle_id
                else:
                    middle_id = identity
                messages, metadata = desktop_store.load(identity)
                assert metadata["parent_id"] == desktop_parent.session_id
                assert metadata["status"] == registry.rows[identity]["status"] == "completed"
                saved_plan = metadata["mount_plan"]
                assert desktop_rows(saved_plan) == []
                assert saved_plan["_unified_desktop_policy"] == DESKTOP_CEILING
                assert json.loads(messages[-1]["content"]) == observation
                if resumed:
                    assert messages[:len(previous_messages)] == previous_messages
                    assert len(messages) > len(previous_messages)
                previous_messages = copy.deepcopy(messages)
                checkpoints.append({
                    "session_id": identity, "resumed": resumed,
                    "policy": copy.deepcopy(saved_plan["_unified_desktop_policy"]),
                    "desktop_tools": desktop_rows(saved_plan), "message_count": len(messages),
                })
            finally:
                await desktop_parent.cleanup()
        return {"executions": observations, "checkpoints": checkpoints,
                "fixture_package": DESKTOP_PACKAGE}

    async def deny(prompt, choices):
        raise AssertionError("Offline routing should not request approval")

    # All generated fixture data and host checkpoints belong to this invocation's
    # working folder. The pytest driver supplies isolated homes before imports.
    with tempfile.TemporaryDirectory(prefix="routing-probe-", dir=Path.cwd()) as temporary:
        root = Path(temporary)
        package = root / PROVIDER_PACKAGE
        package.mkdir()
        (package / "__init__.py").write_text(PROVIDER_SOURCE, encoding="utf-8")
        matrix_dir = root / "custom-routing"
        matrix_dir.mkdir()
        matrix_file = matrix_dir / f"{MATRIX_NAME}.yaml"
        # JSON is valid YAML. Exact names avoid network-dependent glob discovery.
        matrix_file.write_text(json.dumps({"name": MATRIX_NAME, "description": "Offline integration fixture", "roles": {
            "coding": {"description": "Synthetic coding route",
                       "candidates": [{"provider": "beta", "model": "matrix-coding-v1"}]},
            "review": {"description": "Synthetic review route",
                       "candidates": [{"provider": "alpha", "model": "matrix-review-v1"}]},
            "unavailable": {"description": "Deliberately unavailable route",
                            "candidates": [{"provider": "not-mounted", "model": "unavailable-v1"}]},
        }}), encoding="utf-8")
        agent_pin = preference("alpha", "agent-pin-v1")
        agents = {
            "pinned": {"model_role": "coding", "provider_preferences": agent_pin},
            "declared": {"model_role": "coding", "providers": [
                provider("beta", "declared-pin-v1", 100, BETA_MODELS)]},
            "role-only": {"model_role": "coding"},
            "plain": {},
            "unroutable": {"model_role": "unavailable"},
        }
        plan = {
            "session": {"orchestrator": {"module": "loop-live", "config": {"inherit_effective_model": True}},
                        "context": {"module": "context-simple"}},
            "providers": [provider("alpha", "alpha-default-v1", 0, ALPHA_MODELS),
                          provider("beta", "beta-default-v1", 50, BETA_MODELS)],
            "agents": copy.deepcopy(agents),
            "hooks": [{"module": "hooks-routing", "config": {
                "default_matrix": MATRIX_NAME, "custom_routing_dirs": [str(matrix_dir)],
                "_bundle_root": str(routing_root)}}],
            "tools": [{"module": "tool-delegate", "config": {
                "features": {"provider_selection": {"enabled": True},
                             "return_contract": {"enabled": False}},
                "settings": {"timeout": 10}}}],
        }
        module_paths = {**paths, PROVIDER_MODULE: package}
        bundle = Bundle.from_dict(copy.deepcopy(plan), base_path=root)
        prepared = ProbePrepared(plan, BundleModuleResolver(module_paths), bundle)
        approvals = Approvals(None, deny)
        parent = await prepared.create_session(session_id="routing-parent", session_cwd=root,
                                              approval_system=approvals,
                                              before_initialize=prepare_default_routing)
        try:
            delegate_module = exact_package("amplifier_module_tool_delegate", paths["tool-delegate"])
            exact_package("amplifier_module_hooks_routing", paths["hooks-routing"])
            synthetic = exact_package(PROVIDER_PACKAGE, package)
            mounted = parent.coordinator.get("providers")
            assert set(mounted) == {"alpha", "beta"}, "Synthetic providers failed to mount"
            for instance, models in (("alpha", ALPHA_MODELS), ("beta", BETA_MODELS)):
                assert [model.id for model in await mounted[instance].list_models()] == models
            loop = parent.coordinator.get("orchestrator")
            loop.root_provider = SelectedProvider(mounted["alpha"], {"instance": "alpha", "model": "conversation-v1"})
            store = SessionStore(root / "sessions")
            registry = await install_children(parent, prepared, Runtime(parent.session_id), store, approvals)
            wrapper = parent.coordinator.get("tools")["delegate"]
            assert isinstance(wrapper, PersistentDelegate)
            assert isinstance(wrapper.original, delegate_module.DelegateTool)
            assert "persistent" in wrapper.input_schema["properties"]
            await parent.execute("root-before")

            async def invoke(case, supplied):
                before = len(executions)
                result = await wrapper.execute({"instruction": case, "context_depth": "none", **supplied})
                assert result.success, f"{case}: delegate failed: {result.error}"
                assert len(executions) == before + 1, f"{case}: no real child execution"
                observation = executions[-1]
                assert observation["case"] == case
                assert json.loads(result.output["response"]) == observation
                identity = result.output["session_id"]
                assert observation["session_id"] == identity
                assert registry.rows[identity]["status"] == "completed"
                messages, metadata = store.load(identity)
                assert metadata["parent_id"] == parent.session_id
                assert json.loads(messages[-1]["content"]) == observation
                return identity

            ids = {}
            for case, supplied in (
                ("agent-pin-vs-role", {"agent": "pinned"}),
                ("call-pin-vs-agent-pin", {"agent": "pinned", "model_role": "review",
                                         "provider_preferences": preference("beta", "call-pin-v1")}),
                ("call-role-vs-agent-pin", {"agent": "pinned", "model_role": "review"}),
                ("role-only-custom-matrix", {"agent": "role-only"}),
                ("call-role-vs-agent-role", {"agent": "role-only", "model_role": "review"}),
                ("declared-provider-vs-role", {"agent": "declared"}),
                ("call-role-vs-declared-provider", {"agent": "declared", "model_role": "review"}),
                ("call-pin-vs-declared-provider", {"agent": "declared",
                                                 "provider_preferences": preference("alpha", "caller-alt-v1")}),
                ("unspecified-conversation", {"agent": "plain"}),
            ):
                ids[case] = await invoke(case, supplied)

            # Real DelegateTool failures must surface and must not execute the
            # parent's default provider. Neither is a composition-only check.
            rejected = []
            for case, supplied, message in (
                ("reject-explicit-provider", {"agent": "pinned", "provider_preferences":
                                             preference("not-mounted", "unavailable-v1")}, "no default was substituted"),
                ("reject-agent-role", {"agent": "unroutable"}, "no available candidates"),
            ):
                before = (len(executions), len(creations))
                result = await wrapper.execute({"instruction": case, "context_depth": "none", **supplied})
                assert not result.success, f"{case}: silently executed a fallback"
                assert message in str(result.error), result.error
                assert (len(executions), len(creations)) == before, f"{case}: child was admitted"
                rejected.append({"case": case, "error": result.error})

            # A new tool object has no in-memory routing cache. Resume must use
            # the host's saved metadata, even after the conversation changes.
            fresh = delegate_module.DelegateTool(parent.coordinator, {
                "features": {"provider_selection": {"enabled": True}, "return_contract": {"enabled": False}},
                "settings": {"timeout": 10}})
            await parent.coordinator.mount("tools", fresh, name="delegate")
            await registry.install(parent, prepared)
            wrapper = parent.coordinator.get("tools")["delegate"]
            assert isinstance(wrapper, PersistentDelegate) and wrapper.original is fresh
            loop.root_provider = SelectedProvider(mounted["alpha"], {"instance": "alpha", "model": "conversation-v2"})
            await parent.execute("root-after-change")
            for case, original_case in (
                ("resume-agent-pin", "agent-pin-vs-role"),
                ("resume-call-pin", "call-pin-vs-agent-pin"),
                ("resume-role-only", "role-only-custom-matrix"),
                ("resume-unspecified", "unspecified-conversation"),
            ):
                identity = ids[original_case]
                prior_messages = copy.deepcopy(store.load(identity)[0])
                assert await invoke(case, {"session_id": identity}) == identity
                assert store.load(identity)[0][:len(prior_messages)] == prior_messages
                assert executions[-1]["lifecycle"] == "session:resume"
            assert len(executions) == len(expected)
            assert {row["case"] for row in executions} == set(expected)
            nested_desktop = await exercise_nested_desktop(plan, module_paths, root)
            assert not synthetic.COMPLETION_ATTEMPTS, "A model completion was attempted"
            assert not attempts, f"Forbidden offline effects were attempted: {attempts}"
            wheel = verify_wheel(distribution)
            exact_package("amplifier_module_tool_delegate", paths["tool-delegate"])
            exact_package("amplifier_module_hooks_routing", paths["hooks-routing"])
            report = {"status": "passed", "installed_wheel": wheel,
                      "tool_delegate": str(paths["tool-delegate"]), "routing_hook": str(paths["hooks-routing"]),
                      "executions": executions, "rejected": rejected, "nested_desktop": nested_desktop,
                      "model_calls": 0, "forbidden_effects": attempts}
        finally:
            await parent.cleanup()
        assert not attempts, f"Forbidden effects during cleanup: {attempts}"
        assert not synthetic.COMPLETION_ATTEMPTS, "Cleanup attempted a model call"
        assert verify_upstream(paths, warmed_paths) == upstream, (
            "Installed upstream packages or warmed sources changed during the probe")
        report["upstream_wheels"] = upstream
        emit(report)


def main():
    assert sys.flags.isolated, "Run this probe with Python -I; source/PYTHONPATH qualification is forbidden"
    assert not sys.flags.optimize, "Routing acceptance requires enabled assertions"
    assert sys.argv[1:] in ([], ["--preflight"], ["--negative-control"]), "Unknown probe arguments"
    attempts = offline_guard()
    prerequisites, missing = prerequisite_paths()
    if missing:
        emit({"status": "missing_prerequisites", "missing": missing})
        return 77
    paths, warmed_paths, routing_root, distribution = prerequisites
    verify_wheel(distribution)
    if sys.argv[1:] == ["--preflight"]:
        upstream = verify_upstream(paths, warmed_paths)
        emit({"status": "ready", "version": distribution.version, "upstream_wheels": upstream})
        return 0
    # Deliberately no setup/install/fallback here. The caller owns these homes.
    for name in ("AMPLIFIER_WEB_HOME", "AMPLIFIER_HOME", "AMPLIFIER_SESSION_STATE_HOME"):
        assert os.environ.get(name), f"The test driver must supply an isolated {name}"
        assert Path(os.environ[name]).resolve().is_relative_to(Path.cwd().resolve()), name
    asyncio.run(run(paths, warmed_paths, routing_root, distribution, attempts,
                    negative_control=sys.argv[1:] == ["--negative-control"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
