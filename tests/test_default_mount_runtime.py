"""Opt-in, provider-free mount/export checks in the owned qualification container.

Run beside test_default_behaviors_integration.py and test_default_runtime_safety.py
with UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos, using the installed runtime Python
outside the checkout (pytest --import-mode=importlib). No prepare/install/activation,
model turn, image request, desktop action, or network access is performed. The only
permitted subprocesses are the resource collector's bounded local Git rev-parse
queries. Missing known prerequisites skip; broken mounts/resources/provenance fail.

The composition fixture's sole synthetic provider declaration is removed through
the public Bundle.providers field before preparation; NO provider is substituted.
All five real default behaviors, Core, Foundation, host live-plan adaptation and
module mount functions execute. Mount/CI-constructor wrappers only observe calls.
This is not full host launch, provider/delegation execution, desktop backend, export
reload, or production-account qualification. In particular, do not repair missing
installed routing resources with _bundle_root or fabricate routingMatrix/baseRoles.
"""
from __future__ import annotations

import asyncio
import base64
from collections import Counter
import copy
from functools import wraps
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


if not os.environ.get("UNIFIED_BEHAVIOR_REPOS"):
    pytest.skip("Requires owned DTU and UNIFIED_BEHAVIOR_REPOS", allow_module_level=True)
pytest_asyncio = pytest.importorskip("pytest_asyncio", reason="Prepared async test dependency")
yaml = pytest.importorskip("yaml", reason="Prepared PyYAML dependency")

_path = Path(__file__).with_name("test_default_runtime_safety.py")
if not _path.is_file():
    pytest.skip("Copy the runtime safety support beside this test", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("_unified_default_mount_support", _path)
assert _spec is not None and _spec.loader is not None
_safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_safety)
_support = _safety._support
offline_defaults = _support.offline_defaults

NESTED = {
    "tool-skills": "skills", "tool-image": "imagegen",
    "tool-computer-use": "computer-use", "hook-computer-use": "computer-use",
    "hooks-routing": "routing-matrix", "hooks-mode": "modes", "tool-mode": "modes",
    "tool-delegate": "foundation", "hooks-session-naming": "foundation",
    **{name: "context-intelligence" for name in (
        "hook-context-intelligence", "hook-server-data-ops-lockdown",
        "tool-context-intelligence-query", "tool-context-intelligence-recover",
        "tool-context-intelligence-set-filters", "tool-context-intelligence-transcript",
        "tool-context-intelligence-upload", "tool-server-data-ops",
    )},
}
STANDALONE = {name: "amplifier-module-" + name for name in (
    "loop-live", "loop-streaming", "context-simple", "hooks-approval",
)}


def _module(env, module_id):
    """Reuse safety provenance for bundled modules; also attest wheel RECORD."""
    package = "amplifier_module_" + module_id.replace("-", "_")
    distribution = "amplifier-module-" + module_id
    if module_id in NESTED:
        namespace = NESTED[module_id]
        module = _safety._upstream(env, namespace, module_id, package, distribution)
        source = env.roots[namespace] / "modules" / module_id / package
    else:
        assert module_id in STANDALONE, f"Unreviewed module prerequisite: {module_id}"
        source = Path(os.environ["UNIFIED_BEHAVIOR_REPOS"]) / STANDALONE[module_id] / package
        if not (source / "__init__.py").is_file():
            pytest.skip(f"Prepared standalone module source missing: {module_id}")
        _safety._require_distribution(distribution)
        module = _support._installed(package, distribution)
    installed = Path(module.__file__).resolve().parent
    source = source.resolve(strict=True)
    assert not installed.is_relative_to(source) and not source.is_relative_to(installed)
    expected = {path.relative_to(source) for path in source.rglob("*.py")}
    actual = {path.relative_to(installed) for path in installed.rglob("*.py")}
    assert actual == expected, f"Installed/source Python file sets differ: {module_id}"
    dist = importlib.metadata.distribution(distribution)
    assert dist.read_text("WHEEL") and dist.read_text("RECORD"), distribution
    records = {str(path): path for path in dist.files or ()}
    recorded_python = {Path(name.removeprefix(package + "/")) for name in records
                       if name.startswith(package + "/") and name.endswith(".py")}
    assert recorded_python == expected, f"Wheel RECORD/source file sets differ: {module_id}"
    hashes = {}
    for relative in sorted(expected):
        path = (installed / relative).resolve(strict=True)
        original = (source / relative).resolve(strict=True)
        assert path.is_relative_to(installed) and original.is_relative_to(source)
        data = path.read_bytes()
        assert data == original.read_bytes(), f"Installed/source bytes differ: {module_id}/{relative}"
        record = records.get(package + "/" + relative.as_posix())
        assert record is not None and record.hash is not None, (module_id, relative)
        assert Path(dist.locate_file(record)).resolve() == path
        assert record.hash.mode == "sha256" and record.size == len(data)
        digest = hashlib.sha256(data)
        assert base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=") == record.hash.value
        hashes[relative.as_posix()] = digest.hexdigest()
    for name, loaded in tuple(sys.modules.items()):
        if name == package or name.startswith(package + "."):
            assert Path(loaded.__file__).resolve().is_relative_to(installed), name
    return module, {"origin": str(installed), "python_sha256": hashes}


@pytest.fixture
def mount_environment(request, monkeypatch, tmp_path):
    assert Path("/run/systemd/container").exists() or Path("/.dockerenv").exists(), (
        "Full-composition mounting is container-only, never a host test"
    )
    for distribution in ("amplifier-unified", "amplifier-foundation", "amplifier-core"):
        _safety._require_distribution(distribution)
    _safety._require_distribution("amplifier-module-loop-live")
    env = request.getfixturevalue("offline_defaults")
    monkeypatch.chdir(env.workspace)
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SSH_AUTH_SOCK",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(key, raising=False)
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        path = tmp_path / key.lower()
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")

    # Tripwire, not a sandbox. Record even swallowed attempts. No connect/bind,
    # including Unix/X11 sockets, and no installer/shell processes are allowed.
    # Audit hooks cannot be removed; deactivate this one after owned cleanup.
    attempts, git_reads = [], []
    active = True
    roots = tuple(path.resolve() for path in env.roots.values())

    def audit(event, args):
        if not active:
            return
        if event == "subprocess.Popen":
            argv = args[1]
            if (isinstance(argv, (list, tuple)) and len(argv) == 5
                    and Path(str(args[0])).name == "git" and argv[0] == "git"
                    and argv[1] == "-C" and argv[3] == "rev-parse"
                    and argv[4] in {"HEAD", "--show-toplevel"}
                    and any(Path(argv[2]).resolve().is_relative_to(root) for root in roots)):
                git_reads.append(list(argv))
                return
        elif event not in {
            "socket.connect", "socket.connect_ex", "socket.getaddrinfo",
            "socket.gethostbyname", "socket.bind", "socket.sendto",
            "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty",
        }:
            return
        attempts.append(event)
        raise RuntimeError(f"Offline mount/export forbids {event}")

    sys.addaudithook(audit)
    try:
        yield SimpleNamespace(env=env, attempts=attempts, git_reads=git_reads)
    finally:
        active = False
        assert not attempts, f"Forbidden effects were attempted, even if caught: {attempts}"


def _preserve_remote_provenance(env, monkeypatch):
    """Keep real Git origins while delegating all reads to the offline whitelist.

    The composition-only fixture rewrites origins to file: URIs. Export needs
    the original provenance, so move ONLY that exact URI-to-local mapping to
    the source resolver. No registry rows or collector results are fabricated.
    """
    from amplifier_foundation.sources import SimpleSourceResolver

    offline_resolve = SimpleSourceResolver.resolve
    mapping = {key: value for key, value in env.sources.items() if key.startswith("git+")}

    async def resolve(resolver, uri):
        return await offline_resolve(resolver, mapping.get(uri, uri))

    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    sources = {key: key for key in mapping}
    for namespace, repository in _support.REPOSITORIES.items():
        remote = f"git+https://github.com/microsoft/{repository}@main"
        sources[namespace] = remote
        # Namespaced manifest aliases in the existing whitelist retain their
        # exact relative paths, including the actual filename extension.
        for key, local in env.sources.items():
            if key.startswith(namespace + ":") and "#subdirectory=" in local:
                sources[key] = remote + "#" + local.split("#", 1)[1]
    return {"sources": {"bundles": sources}}


def _observe_mount(monkeypatch, module, module_id, completed):
    original = module.mount
    assert inspect.iscoroutinefunction(original), module_id

    @wraps(original)
    async def observe(coordinator, config=None):
        before_tools = dict(coordinator.get("tools") or {})
        before_hooks = {(event, name) for event, names in coordinator.hooks.list_handlers().items()
                        for name in names}
        received = copy.deepcopy(config or {})
        result = await original(coordinator, config)
        after_tools = coordinator.get("tools") or {}
        completed.append({
            "module": module_id, "config": received,
            "tools": {name: tool for name, tool in after_tools.items()
                      if before_tools.get(name) is not tool},
            "handlers": {(event, name) for event, names in coordinator.hooks.list_handlers().items()
                         for name in names} - before_hooks,
        })
        return result

    monkeypatch.setattr(module, "mount", observe)


@pytest_asyncio.fixture
async def mounted_defaults(mount_environment, monkeypatch):
    env = mount_environment.env
    _support._installed("amplifier_core", "amplifier-core")
    from amplifier_core import HookResult
    from amplifier_core.events import MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED
    from amplifier_foundation.bundle import BundleModuleResolver, PreparedBundle
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.model_selection import prepare_default_routing
    from amplifier_web.host.session import live_plan
    from amplifier_web.runtime_controls import RuntimeControls

    assert "before_initialize" in inspect.signature(PreparedBundle.create_session).parameters
    registry, bundle, config = await env.compose(
        root_patch={
            "session": {"context": {
                "module": "context-simple",
                "source": "git+https://github.com/microsoft/amplifier-module-context-simple@main",
            }},
        },
        settings=_preserve_remote_provenance(env, monkeypatch),
    )
    assert set(config.app_bundles) == set(env.defaults.DEFAULT_BEHAVIORS)
    # HostConfig.merge combines module lists, so providers: [] would NOT clear
    # the composition fixture's sentinel. Remove only that exact known fixture
    # declaration through the public Bundle field; never invent its dependency.
    assert bundle.providers == [{"module": "provider-test", "config": {"model": "no-call"}}]
    bundle.providers = []
    original_roots = dict(bundle.source_base_paths)
    baseline = bundle.to_mount_plan()
    adapted, _ = live_plan(baseline)
    bundle.session = adapted["session"]
    bundle.agents = adapted.get("agents", {})
    # Use the same host-owned normalization as session.py, not a hand-built
    # five-module approximation. Keep the composed Bundle and its resource roots.
    components = bundle._host_components
    components.apply(bundle)
    plan = bundle.to_mount_plan()
    assert bundle.source_base_paths == original_roots
    assert plan.get("providers", []) == []
    declarations = [*plan.get("tools", []), *plan.get("hooks", []),
                    *[row for row in plan["session"].values()
                      if isinstance(row, dict) and "module" in row]]
    required = Counter(row["module"] for row in declarations)
    assert {"tool-skills", "tool-image", "tool-computer-use",
            "tool-context-intelligence-transcript", "hook-computer-use",
            "hook-context-intelligence", "hooks-routing"} <= required.keys()
    desktop_config = _support._row(plan["tools"], "tool-computer-use")["config"]
    assert desktop_config["read_only"] is True
    assert desktop_config.get("target") in (None, "", "local"), (
        "The probe must not mount a remote desktop target"
    )

    modules, provenance = {}, {}
    # loop-live imports streaming code although only loop-live is mounted.
    for module_id in sorted(set(required) | {"loop-streaming"}):
        modules[module_id], provenance[module_id] = _module(env, module_id)
    module_paths = {key: Path(value["origin"]) for key, value in provenance.items()}
    completed, failures, provider_requests, constructed_ci = [], [], [], []
    for module_id in required:
        _observe_mount(monkeypatch, modules[module_id], module_id, completed)

    ci_api = importlib.import_module(
        "amplifier_module_hook_context_intelligence.config_resolver"
    )
    original_ci_init = ci_api.HookConfigResolver.__init__

    @wraps(original_ci_init)
    def observe_ci(instance, *args, **kwargs):
        original_ci_init(instance, *args, **kwargs)
        constructed_ci.append(instance)

    monkeypatch.setattr(ci_api.HookConfigResolver, "__init__", observe_ci)
    prepared = PreparedBundle(copy.deepcopy(plan), BundleModuleResolver(module_paths), bundle)
    created = []
    controls = None

    async def before_initialize(session):
        created.append(session)  # Retain ownership even if initialization fails.
        assert not session.initialized
        await prepare_default_routing(session)

        async def failure(event, data):
            failures.append({"event": event, "data": copy.deepcopy(data)})
            return HookResult()

        async def no_provider_request(event, data):
            provider_requests.append(event)
            pytest.fail("Mount/export must never request a provider")

        for event in (MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED):
            session.coordinator.hooks.register(event, failure, priority=-1000,
                                               name="mount-probe-" + event)
        session.coordinator.hooks.register("provider:request", no_provider_request,
                                           priority=-1000, name="mount-probe-no-provider")

    try:
        session = await asyncio.wait_for(prepared.create_session(
            session_cwd=env.workspace, before_initialize=before_initialize,
        ), timeout=30)
        coordinator = session.coordinator
        assert not failures, failures
        assert Counter(row["module"] for row in completed) == required
        assert coordinator.get("orchestrator") is not None
        assert coordinator.get("context") is not None
        assert not coordinator.get("providers")
        tools = coordinator.get("tools") or {}
        for row in completed:
            if row["module"].startswith("tool-"):
                assert row["tools"], f"No real tool was mounted by {row['module']}"
                package = modules[row["module"]].__name__
                for name, tool in row["tools"].items():
                    assert tools[name] is tool
                    assert type(tool).__module__.split(".")[0] == package, (name, type(tool))
        # Some hook modules provide capabilities rather than event handlers;
        # their successful real mount is recorded above. Do not invent a
        # per-module handler contract. Routing does declare these exact hooks.
        routing_mount = next(row for row in completed if row["module"] == "hooks-routing")
        assert {("session:start", "routing-resolve"),
                ("session:resume", "routing-resolve-resume"),
                ("provider:request", "routing-context")} <= routing_mount["handlers"]
        desktop = next(row for row in completed if row["module"] == "tool-computer-use")
        assert desktop["config"]["read_only"] is True
        assert desktop["config"].get("target") in (None, "", "local")
        if "computer_use_unavailable" in tools:
            assert tools["computer_use_unavailable"].description.strip()
            assert not {"computer", "desktop"} & tools.keys()
        else:
            assert {"computer", "desktop"} <= tools.keys()
            # Real mount received read_only=True above. Behavioral denial is
            # covered separately by test_default_runtime_safety, not by guessing
            # a backend-specific private policy attribute or invoking a desktop.
        assert constructed_ci, "The real CI mount did not construct its config resolver"
        assert os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL"] == "https://ambient.invalid"
        for resolver in constructed_ci:
            assert resolver.destinations == {}
            assert resolver.validate_destinations() == {}
        assert not provider_requests and not mount_environment.attempts
        coordinator.register_capability("web.prepared", prepared)
        coordinator.register_capability("web.registry", registry)
        controls = RuntimeControls(session, Runtime(session.session_id))
        yield SimpleNamespace(env=env, session=session, controls=controls, config=config,
                              prepared=prepared, registry=registry, provenance=provenance)
    finally:
        try:
            if controls is not None:
                await asyncio.wait_for(controls.close(), timeout=10)
        finally:
            for session in reversed(created):
                await asyncio.wait_for(session.cleanup(), timeout=10)
        assert not failures, failures
        assert not provider_requests and not mount_environment.attempts
        for module_id, before in provenance.items():
            assert _module(env, module_id)[1] == before, module_id


@pytest.mark.asyncio
async def test_real_five_behavior_mount_has_shipped_balanced_resolver(mounted_defaults):
    mounted = mounted_defaults
    resolver = mounted.session.coordinator.get_capability("model_role_resolver")
    assert resolver is not None, (
        "Real hooks-routing mounted without a resolver: inspect installed-package "
        "routing resource discovery; do not inject _bundle_root in this test"
    )
    assert type(resolver).__module__ == "amplifier_module_hooks_routing.resolver_class"
    assert resolver.name == "balanced" and resolver.matrix_source == "bundle"
    shipped = mounted.env.roots["routing-matrix"] / "routing/balanced.yaml"
    assert Path(resolver.matrix_path).resolve(strict=True).read_bytes() == shipped.read_bytes()
    expected = yaml.safe_load(shipped.read_text())["roles"]
    assert expected and set(resolver._matrix_roles) == set(expected)


@pytest.mark.asyncio
async def test_mounted_defaults_export_actual_resources_and_flatten_routing(mounted_defaults):
    mounted = mounted_defaults
    from amplifier_web.bundles import BundleManager

    # Execute both real host paths before judging routing metadata. A collector
    # with no resolver must not pass merely because export returns Markdown.
    resources = mounted.controls.export_resources()
    before = copy.deepcopy(resources)
    manager = BundleManager(mounted.config.home)
    exported = manager.export_document(
        mounted.config.settings, effective_plan=mounted.session.coordinator.config,
        root_bundle=mounted.config.active_bundle, name="offline-defaults",
        resources=resources,
    )
    assert resources == before, "Export mutated the observed runtime resources"
    assert "routingMatrix" in resources, "Actual collector omitted mounted routing metadata"
    matrix = resources["routingMatrix"]
    assert matrix["name"] == "balanced" and matrix["roles"]
    assert "baseRoles" in matrix, (
        "Actual collector cannot locate shipped roles for the installed routing module"
    )
    shipped = yaml.safe_load(
        (mounted.env.roots["routing-matrix"] / "routing/balanced.yaml").read_text()
    )
    assert set(matrix["baseRoles"]) == set(shipped["roles"])
    namespaces = {row["namespace"] for row in resources["namespaces"]}
    assert {"unified", "imagegen", "computer-use", "context-intelligence",
            "routing-matrix"} <= namespaces
    assert set(mounted.prepared.bundle.context) <= {
        row["name"] for row in resources["context"]
    }, resources["warnings"]
    assert exported["includes"] == []
    document = yaml.safe_load(exported["content"].split("---", 2)[1])
    assert "includes" not in document
    routing = _support._row(document["hooks"], "hooks-routing")["config"]
    assert routing["default_matrix"] == "balanced"
    assert "custom_routing_dirs" not in routing
    expected = copy.deepcopy(matrix["roles"])
    for role in matrix["baseRoles"]:
        expected.setdefault(role, {"description": "Unavailable in exported routing policy",
                                   "candidates": []})
    assert routing["overrides"] == expected
    assert set(row["module"] for row in document["tools"]) == {
        row["module"] for row in mounted.prepared.mount_plan["tools"]
    }
    # Negative control: missing metadata must be refused, not silently flattened.
    incomplete = copy.deepcopy(resources)
    del incomplete["routingMatrix"]["baseRoles"]
    with pytest.raises(ValueError, match="shipped matrix"):
        manager.export_document(
            mounted.config.settings, effective_plan=mounted.session.coordinator.config,
            resources=incomplete,
        )
