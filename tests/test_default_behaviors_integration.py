"""Offline, installed-wheel composition of the real default behavior manifests.

Opt in with UNIFIED_BEHAVIOR_REPOS pointing to the DTU's prepared repositories.
Run outside the source checkout (for example, with pytest --import-mode=importlib).
An unset opt-in skips; an incomplete opted-in environment FAILS, never downloads.

Only the selected root is synthetic. Upstream behavior, agent and context files
are read unchanged. This suite checks composition, not module mounting, CI's
runtime destination resolver, desktop construction/Xvfb, or routing execution.
No provider calls, installs, subprocesses, or desktop operations are performed.
"""
from __future__ import annotations

import copy
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
import yaml


# Namespace -> exact prepared repository directory. No lookup in host caches.
REPOSITORIES = {
    "foundation": "amplifier-foundation",
    "context-intelligence": "amplifier-bundle-context-intelligence",
    "computer-use": "amplifier-bundle-computer-use",
    "routing-matrix": "amplifier-bundle-routing-matrix",
    "skills": "amplifier-bundle-skills",
    "modes": "amplifier-bundle-modes",
    "imagegen": "amplifier-bundle-imagegen",
    "hooks-approval": "amplifier-module-hooks-approval",
}


def _installed(package, distribution):
    """Reject checkout/editable imports before importing the product package."""
    dist = importlib.metadata.distribution(distribution)
    relative = package.replace(".", "/") + "/__init__.py"
    assert relative in {str(path) for path in dist.files or ()}, (
        f"{distribution} must be installed from a wheel with RECORD entries"
    )
    expected = Path(dist.locate_file(relative)).resolve(strict=True)
    spec = importlib.util.find_spec(package)
    assert spec is not None and spec.origin, f"Missing installed package {package}"
    assert Path(spec.origin).resolve() == expected, (
        f"{package} resolves outside its installed distribution: {spec.origin}"
    )
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable"), distribution
    module = importlib.import_module(package)
    assert Path(module.__file__).resolve() == expected
    return module


def _row(rows, module):
    matches = [row for row in rows if row["module"] == module]
    assert len(matches) == 1, (module, matches)
    return matches[0]


@pytest.fixture
def offline_defaults(tmp_path, monkeypatch):
    location = os.environ.get("UNIFIED_BEHAVIOR_REPOS")
    if not location:
        pytest.skip("Requires prepared DTU repos via UNIFIED_BEHAVIOR_REPOS")
    directory = Path(location).resolve(strict=True)
    roots = {
        namespace: (directory / name).resolve(strict=True)
        for namespace, name in REPOSITORIES.items()
    }
    assert all(root.is_dir() for root in roots.values())
    assert Path("/run/systemd/container").exists() or Path("/.dockerenv").exists(), (
        "Run this opt-in suite only in the owned DTU, not on the host"
    )

    def no_network(*args, **kwargs):
        pytest.fail("Network access is forbidden in default-behavior composition")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock, *args, **kwargs):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            no_network()
        return original_connect(sock, *args, **kwargs)

    def connect_ex(sock, *args, **kwargs):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            no_network()
        return original_connect_ex(sock, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    for variable, relative in {
        "HOME": "home",
        "AMPLIFIER_HOME": "shared",
        "AMPLIFIER_WEB_HOME": "app",
        "AMPLIFIER_SESSION_STATE_HOME": "session-state",
    }.items():
        path = tmp_path / relative
        path.mkdir(exist_ok=True)
        monkeypatch.setenv(variable, str(path))
    # Deliberately present: these must NOT activate CI forwarding.
    monkeypatch.setenv(
        "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "https://ambient.invalid"
    )
    monkeypatch.setenv(
        "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", "synthetic-ambient-key"
    )
    monkeypatch.setenv(
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH", str(tmp_path / "ambient-captures")
    )

    web = _installed("amplifier_web", "amplifier-unified")
    _installed("amplifier_foundation", "amplifier-foundation")
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import SimpleSourceResolver
    from amplifier_web import builtin_behaviors
    from amplifier_web.host.config import HostConfig, merge
    from amplifier_web.host.session import load_root_bundle

    packaged = Path(web.__file__).parent / "bundle_data"
    assert packaged.is_dir(), "Wheel must contain the real Unified resources"
    assert builtin_behaviors.resource_root().resolve() == packaged.resolve()
    roots["unified"] = packaged.resolve()

    # Exact URI -> file mappings, generated only from prepared manifests. A
    # different ref/host/repository cannot fall back to a clone or HTTP handler.
    # Keep the repository boundary in each URI, not just the behavior filename:
    # Foundation needs the containing bundle.md/yaml as its namespace anchor.
    sources = {}
    admitted = {}
    for namespace, root in roots.items():
        sources[namespace] = str(root)
        manifests = {
            path for path in root.glob("bundle.*")
            if path.suffix in {".md", ".yaml", ".yml"} and path.is_file()
        }
        if namespace != "hooks-approval":
            assert manifests, f"Missing real namespace anchor in {root}"
        manifests.update(
            path for path in (root / "behaviors").rglob("*")
            if path.suffix in {".md", ".yaml", ".yml"} and path.is_file()
        )
        repository = REPOSITORIES.get(namespace, "amplifier-unified")
        origins = [
            f"git+https://github.com/microsoft/{repository}{suffix}"
            for suffix in ("@main", ".git@main", "", ".git")
        ]
        admitted[str(root)] = admitted[root.as_uri()] = (root, root)
        for origin in origins:
            sources[origin] = root.as_uri()
        for path in sorted(manifests):
            path = path.resolve(strict=True)
            assert path.is_relative_to(root), f"Manifest escapes its repository: {path}"
            relative = path.relative_to(root).as_posix()
            local = root.as_uri() + "#subdirectory=" + relative
            admitted[local] = admitted[str(path)] = admitted[path.as_uri()] = (path, root)
            sources[f"{namespace}:{relative}"] = local
            sources[f"{namespace}:{str(Path(relative).with_suffix(''))}"] = local
            for origin in origins:
                sources[origin + "#subdirectory=" + relative] = local

    calls = []

    async def resolve(self, uri):
        if uri not in admitted:
            pytest.fail(f"Unmapped source (network/cache fallback forbidden): {uri}")
        active, root = admitted[uri]
        assert active.exists(), active
        calls.append(active)
        return ResolvedSource(active_path=active, source_root=root)

    monkeypatch.setattr(SimpleSourceResolver, "resolve", resolve)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sequence = 0

    async def compose(*, root_patch=None, settings=None):
        nonlocal sequence
        sequence += 1
        manifest = workspace / f"selected-{sequence}.yaml"
        root = merge({
            "bundle": {"name": "selected-root", "version": "1.2.3"},
            "session": {"orchestrator": {"module": "loop-streaming"}},
            "providers": [{"module": "provider-test", "config": {"model": "no-call"}}],
        }, root_patch or {})
        manifest.write_text(yaml.safe_dump(root), encoding="utf-8")
        admitted[str(manifest)] = admitted[manifest.as_uri()] = (manifest, workspace)
        values = merge({
            "bundle": {"active": "selected-root"},
            "sources": {"bundles": {**sources, "selected-root": manifest.as_uri()}},
        }, settings or {})
        config = HostConfig(
            tmp_path / "app", workspace, values,
            tmp_path / f"registry-{sequence}", tmp_path / "shared",
        )
        before = copy.deepcopy(values)
        registry, bundle, _ = await load_root_bundle(config, config.active_bundle)
        bundle.resolve_pending_context()
        assert config.settings == before, "Composition must not rewrite user settings"
        assert bundle.name == "selected-root"
        assert bundle.version == root["bundle"]["version"]
        assert bundle.providers == root["providers"]
        assert bundle.session == root["session"]
        return registry, bundle, config

    return SimpleNamespace(
        compose=compose, roots=roots, sources=sources, calls=calls,
        defaults=builtin_behaviors, workspace=workspace,
    )


@pytest.mark.asyncio
async def test_real_five_behaviors_keep_modules_agents_context_and_namespace_anchors(
    offline_defaults,
):
    env = offline_defaults
    registry, bundle, config = await env.compose()
    defaults = env.defaults
    expected = {
        defaults.SHELL_BEHAVIOR_URI,
        defaults.IMAGEGEN_BEHAVIOR_URI,
        defaults.COMPUTER_USE_BEHAVIOR_URI,
        defaults.CONTEXT_INTELLIGENCE_BEHAVIOR_URI,
        defaults.ROUTING_BEHAVIOR_URI,
    }
    assert set(config.app_bundles) == expected
    tools = {row["module"] for row in bundle.tools}
    hooks = {row["module"] for row in bundle.hooks}
    assert {"tool-skills", "tool-image", "tool-computer-use",
            "tool-context-intelligence-transcript"} <= tools
    assert {"hook-computer-use", "hook-context-intelligence", "hooks-routing"} <= hooks
    assert {"computer-use:computer-operator",
            "context-intelligence:session-navigator"} <= bundle.agents.keys()

    from amplifier_web.host.session import load_configured_bundle
    contexts = {Path(path).resolve() for path in bundle.context.values()}
    assert contexts
    assert all(path.is_file() and path.read_text(encoding="utf-8").strip() for path in contexts)
    # Compare with EVERY declaration in the real upstream behaviors, not a
    # handcrafted approximation of CI's full payload or just one sentinel tool.
    for uri in expected:
        behavior, _ = await load_configured_bundle(
            registry, config, defaults.resolve_builtin_behavior(uri)
        )
        behavior.resolve_pending_context()
        assert {row["module"] for row in behavior.tools} <= tools, uri
        assert {row["module"] for row in behavior.hooks} <= hooks, uri
        assert behavior.agents.keys() <= bundle.agents.keys(), uri
        assert {Path(path).resolve() for path in behavior.context.values()} <= contexts, uri

    for namespace in ("unified", "imagegen", "computer-use",
                      "context-intelligence", "routing-matrix"):
        assert Path(bundle.source_base_paths[namespace]).resolve() == env.roots[namespace]
        assert any(path.is_relative_to(env.roots[namespace]) for path in contexts), namespace
    skills = _row(bundle.tools, "tool-skills")["config"]["skills"]
    assert {"@unified:skills", "@imagegen:skills", "@routing-matrix:skills"} <= set(skills)
    assert (env.roots["unified"] / "skills/amplifier-shell/SKILL.md").is_file()

    # Exercise the guard itself: a changed ref must not silently use main.
    from amplifier_foundation.sources import SimpleSourceResolver
    with pytest.raises(pytest.fail.Exception, match="Unmapped source"):
        await SimpleSourceResolver().resolve(
            defaults.COMPUTER_USE_BEHAVIOR_URI.replace("@main", "@unmapped-ref")
        )


@pytest.mark.asyncio
async def test_real_defaults_are_local_only_and_desktop_is_read_only(offline_defaults):
    env = offline_defaults
    _, bundle, _ = await env.compose()
    ci = _row(bundle.hooks, "hook-context-intelligence")["config"]
    assert ci["destinations"] == {}
    assert ci["context_intelligence_server_url"] == ""
    assert ci["context_intelligence_api_key"] == ""
    from amplifier_web.session_files import capture_dir, project_slug
    assert ci["base_path"] == str(capture_dir(env.workspace, "root").parents[3])
    assert ci["project_slug"] == project_slug(env.workspace)
    assert _row(bundle.tools, "tool-computer-use")["config"]["read_only"] is True
    # Preserve the real image behavior's declared backend/paid-call policy.
    # Composition does not instantiate that backend or make an image request.
    image = _row(bundle.tools, "tool-image")["config"]
    assert image["backend"] == "images"
    assert image["allow_paid"] is True


@pytest.mark.asyncio
async def test_real_defaults_preserve_explicit_ci_desktop_and_routing_policy(
    offline_defaults,
):
    env = offline_defaults
    destinations = {
        "chosen": {"url": "https://explicit.invalid", "include": ["**"]},
    }
    ci = {
        "destinations": destinations,
        "project_slug": "explicit-project",
        "base_path": str(env.workspace / "explicit-captures"),
    }
    desktop = {"read_only": False, "gate_writes": True}
    root_patch = {
        "hooks": [
            {"module": "hook-context-intelligence", "config": ci},
            {"module": "hooks-routing", "config": {"default_matrix": "quality"}},
        ],
        "tools": [{"module": "tool-computer-use", "config": desktop}],
    }
    original = copy.deepcopy(root_patch)
    _, root_selected, _ = await env.compose(root_patch=root_patch)
    assert _row(root_selected.hooks, "hook-context-intelligence")["config"] == ci
    assert _row(root_selected.tools, "tool-computer-use")["config"] == desktop
    assert _row(root_selected.hooks, "hooks-routing")["config"]["default_matrix"] == "quality"
    assert root_patch == original

    # A separate load proves explicit scoped overrides also win over defaults,
    # including settings taking precedence over an explicit root matrix.
    settings = {
        "overrides": {
            "hook-context-intelligence": {"config": ci},
            "tool-computer-use": {"config": desktop},
        },
        "routing": {"matrix": "economy"},
    }
    _, scoped, _ = await env.compose(
        root_patch={"hooks": [root_patch["hooks"][1]]}, settings=settings
    )
    actual_ci = _row(scoped.hooks, "hook-context-intelligence")["config"]
    assert all(actual_ci[key] == value for key, value in ci.items())
    actual_desktop = _row(scoped.tools, "tool-computer-use")["config"]
    assert all(actual_desktop[key] == value for key, value in desktop.items())
    assert _row(scoped.hooks, "hooks-routing")["config"]["default_matrix"] == "economy"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True], ids=["opt-out", "snapshot"])
async def test_real_loader_does_not_restore_disabled_defaults_or_expand_snapshots(
    offline_defaults, snapshot,
):
    env = offline_defaults
    from amplifier_web.bundles import SNAPSHOT_VERSION
    # Snapshot deliberately leaves defaults enabled in settings. A complete
    # saved plan must bypass them without requiring opt-outs.
    settings = {"routing": {"matrix": "quality"}}
    root_patch = None
    if snapshot:
        root_patch = {"bundle": {"version": SNAPSHOT_VERSION}}
    else:
        settings.update({
            "bundle": {"app": []},
            "web_bundles": {"excluded": list(env.defaults.DEFAULT_BEHAVIORS)},
        })
    env.calls.clear()
    _, bundle, _ = await env.compose(root_patch=root_patch, settings=settings)
    assert not bundle.tools
    assert not bundle.agents
    assert not bundle.context
    assert all(path.is_relative_to(env.workspace) for path in env.calls), env.calls
    if snapshot:
        assert not bundle.hooks
    else:
        # Opting out of full CI retains Unified's existing local capture hook,
        # not the analysis/navigation tools, agents or routing behavior.
        assert [row["module"] for row in bundle.hooks] == ["hook-context-intelligence"]
        assert bundle.hooks[0]["config"]["destinations"] == {}
