"""Opt-in runtime safety checks against the installed Unified candidate.

Prepare pinned repositories at /opt/defaults/repos, install the candidate wheel
and upstream runtime packages, then set UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos.
Run outside the checkout with pytest --import-mode=importlib. Missing prerequisites
skip explicitly; checkout/editable imports, source mismatches and safety failures
FAIL. No downloads, provider calls, module mounting or real desktop access.

The unchanged composition fixture supplies all canonical default manifests. The
real upstream resolver and ComputerTool packages must match the prepared sources
byte-for-byte. Qualification setup owns verification of these repository pins:
  context-intelligence: a80ce69d0e96e9a343501f238dcd867350dd9aa2
  computer-use:         08faa0cf17876be47d7a272e702769c4702dcba5
These tests do not independently attest Git revisions or test desktop disclosure,
platform backends, transport delivery, or nested Core session persistence.
"""
from __future__ import annotations

import copy
import importlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest


if not os.environ.get("UNIFIED_BEHAVIOR_REPOS"):
    pytest.skip(
        "Opt in inside an owned container: UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos",
        allow_module_level=True,
    )
pytest.importorskip("yaml", reason="Prepared runtime checks require PyYAML")

# Import only the fixture/helpers, not its tests. Loading by file keeps this
# usable with --import-mode=importlib without adding the checkout to sys.path.
_support_path = Path(__file__).with_name("test_default_behaviors_integration.py")
if not _support_path.is_file():
    pytest.skip("Copy the composition test beside this runtime test", allow_module_level=True)
_support_spec = importlib.util.spec_from_file_location(
    "_unified_default_composition_support", _support_path
)
assert _support_spec is not None and _support_spec.loader is not None
_support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
offline_defaults = _support.offline_defaults


def _require_distribution(name):
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"Prepared runtime prerequisite missing: installed {name}")


@pytest.fixture
def runtime_defaults(request, monkeypatch):
    assert Path("/.dockerenv").exists() or Path("/run/systemd/container").exists(), (
        "Runtime safety checks must run in the owned container, never on the host"
    )
    directory = Path(os.environ["UNIFIED_BEHAVIOR_REPOS"])
    missing = [name for name in _support.REPOSITORIES.values()
               if not (directory / name).is_dir()]
    if missing:
        pytest.skip("Prepared repository prerequisite missing: " + ", ".join(missing))
    for name in ("amplifier-unified", "amplifier-foundation", "amplifier-core"):
        _require_distribution(name)

    # This fixture verifies installed wheel origins before importing amplifier_web
    # and amplifier_foundation; it also isolates HOME/capture state and prohibits
    # HTTP/DNS or unlisted source resolution during real manifest composition.
    env = request.getfixturevalue("offline_defaults")

    def forbidden(*args, **kwargs):
        pytest.fail("Runtime safety checks forbid sockets and subprocess effects")

    # Include Unix sockets: even an accidental X11 connection must fail. The
    # asyncio socketpair wakeup is unaffected (it does not call connect).
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    for variable in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_AUTH_SOCK"):
        monkeypatch.delenv(variable, raising=False)
    return env


def _upstream(env, namespace, module_name, package, distribution):
    """Use installed upstream code, rejecting a different or edited source tree."""
    source = env.roots[namespace] / "modules" / module_name / package
    if not (source / "__init__.py").is_file():
        pytest.skip(f"Prepared upstream source missing: {module_name}/{package}")
    _require_distribution(distribution)
    installed = _support._installed(package, distribution)
    installed_root = Path(installed.__file__).parent
    expected = {path.relative_to(source) for path in source.rglob("*.py")}
    actual = {path.relative_to(installed_root) for path in installed_root.rglob("*.py")}
    assert actual == expected, f"Installed {package} does not match prepared source files"
    for relative in sorted(expected):
        assert (installed_root / relative).read_bytes() == (source / relative).read_bytes(), (
            f"Installed {package}/{relative} differs from the prepared upstream source"
        )
    return installed


@pytest.fixture
def ci_runtime(runtime_defaults):
    _require_distribution("amplifier-bundle-context-intelligence")
    _require_distribution("pathspec")
    _support._installed("context_intelligence", "amplifier-bundle-context-intelligence")
    package = "amplifier_module_hook_context_intelligence"
    _upstream(runtime_defaults, "context-intelligence", "hook-context-intelligence",
              package, "amplifier-module-hook-context-intelligence")
    return SimpleNamespace(
        resolver=importlib.import_module(package + ".config_resolver").HookConfigResolver,
        fanout=importlib.import_module(package + ".fanout"),
    )


def _resolver(runtime, env, bundle):
    config = _support._row(bundle.hooks, "hook-context-intelligence")["config"]
    coordinator = SimpleNamespace(
        config=bundle.to_mount_plan(),
        get_capability=lambda name: str(env.workspace) if name == "session.working_dir" else None,
    )
    return runtime.resolver(copy.deepcopy(config), coordinator)


@pytest.mark.asyncio
async def test_runtime_ci_default_resolves_no_targets_with_ambient_credentials(
    runtime_defaults, ci_runtime,
):
    env = runtime_defaults
    assert os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL"] == "https://ambient.invalid"
    assert os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY"] == "synthetic-ambient-key"
    _, bundle, _ = await env.compose()
    resolver = _resolver(ci_runtime, env, bundle)
    assert resolver.destinations == {}
    assert resolver.validate_destinations() == {}
    match_key = ci_runtime.fanout.normalize_match_key(str(env.workspace))
    assert ci_runtime.fanout.select_active(resolver.validate_destinations(), match_key) == {}

    # Local capture identity must remain usable even with no forwarding targets.
    from amplifier_web.session_files import capture_dir
    assert resolver.session_dir("runtime-probe") == capture_dir(env.workspace, "runtime-probe")


@pytest.mark.asyncio
async def test_runtime_ci_explicit_target_survives_composition_validation_and_selection(
    runtime_defaults, ci_runtime,
):
    env = runtime_defaults
    chosen = {
        "url": "https://explicit.invalid",
        "api_key": "synthetic-explicit-key",
        "include": ["**"],
        "exclude": ["**/private/**"],
    }
    _, bundle, _ = await env.compose(settings={
        "overrides": {"hook-context-intelligence": {
            "config": {"destinations": {"chosen": copy.deepcopy(chosen)}}},
        },
    })
    resolver = _resolver(ci_runtime, env, bundle)
    assert set(resolver.destinations) == {"chosen"}
    valid = resolver.validate_destinations()
    assert set(valid) == {"chosen"}, "A silently dropped explicit target is not preservation"
    target = valid["chosen"]
    assert (target.name, target.url, target.api_key, target.include, target.exclude) == (
        "chosen", chosen["url"], chosen["api_key"], ("**",), ("**/private/**",),
    )
    match_key = ci_runtime.fanout.normalize_match_key(str(env.workspace))
    assert ci_runtime.fanout.select_active(valid, match_key) == {"chosen": target}
    excluded = ci_runtime.fanout.normalize_match_key(str(env.workspace / "private" / "project"))
    assert ci_runtime.fanout.select_active(valid, excluded) == {}


@pytest.mark.asyncio
async def test_runtime_computer_read_only_denies_writes_before_backend_effects(
    runtime_defaults, monkeypatch,
):
    env = runtime_defaults
    _require_distribution("Pillow")
    module = _upstream(env, "computer-use", "tool-computer-use",
                       "amplifier_module_tool_computer_use", "amplifier-module-tool-computer-use")
    backend_api = importlib.import_module(module.__name__ + ".backend")

    class FakeBackend:
        name = "runtime-safety-fake"
        is_remote = False

        def __init__(self):
            self.calls = []

        def list_monitors(self):
            self.calls.append(("list_monitors",))
            return [backend_api.MonitorInfo("fake", 0, 0, 800, 600, primary=True)]

        def cursor_position(self):
            self.calls.append(("cursor_position",))
            return 20, 30

        def type_text(self, text, guard=None):
            self.calls.append(("type_text", text))

        def __getattr__(self, name):
            # A missing backend method is a test failure, never a real-platform
            # fallback or a normal ToolResult failure that could mask a leak.
            pytest.fail(f"Unexpected fake-backend access: {name}")

    # Disclosure is explicitly outside this test: replace only the external
    # announcement builder. Constructor, execute, policy and dispatch stay real.
    monkeypatch.setattr(module, "_build_announcement", lambda *args, **kwargs: None)
    _, bundle, _ = await env.compose()
    config = _support._row(bundle.tools, "tool-computer-use")["config"]
    assert config["read_only"] is True
    original = copy.deepcopy(config)
    backend = FakeBackend()
    tool = module.ComputerTool(backend, config=copy.deepcopy(config))
    tool.resolve_display()  # Real geometry resolution, but exclusively fake data.
    backend.calls.clear()

    # Positive read control: an unconditional refusal must not pass this test.
    read = await tool.execute({"action": "cursor_position"})
    assert read.success is True, read.error
    assert backend.calls == [("cursor_position",)]
    backend.calls.clear()
    writes = [
        {"action": action, "coordinate": [20, 30]}
        for action in ("mouse_move", "left_click", "right_click", "middle_click",
                       "double_click", "triple_click", "left_mouse_down", "left_mouse_up")
    ] + [
        {"action": "left_click_drag", "start_coordinate": [10, 10], "coordinate": [20, 30]},
        {"action": "scroll", "scroll_direction": "down", "scroll_amount": 1},
        {"action": "key", "text": "Return", "repeat": 2},
        {"action": "hold_key", "text": "Shift", "duration": 0.01},
        {"action": "type", "text": "synthetic input"},
        {"action": "focus_window", "handle": "fake-window"},
    ]
    for payload in writes:
        result = await tool.execute(payload)
        assert result.success is False, payload
        assert result.error["message"] == (
            f"action {payload['action']!r} blocked: computer-use is mounted read_only"
        ), (payload, result.error)
        assert backend.calls == [], (payload, backend.calls)

    # Discriminating write control: change only read_only on a second instance.
    # This proves type_text reaches the fake backend when policy allows it.
    writable = module.ComputerTool(backend, config={**copy.deepcopy(config), "read_only": False})
    writable.resolve_display()
    backend.calls.clear()
    allowed = await writable.execute({"action": "type", "text": "synthetic input"})
    assert allowed.success is True, allowed.error
    assert backend.calls == [("type_text", "synthetic input")]
    assert config == original
