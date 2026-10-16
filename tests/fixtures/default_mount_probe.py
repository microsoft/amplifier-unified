"""Fresh-process pytest probe; parent owns dependencies and the container.

Standalone API/source preflight (no activation or mounts):
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos \
  /opt/defaults/.venv/bin/python -I -B -m pytest -q -s --noconftest \
  --import-mode=importlib -c /dev/null -p pytest_asyncio.plugin \
  tests/fixtures/default_mount_probe.py::test_preflight

Unified/Core/Foundation stay installed. Declared upstream modules use the real
Bundle.prepare/ModuleActivator source layout, with explicit host-installed module
exceptions. Exact existing skill directories replace Git skill URIs only in this
invocation's composed Bundle, not source manifests. No agent/skill is dropped.
No provider turn, image request, desktop action, installs or network are allowed.
This tests preparation/mount/export, NOT complete host launch or export reload.
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
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import traceback
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import yaml


PREFIX = "DEFAULT_MOUNT_PROBE "
_support_path = Path(__file__).parents[1] / "test_default_behaviors_integration.py"
_spec = importlib.util.spec_from_file_location("_default_mount_composition", _support_path)
assert _spec is not None and _spec.loader is not None
_support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_support)
offline_defaults = _support.offline_defaults


def emit(value):
    print("\n" + PREFIX + json.dumps(value, sort_keys=True), flush=True)


def missing(items):
    emit({"status": "missing_prerequisites", "missing": items})
    pytest.skip("Prepared prerequisites missing: " + json.dumps(items))


def declarations(plan, owner="root"):
    """Keep declaration owners in prerequisite reports, including child plans."""
    for section in ("providers", "tools", "hooks"):
        for index, row in enumerate(plan.get(section, [])):
            yield f"{owner}.{section}[{index}]", row
    for name, row in plan.get("session", {}).items():
        if isinstance(row, dict) and "module" in row:
            yield f"{owner}.session.{name}", row
    for index, row in enumerate(plan.get("spawn", {}).get("tools", [])):
        yield f"{owner}.spawn.tools[{index}]", row
    for name, agent in plan.get("agents", {}).items():
        if isinstance(agent, dict):
            yield from declarations(agent, f"{owner}.agents.{name}")


def wheel_bytes(package, distribution, source=None):
    """Read wheel/source bytes WITHOUT importing an upstream module package."""
    dist = importlib.metadata.distribution(distribution)
    assert dist.read_text("WHEEL") and dist.read_text("RECORD"), distribution
    assert not json.loads(dist.read_text("direct_url.json") or "{}").get("dir_info", {}).get("editable")
    installed = Path(dist.locate_file(package)).resolve(strict=True)
    records = {str(item): item for item in dist.files or ()}
    files = {path.relative_to(installed).as_posix() for path in installed.rglob("*.py")}
    recorded = {name[len(package) + 1:] for name in records
                if name.startswith(package + "/") and name.endswith(".py")}
    assert files == recorded and "__init__.py" in files, distribution
    if source is not None:
        source = source.resolve(strict=True)
        assert not installed.is_relative_to(source) and not source.is_relative_to(installed)
        assert files == {path.relative_to(source).as_posix() for path in source.rglob("*.py")}
    hashes = {}
    for relative in sorted(files):
        path = (installed / relative).resolve(strict=True)
        assert path.is_relative_to(installed), relative
        record = records[package + "/" + relative]
        assert record.hash is not None and record.hash.mode == "sha256", relative
        assert Path(dist.locate_file(record)).resolve(strict=True) == path, relative
        data = path.read_bytes()
        digest = hashlib.sha256(data)
        assert record.size == len(data)
        assert base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=") == record.hash.value
        if source is not None:
            original = (source / relative).resolve(strict=True)
            assert original.is_relative_to(source)
            assert data == original.read_bytes(), f"Wheel/source mismatch: {package}/{relative}"
        hashes[relative] = digest.hexdigest()
    return {"package": package, "distribution": distribution, "version": dist.version,
            "installed": str(installed), "source": str(source) if source else None,
            "files": hashes}


def public_provenance(row):
    return {**{key: value for key, value in row.items() if key != "files"},
            "python_files": len(row["files"]),
            "python_manifest_sha256": hashlib.sha256(
                json.dumps(row["files"], sort_keys=True).encode()).hexdigest()}


def assert_origin(row, expected, *, required=False):
    package = row["package"]
    loaded = {name: module for name, module in tuple(sys.modules.items())
              if name == package or name.startswith(package + ".")}
    if required:
        assert package in loaded, f"Expected package was not imported: {package}"
    for name, module in loaded.items():
        path = Path(module.__file__).resolve(strict=True)
        assert path.is_relative_to(expected), f"Wrong origin for {name}: {path}; expected {expected}"
        relative = path.relative_to(expected).as_posix()
        assert relative in row["files"], f"Unverified import: {name}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["files"][relative]


@pytest.fixture
def guarded_environment(request, monkeypatch, tmp_path):
    assert sys.flags.isolated and not sys.flags.optimize
    assert Path("/run/systemd/container").exists() or Path("/.dockerenv").exists()
    if not os.environ.get("UNIFIED_BEHAVIOR_REPOS"):
        missing(["UNIFIED_BEHAVIOR_REPOS"])
    repositories = Path(os.environ["UNIFIED_BEHAVIOR_REPOS"]).resolve(strict=True)
    absent = [str(repositories / name) for name in _support.REPOSITORIES.values()
              if not (repositories / name).is_dir()]
    for distribution in ("amplifier-unified", "amplifier-core", "amplifier-foundation"):
        try:
            importlib.metadata.distribution(distribution)
        except importlib.metadata.PackageNotFoundError:
            absent.append("installed distribution " + distribution)
    if absent:
        missing(absent)
    attempts, git_reads = [], []
    active = True
    phase = "fixture/imports"

    def audit(event, args):
        if not active:
            return
        if event == "subprocess.Popen":
            argv = args[1]
            if (isinstance(argv, (list, tuple)) and len(argv) == 5
                    and str(args[0]) == argv[0] == "git" and argv[1] == "-C"
                    and argv[3] == "rev-parse" and argv[4] in {"HEAD", "--show-toplevel"}
                    and Path(argv[2]).resolve().is_relative_to(repositories)):
                git_reads.append(list(argv))
                return
        elif event not in {
            "socket.bind", "socket.connect", "socket.connect_ex", "socket.sendto",
            "socket.getaddrinfo", "socket.gethostbyname", "os.system",
            "os.posix_spawn", "os.exec", "os.fork", "os.forkpty",
        }:
            return
        detail = {"event": event, "phase": phase}
        if event in {"socket.bind", "socket.connect", "socket.connect_ex", "socket.sendto"}:
            detail.update(family=str(getattr(args[0], "family", None)),
                          socket_type=str(getattr(args[0], "type", None)),
                          endpoint=repr(args[-1])[:400])
        elif event == "subprocess.Popen":
            detail["argv"] = repr(args[1])[:800]
        else:
            detail["arguments"] = repr(args)[:400]
        detail["stack"] = [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_stack(limit=10)[:-1]
        ]
        if len(attempts) < 20:
            attempts.append(detail)
            emit({"status": "forbidden_effect", **detail})
        raise RuntimeError("Offline mount/export forbids " + event)

    def set_phase(value):
        nonlocal phase
        phase = value

    sys.addaudithook(audit)
    try:
        # Install the guard BEFORE the existing fixture imports product code.
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
        yield SimpleNamespace(env=env, repositories=repositories, attempts=attempts,
                              git_reads=git_reads, phase=set_phase)
    finally:
        active = False
        assert not attempts, "Forbidden effects (including caught attempts): " + json.dumps(attempts)


def offline_sources(env, monkeypatch):
    """Preserve remote registry identity; extend only exact prepared bindings."""
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver

    original = SimpleSourceResolver.resolve  # The existing fixture's whitelist.
    manifests = {key: value for key, value in env.sources.items() if key.startswith("git+")}
    bindings = {}

    async def resolve(resolver, uri):
        if uri in bindings:
            path, root = bindings[uri]
            assert path.exists() and path.resolve().is_relative_to(root.resolve()), uri
            return ResolvedSource(active_path=path, source_root=root)
        return await original(resolver, manifests.get(uri, uri))

    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    sources = {key: key for key in manifests}
    for namespace, repository in _support.REPOSITORIES.items():
        remote = f"git+https://github.com/microsoft/{repository}@main"
        sources[namespace] = remote
        for key, local in env.sources.items():
            if key.startswith(namespace + ":") and "#subdirectory=" in local:
                sources[key] = remote + "#" + local.split("#", 1)[1]
    return {"sources": {"bundles": sources}}, bindings


def remote_path(source, repositories, heads):
    """An exact Microsoft Git URI at main (the prepared pin) or its actual SHA."""
    assert isinstance(source, str) and source.startswith("git+https://"), source
    parsed = urlsplit(source[4:])
    assert parsed.netloc == "github.com" and not parsed.query, source
    repository, at, revision = parsed.path.removeprefix("/microsoft/").rpartition("@")
    assert parsed.path.startswith("/microsoft/") and at and "/" not in repository, source
    repository = repository.removesuffix(".git")
    assert re.fullmatch(r"amplifier[-a-z0-9]+", repository), source
    root = repositories / repository
    if not root.is_dir():
        raise FileNotFoundError(f"Prepared repository missing: {root} for {source}")
    root = root.resolve(strict=True)
    assert root.is_relative_to(repositories), source
    if root not in heads:
        heads[root] = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
            capture_output=True, check=True, timeout=2,
        ).stdout.strip()
        assert re.fullmatch(r"[a-f0-9]{40}", heads[root]), root
    assert revision in {"main", heads[root]}, f"Unprepared revision: {source}; HEAD={heads[root]}"
    relative = ""
    if parsed.fragment:
        assert parsed.fragment.startswith("subdirectory=") and "&" not in parsed.fragment, source
        relative = parsed.fragment[len("subdirectory="):]
    path = (root / relative).resolve()
    assert path.is_relative_to(root), source
    if not path.exists():
        raise FileNotFoundError(f"Prepared source missing: {path} for {source}")
    return path, root


def module_wheel(module_id, source_dir):
    metadata = source_dir / "pyproject.toml"
    if not metadata.is_file():
        raise FileNotFoundError(f"Prepared module metadata missing: {metadata}")
    project = tomllib.loads(metadata.read_text())["project"]
    entry = project.get("entry-points", {}).get("amplifier.modules", {}).get(module_id)
    package = entry.split(":")[0].split(".")[0] if entry else (
        "amplifier_module_" + module_id.replace("-", "_")
    )
    candidates = [source_dir / package, source_dir / "src" / package]
    candidates = [path for path in candidates if (path / "__init__.py").is_file()]
    if not candidates:
        raise FileNotFoundError(f"No prepared Python package {package} under {source_dir}")
    assert len(candidates) == 1, candidates
    return wheel_bytes(package, project["name"], candidates[0])


@pytest_asyncio.fixture
async def inputs(guarded_environment, monkeypatch):
    state = guarded_environment
    env = state.env
    state.phase("composition/preflight")
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    from amplifier_web.host.components import ComponentResolver
    from amplifier_web.host.config import configure_skill_cache
    from amplifier_web.host.session import live_plan, module_source

    signatures = {
        "Bundle.prepare": inspect.signature(Bundle.prepare),
        "PreparedBundle.create_session": inspect.signature(PreparedBundle.create_session),
    }
    assert {"strict", "install_deps", "cache_dir", "source_resolver"} <= set(
        signatures["Bundle.prepare"].parameters
    ), "Pinned Foundation lacks the required preparation API"
    assert "before_initialize" in signatures["PreparedBundle.create_session"].parameters
    assert callable(getattr(BundleModuleResolver, "get_module_source", None))
    settings, bindings = offline_sources(env, monkeypatch)
    registry, loaded, config = await env.compose(
        root_patch={"session": {"context": {"module": "context-simple",
                    "source": "git+https://github.com/microsoft/amplifier-module-context-simple@main"}}},
        settings=settings,
    )
    assert set(config.app_bundles) == set(env.defaults.DEFAULT_BEHAVIORS)
    assert loaded.providers == [{"module": "provider-test", "config": {"model": "no-call"}}]
    loaded.providers = []  # Remove only the fixture's non-existent sentinel.
    original_roots = dict(loaded.source_base_paths)
    adapted, _ = live_plan(loaded.to_mount_plan())
    loaded.session, loaded.agents = adapted["session"], adapted.get("agents", {})
    components = loaded._host_components
    components.apply(loaded)
    configure_skill_cache(loaded, config.registry_home)
    assert loaded.source_base_paths == original_roots
    plan = loaded.to_mount_plan()
    before_agents = copy.deepcopy(plan.get("agents", {}))
    rows = list(declarations(plan))
    heads, packages, expected_paths, absent, declared = {}, {}, {}, [], []
    selected_sources = {}

    for owner, row in rows:
        module_id, source = row["module"], row.get("source")
        declared.append({"owner": owner, "module": module_id, "source": source})
        if not source:
            continue  # Inherited modules are checked against the prepared set below.
        try:
            source_dir, root = remote_path(source, state.repositories, heads)
            attested = module_wheel(module_id, source_dir)
            selected = module_source(config, False, module_id, source, components)
            if module_id in components.installed:
                expected = Path(attested["installed"])
                assert Path(selected).resolve(strict=True) == expected, (module_id, selected)
                binding_root = expected
            else:
                assert selected == source, (module_id, source, selected)
                expected, binding_root = source_dir, root
            if module_id in packages:
                assert packages[module_id] == attested and expected_paths[module_id] == expected, (
                    "Conflicting sources for one module", module_id
                )
            packages[module_id], expected_paths[module_id] = attested, expected
            selected_sources[module_id] = selected
            bindings[selected] = (expected, binding_root)
        except (FileNotFoundError, importlib.metadata.PackageNotFoundError) as exc:
            absent.append({"owner": owner, "module": module_id, "source": source, "reason": str(exc)})

    for owner, row in rows:
        if not row.get("source") and row["module"] not in packages:
            absent.append({"owner": owner, "module": row["module"],
                           "reason": "No declared prepared source to inherit"})
    # Host loop imports its streaming dependency even though live_plan replaces
    # declared streaming loops. Verify that installed dependency without importing.
    try:
        streaming = module_wheel("loop-streaming", state.repositories / "amplifier-module-loop-streaming")
    except (FileNotFoundError, importlib.metadata.PackageNotFoundError) as exc:
        absent.append({"owner": "loop-live Python dependency", "module": "loop-streaming", "reason": str(exc)})
        streaming = None
    if absent:
        missing(absent)

    substitutions = []
    for owner, row in rows:
        if row["module"] != "tool-skills":
            continue
        skills = row.get("config", {}).get("skills", [])
        assert isinstance(skills, list), (owner, skills)
        for index, value in enumerate(skills):
            if isinstance(value, str) and value.startswith("git+"):
                try:
                    path, _ = remote_path(value, state.repositories, heads)
                    assert path.is_dir() and any(path.rglob("SKILL.md")), (owner, value)
                    skills[index] = str(path)
                    substitutions.append({"owner": owner, "index": index, "source": value, "path": str(path)})
                except FileNotFoundError as exc:
                    absent.append({"owner": owner, "skill": value, "reason": str(exc)})
    if absent:
        missing(absent)
    # Verify the complete agent tree changed ONLY at the recorded skill entries.
    expected_agents = copy.deepcopy(before_agents)
    for owner, row in declarations({"agents": expected_agents}):
        if row["module"] == "tool-skills":
            for item in substitutions:
                if item["owner"] == owner:
                    row["config"]["skills"][item["index"]] = item["path"]
    assert plan.get("agents", {}) == expected_agents
    loaded.tools, loaded.hooks, loaded.agents = plan.get("tools", []), plan.get("hooks", []), plan.get("agents", {})
    # Every module stays declared. Preparation sees this exact full composition.
    assert [(owner, row["module"], row.get("source")) for owner, row in declarations(loaded.to_mount_plan())] == [
        (row["owner"], row["module"], row["source"]) for row in declared
    ]
    root_rows = [row for owner, row in rows if ".agents." not in owner and ".spawn." not in owner]
    desktop = _support._row(plan["tools"], "tool-computer-use")["config"]
    assert desktop["read_only"] is True and desktop.get("target") in (None, "", "local")
    assert {"tool-skills", "tool-image", "tool-computer-use", "hook-computer-use",
            "tool-context-intelligence-transcript", "hook-context-intelligence", "hooks-routing"} <= {
                row["module"] for row in root_rows
            }
    for module_id, row in packages.items():
        # This preflight must not prime imports from the installed upstream
        # copies. Already-imported host dependencies must have their chosen origin.
        expected = Path(row["installed"] if module_id in components.installed else row["source"])
        assert_origin(row, expected)
    unified = wheel_bytes("amplifier_web", "amplifier-unified")
    foundation = wheel_bytes("amplifier_foundation", "amplifier-foundation",
                             env.roots["foundation"] / "amplifier_foundation")
    assert_origin(unified, Path(unified["installed"]), required=True)
    assert_origin(foundation, Path(foundation["installed"]), required=True)
    return SimpleNamespace(
        state=state, env=env, config=config, loaded=loaded, registry=registry,
        components=components, bindings=bindings, packages=packages, streaming=streaming,
        expected_paths=expected_paths, selected_sources=selected_sources, root_rows=root_rows,
        substitutions=substitutions, declared=declared, unified=unified, foundation=foundation,
        signatures={key: str(value) for key, value in signatures.items()},
        commits={str(root): sha for root, sha in heads.items()},
        component_resolver=ComponentResolver, module_source=module_source,
    )


@pytest.mark.asyncio
async def test_preflight(inputs):
    emit({"status": "ready", "apis": inputs.signatures,
          "declared_modules": inputs.declared, "prepared_commits": inputs.commits,
          "expected_module_paths": {key: str(path) for key, path in inputs.expected_paths.items()},
          "skill_substitutions": inputs.substitutions,
          "upstream": {key: public_provenance(row) for key, row in inputs.packages.items()},
          "limits": ["No activation or mounts in preflight", "No model turn or desktop actions"]})


def observe_mount(monkeypatch, module, module_id, target, mounted, validation):
    original = module.mount
    assert inspect.iscoroutinefunction(original), module_id

    @wraps(original)
    async def observed(coordinator, config=None):
        # Core validators genuinely call mount on MockCoordinator. Do not change
        # those calls, count them as production mounts or weaken their guards.
        if not target or coordinator is not target[0]:
            validation[module_id] += 1
            return await original(coordinator, config)
        before_tools = dict(coordinator.get("tools") or {})
        before_hooks = {(event, name) for event, names in coordinator.hooks.list_handlers().items()
                        for name in names}
        received = copy.deepcopy(config or {})
        result = await original(coordinator, config)
        mounted.append({
            "module": module_id, "config": received,
            "tools": {name: tool for name, tool in (coordinator.get("tools") or {}).items()
                      if before_tools.get(name) is not tool},
            "hooks": {(event, name) for event, names in coordinator.hooks.list_handlers().items()
                      for name in names} - before_hooks,
        })
        return result

    monkeypatch.setattr(module, "mount", observed)


@pytest_asyncio.fixture
async def mounted(inputs, monkeypatch):
    data = inputs
    from amplifier_core import HookResult
    from amplifier_core.events import MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED
    from amplifier_web.host.model_selection import prepare_default_routing
    from amplifier_web.runtime_controls import RuntimeControls

    data.state.phase("real preparation; installs disabled")
    prepared = await asyncio.wait_for(data.loaded.prepare(
        strict=True, install_deps=False, cache_dir=data.config.registry_home / "cache",
        source_resolver=lambda module, source: data.module_source(
            data.config, False, module, source, data.components),
    ), timeout=30)
    prepared.resolver = data.component_resolver(prepared.resolver, data.components)
    assert prepared.bundle is data.loaded
    assert prepared.mount_plan.get("providers", []) == []
    assert prepared.mount_plan.get("agents", {}) == data.loaded.agents
    for module_id, expected in data.expected_paths.items():
        actual = prepared.resolver.get_module_source(module_id)
        assert actual is not None, f"Real prepare omitted declared module {module_id}"
        assert Path(actual).resolve(strict=True) == expected, (module_id, actual, expected)
        # Exercise the same wrapped resolver Core will call, not only its index.
        actual_source = await prepared.resolver.async_resolve(
            module_id, data.selected_sources[module_id])
        assert Path(actual_source.resolve()).resolve(strict=True) == expected, module_id

    target, created, mounts, failures, provider_requests, ci_resolvers = [], [], [], [], [], []
    validation = Counter()
    required = Counter(row["module"] for row in data.root_rows)
    for module_id in required:
        row = data.packages[module_id]
        module = importlib.import_module(row["package"])
        expected = Path(row["installed"] if module_id in data.components.installed else row["source"])
        assert_origin(row, expected, required=True)
        observe_mount(monkeypatch, module, module_id, target, mounts, validation)

    ci_api = importlib.import_module("amplifier_module_hook_context_intelligence.config_resolver")
    original_ci_init = ci_api.HookConfigResolver.__init__

    @wraps(original_ci_init)
    def observe_ci(instance, *args, **kwargs):
        original_ci_init(instance, *args, **kwargs)
        if target and any(value is target[0] for value in (*args, *kwargs.values())):
            ci_resolvers.append(instance)

    monkeypatch.setattr(ci_api.HookConfigResolver, "__init__", observe_ci)
    controls = None
    report = {}

    async def before_initialize(session):
        created.append(session)  # Cleanup remains owned if initialization raises.
        target.append(session.coordinator)
        assert not session.initialized
        await prepare_default_routing(session)

        async def failure(event, value):
            failures.append({"event": event, "data": copy.deepcopy(value)})
            return HookResult()

        async def no_provider_request(event, value):
            provider_requests.append(event)
            pytest.fail("The mount/export probe must not request a provider")

        for event in (MODULE_LOAD_FAILED, MODULE_ON_SESSION_READY_FAILED):
            session.coordinator.hooks.register(event, failure, priority=-1000,
                                               name="mount-probe-" + event)
        session.coordinator.hooks.register("provider:request", no_provider_request,
                                           priority=-1000, name="mount-probe-no-provider")

    try:
        data.state.phase("Core validation and real session mount")
        session = await asyncio.wait_for(prepared.create_session(
            session_cwd=data.env.workspace, before_initialize=before_initialize,
        ), timeout=30)
        coordinator = session.coordinator
        assert not failures, failures
        assert Counter(row["module"] for row in mounts) == required
        assert coordinator.get("orchestrator") is not None and coordinator.get("context") is not None
        assert not coordinator.get("providers") and not provider_requests
        tools = coordinator.get("tools") or {}
        for row in mounts:
            if row["module"].startswith("tool-"):
                assert row["tools"], f"No real tool mounted by {row['module']}"
                for name, tool in row["tools"].items():
                    assert tools[name] is tool
                    assert type(tool).__module__.split(".")[0] == data.packages[row["module"]]["package"]
        routing = next(row for row in mounts if row["module"] == "hooks-routing")
        assert {("session:start", "routing-resolve"),
                ("session:resume", "routing-resolve-resume"),
                ("provider:request", "routing-context")} <= routing["hooks"]
        desktop = next(row for row in mounts if row["module"] == "tool-computer-use")
        assert desktop["config"]["read_only"] is True
        if "computer_use_unavailable" in tools:
            assert tools["computer_use_unavailable"].description.strip()
            assert not {"computer", "desktop"} & tools.keys()
        else:
            assert {"computer", "desktop"} <= tools.keys()
        assert ci_resolvers, "Real CI mount's constructor was not observed with the session coordinator"
        assert os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL"] == "https://ambient.invalid"
        for resolver in ci_resolvers:
            assert resolver.destinations == {} and resolver.validate_destinations() == {}
        coordinator.register_capability("web.prepared", prepared)
        coordinator.register_capability("web.registry", data.registry)
        from amplifier_module_loop_live.runtime import Runtime
        controls = RuntimeControls(session, Runtime(session.session_id))
        data.state.phase("acceptance/resource collection/export")
        yield SimpleNamespace(inputs=data, session=session, prepared=prepared,
                              controls=controls, report=report)
    finally:
        data.state.phase("cleanup and provenance readback")
        try:
            if controls is not None:
                await asyncio.wait_for(controls.close(), timeout=10)
        finally:
            for session in reversed(created):
                await asyncio.wait_for(session.cleanup(), timeout=10)
        assert not failures and not provider_requests, (failures, provider_requests)
        assert not data.state.attempts, data.state.attempts
        for module_id, row in data.packages.items():
            assert wheel_bytes(row["package"], row["distribution"], Path(row["source"])) == row
            expected = Path(row["installed"] if module_id in data.components.installed else row["source"])
            assert_origin(row, expected, required=module_id in required)
        assert wheel_bytes(data.streaming["package"], data.streaming["distribution"],
                           Path(data.streaming["source"])) == data.streaming
        assert_origin(data.streaming, Path(data.streaming["installed"]), required=True)
        for row in (data.unified, data.foundation):
            assert wheel_bytes(row["package"], row["distribution"],
                               Path(row["source"]) if row["source"] else None) == row
            assert_origin(row, Path(row["installed"]), required=True)
        if report:
            emit({"status": "passed", **report, "forbidden_effects": [],
                  "real_mounts": dict(required), "validator_mounts": dict(validation),
                  "prepared_module_paths": {key: str(path) for key, path in data.expected_paths.items()},
                  "loaded_package_origins": {
                      row["package"]: str(Path(sys.modules[row["package"]].__file__).resolve())
                      for row in data.packages.values() if row["package"] in sys.modules},
                  "skill_substitutions": data.substitutions,
                  "agents_retained": sorted(prepared.mount_plan.get("agents", {})),
                  "git_reads": data.state.git_reads})


def check_routing(mounted):
    resolver = mounted.session.coordinator.get_capability("model_role_resolver")
    assert resolver is not None, "Real declared-source routing mounted without a resolver"
    assert type(resolver).__module__ == "amplifier_module_hooks_routing.resolver_class"
    assert resolver.name == "balanced" and resolver.matrix_source == "bundle"
    shipped = mounted.inputs.env.roots["routing-matrix"] / "routing/balanced.yaml"
    assert Path(resolver.matrix_path).resolve(strict=True) == shipped.resolve(strict=True)
    expected = yaml.safe_load(shipped.read_text())["roles"]
    assert expected and set(resolver._matrix_roles) == set(expected)
    return expected


@pytest.mark.asyncio
async def test_mount(mounted):
    expected = check_routing(mounted)
    mounted.report.update(case="mount", routing="balanced", base_roles=sorted(expected))


@pytest.mark.asyncio
async def test_export(mounted):
    from amplifier_web.bundles import BundleManager

    expected = check_routing(mounted)
    resources = mounted.controls.export_resources()
    before = copy.deepcopy(resources)
    manager = BundleManager(mounted.inputs.config.home)
    exported = manager.export_document(
        mounted.inputs.config.settings, effective_plan=mounted.session.coordinator.config,
        root_bundle=mounted.inputs.config.active_bundle, name="offline-defaults",
        resources=resources,
    )
    assert resources == before
    assert resources["routingMatrix"]["name"] == "balanced"
    matrix = resources["routingMatrix"]
    assert matrix["roles"] and set(matrix["baseRoles"]) == set(expected)
    namespaces = {row["namespace"] for row in resources["namespaces"]}
    assert {"unified", "imagegen", "computer-use", "context-intelligence", "routing-matrix"} <= namespaces
    assert set(mounted.prepared.bundle.context) <= {row["name"] for row in resources["context"]}, resources["warnings"]
    document = yaml.safe_load(exported["content"].split("---", 2)[1])
    assert exported["includes"] == [] and "includes" not in document
    routing = _support._row(document["hooks"], "hooks-routing")["config"]
    assert routing["default_matrix"] == "balanced" and "custom_routing_dirs" not in routing
    flattened = copy.deepcopy(matrix["roles"])
    for role in matrix["baseRoles"]:
        flattened.setdefault(role, {"description": "Unavailable in exported routing policy", "candidates": []})
    assert routing["overrides"] == flattened
    assert {row["module"] for row in document["tools"]} == {
        row["module"] for row in mounted.prepared.mount_plan["tools"]
    }
    assert set(document.get("agents", {})) == set(mounted.prepared.mount_plan.get("agents", {}))
    incomplete = copy.deepcopy(resources)
    del incomplete["routingMatrix"]["baseRoles"]
    with pytest.raises(ValueError, match="shipped matrix"):
        manager.export_document(mounted.inputs.config.settings,
                                effective_plan=mounted.session.coordinator.config,
                                resources=incomplete)
    mounted.report.update(case="export", routing="balanced", base_roles=sorted(expected),
                          export_sha256=hashlib.sha256(exported["content"].encode()).hexdigest())
