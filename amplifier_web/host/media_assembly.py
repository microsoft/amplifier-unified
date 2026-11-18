"""Finite, source-pinned cooperative ROOT composition, not plugin attestation.

The shipped manifest is an integration input, never learned from a running
graph. Pending/unreviewed inputs leave ordinary chat unchanged. Foundation owns
the actual construction receipt; these pins select the code allowed to supply
it. This does not isolate malicious Python or qualify native/child routes.
"""
from __future__ import annotations

import copy
from functools import partial
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
from types import CodeType, FunctionType, MethodType

RECIPE = "unified-root-read-file-v1"
MEDIA_MODEL = "gpt-6.1-sol"
_HOOKS = {"hook-context-intelligence"}
# One ordinary Work root, predicted from its shipped manifests and all five
# Unified defaults. These lists are source policy, not discovered module IDs.
WORK_TOOLS = (
    "tool-transcript", "tool-skills", "tool-bash", "tool-exec", "tool-web",
    "tool-ui-use", "tool-filesystem", "tool-apply-patch", "tool-search",
    "tool-delegate", "tool-image", "tool-computer-use",
    "tool-context-intelligence-transcript", "tool-mode",
    "tool-context-intelligence-set-filters",
)
WORK_HOOKS = (
    "hook-computer-use", "hooks-mode", "hooks-approval",
    "hook-context-intelligence", "hooks-routing",
)
WORK_AGENTS = frozenset({
    "computer-use:computer-operator", "context-intelligence:graph-analyst",
    "context-intelligence:session-navigator", "context-intelligence:server-data-ops",
})
WORK_PARTICIPANTS = ("unified-host", "loop-live", "context-managed",
                     "provider-openai", *WORK_TOOLS, *WORK_HOOKS)
_MINIMAL_PARTICIPANTS = ("unified-host", "loop-live", "context-simple",
                         "tool-filesystem", "provider-openai")
# Minimum reviewed semantic owners; integration may pin further dependencies.
# These are authored names, never discovered from the current callback graph.
_REQUIRED_SOURCES = frozenset({
    "amplifier_core/loader.py",
    "amplifier_foundation/session/media.py",
    "amplifier_foundation/session/media_projection.py",
    "amplifier_foundation/session/media_hook_bridge.py",
    "amplifier_foundation/session/checkpoint.py",
    "amplifier_web/host/media.py",
    "amplifier_web/host/media_assembly.py",
    "amplifier_web/host/session.py",
    "amplifier_web/host/storage.py",
    "amplifier_web/runtime_worker.py",
    "amplifier_web/surface_delivery.py",
    "amplifier_web/app_guidance.py",
    "amplifier_web/attachments.py",
    "amplifier_web/read_policy.py",
    "amplifier_web/file_source_guard.py",
    "amplifier_web/provider_recording.py",
    "amplifier_web/provider_capture.py",
    "amplifier_module_loop_live/orchestrator.py",
    "amplifier_module_loop_live/dispatch.py",
    "amplifier_module_loop_streaming/__init__.py",
    "amplifier_module_loop_streaming/dispatch.py",
    "amplifier_module_context_simple/__init__.py",
    "amplifier_module_tool_filesystem/__init__.py",
    "amplifier_module_tool_filesystem/read.py",
})
_WORK_SOURCES = frozenset({
    "amplifier_foundation/bundle/_prepared.py",
    "amplifier_web/host/initialization.py",
    "amplifier_web/host/components.py", "amplifier_web/host/ui_use_config.py",
    "amplifier_web/coordination_delivery.py", "amplifier_web/computer_episodes.py",
    "amplifier_web/tool_approval_scope.py", "amplifier_web/write_policy_refresh.py",
    "amplifier_web/preparation_context.py", "amplifier_web/context_continuity.py",
    "amplifier_module_context_managed/boundary.py",
    "amplifier_module_context_managed/request_context.py",
    "amplifier_module_context_managed/summary.py",
    "amplifier_module_loop_streaming/media.py",
    "amplifier_module_tool_exec/runner.py",
    "amplifier_module_hook_context_intelligence/handlers/logging_handler.py",
    "amplifier_module_hooks_approval/approval_hook.py",
    *("amplifier_module_" + name.replace("-", "_") + "/__init__.py"
      for name in ("context-managed", *WORK_TOOLS, *WORK_HOOKS)),
})


def supported_provider(spec):
    """One explicit OpenAI model; route facts remain the provider's public API."""
    if not isinstance(spec, dict) or spec.get("module") != "provider-openai":
        return False
    config = spec.get("config", {})
    return (isinstance(config, dict) and "auth_mode" not in config
            and config.get("default_model") == MEDIA_MODEL
            and not config.get("extra_request_params"))


def supported_request(plan, provider, request, options):
    """Check actual effective inputs without resolving aliases or changing defaults."""
    from ..provider_capture import provider_chain
    chain = provider_chain(provider)
    actual = chain[-1]() if chain else None
    extra = getattr(actual, "extra_request_params", {}) if actual is not None else None
    return (supported_plan(plan) and actual is not None
            and getattr(actual, "name", None) == "openai"
            and getattr(actual, "default_model", None) == MEDIA_MODEL
            and request.model == MEDIA_MODEL and options.get("model") == MEDIA_MODEL
            and not {"input", "extra_body", "extra_headers", "extra_query",
                     "single_attempt", "single_attempt_version", "background"} & options.keys()
            and isinstance(extra, dict) and not extra)


def supported_plan(plan):
    """Select explicit module roles, never every configured module ID."""
    def module(spec):
        return spec if isinstance(spec, str) else spec.get("module") if isinstance(spec, dict) else None
    session = plan.get("session", {})
    common = (
        module(session.get("orchestrator")) == "loop-live"
        and len(plan.get("providers", [])) == 1
        and supported_provider(plan["providers"][0])
        and not plan.get("spawn")
    )
    minimal = (
        module(session.get("context")) == "context-simple"
        and len(plan.get("tools", [])) == 1
        and module(plan["tools"][0]) == "tool-filesystem"
        and all(module(row) in _HOOKS for row in plan.get("hooks", []))
        and len(plan.get("hooks", [])) <= 1
        and not plan.get("agents")
    )
    return common and (minimal or ordinary_work_plan(plan))


def ordinary_work_plan(plan):
    """Exact root occurrence shape, retaining agents but not qualifying children."""
    session = plan.get("session", {})
    context = session.get("context", {})
    def exact(rows, names):
        return (isinstance(rows, list) and len(rows) == len(names)
                and all(isinstance(row, dict) for row in rows)
                and {row.get("module") for row in rows} == set(names)
                and all(row.get("id", row["module"]) == row["module"]
                        and row.get("instance_id", row["module"]) == row["module"]
                        and row.get("enabled", True) is True for row in rows))
    shape = (isinstance(context, dict) and context.get("module") == "context-managed"
            and context.get("config", {}).get("engine", "boundary") == "boundary"
            and exact(plan.get("tools", []), WORK_TOOLS)
            and exact(plan.get("hooks", []), WORK_HOOKS)
            and set(plan.get("agents", {})) == WORK_AGENTS)
    if not shape:
        return False
    tools = {row["module"]: row.get("config", {}) for row in plan["tools"]}
    # These select different construction/transport owners, not ordinary value
    # variations of this routed, local/unavailable Work default.
    return (not tools["tool-image"].get("builtin") and not tools["tool-image"].get("backend")
            and not tools["tool-computer-use"].get("target")
            and tools["tool-ui-use"].get("browser", {}).get("enabled") is not True)


def root_participants(plan):
    if not supported_plan(plan):
        return ()
    if ordinary_work_plan(plan):
        return WORK_PARTICIPANTS
    # Retained minimal source regressions are not the production Work profile.
    return _MINIMAL_PARTICIPANTS + (("hook-context-intelligence",) if plan.get("hooks") else ())


class ReviewedRootRecipe:
    """Exact selected plan/files plus actual callback code associations.

    Constructed only by the source assembly (or a synthetic test). Receivers are
    cooperative reviewed code, not hostile objects. Native lifecycle, config,
    mounted identities, registration order and final result are checked by the
    Foundation issuer, independently of this source selection.
    """
    def __init__(self, plan, sources):
        self.plan = copy.deepcopy(plan)
        self.sources = dict(sources)
        self.codes = {}
        if not self.sources:
            raise ValueError("A reviewed root requires explicit source pins")
        for filename, expected in self.sources.items():
            data = Path(filename).read_bytes()
            if (not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or len(data) > 2 * 1024 * 1024
                    or hashlib.sha256(data).hexdigest() != expected):
                raise ValueError("Reviewed root source changed")
            codes = []
            def collect(code):
                codes.append(code)
                for child in code.co_consts:
                    if isinstance(child, CodeType):
                        collect(child)
            collect(compile(data, filename, "exec", dont_inherit=True, optimize=0))
            self.codes[str(Path(filename).resolve())] = codes

    def current(self):
        return all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
                   for path, digest in self.sources.items())

    def accepts_plan(self, plan):
        if not self.current():
            return False
        if plan == self.plan:
            return True
        # The reviewed Work routing hook materializes absent role preferences
        # at session:start. These retained declarations do not execute in the
        # root image route. Preserve every declared field/explicit preference;
        # only additive derived preferences on these four roles may differ.
        # Foundation independently freezes the ENTIRE effective config during
        # actual construction and invalidates any later out-of-phase mutation.
        if not ordinary_work_plan(self.plan) or not ordinary_work_plan(plan):
            return False
        comparable = copy.deepcopy(plan)
        for name, original in self.plan["agents"].items():
            if original.get("model_role") and "provider_preferences" not in original:
                comparable["agents"][name].pop("provider_preferences", None)
        return comparable == self.plan

    def accepts_callable(self, value, seen=None):
        seen = set() if seen is None else seen
        if id(value) in seen:
            return True
        seen.add(id(value))
        if isinstance(value, partial):
            return self.accepts_callable(value.func, seen)
        if isinstance(value, MethodType):
            value = value.__func__
        elif not isinstance(value, FunctionType) and callable(value):
            value = getattr(type(value), "__call__", None)
        if not isinstance(value, FunctionType):
            return False
        filename = str(Path(value.__code__.co_filename).resolve())
        if value.__code__ not in self.codes.get(filename, ()):
            return False
        # In particular, Core loader closures must retain a selected mount.
        captured = [*(value.__defaults__ or ()), *(value.__kwdefaults__ or {}).values()]
        for cell in value.__closure__ or ():
            try:
                captured.append(cell.cell_contents)
            except ValueError:
                return False
        return all(self.accepts_callable(item, seen)
                   for item in captured if inspect.isfunction(item) or inspect.ismethod(item))

    def accepts_construction(self, graph):
        evidence = graph.participants_evidence()
        callbacks = [row["mount"] for row in evidence["loads"] if row["mounted"]]
        callbacks += [row["callback"] for row in evidence["ready"] if row["completed"]]
        callbacks += [row["original"] for key in
                      ("registrations", "mode_registrations", "contributions")
                      for row in evidence.get(key, ())]
        return bool(evidence["loads"] and self.current()
                    and all(self.accepts_callable(callback) for callback in callbacks))


def select_root(plan):
    """Use only shipped reviewed pins; no environment mutation or source fetch."""
    if not supported_plan(plan) or not ordinary_work_plan(plan):
        return None
    try:
        manifest = json.loads(Path(__file__).with_name("media_sources.json").read_text())
        if (manifest["recipe"] != RECIPE or manifest["status"] != "reviewed"
                or not (_REQUIRED_SOURCES | _WORK_SOURCES) <= manifest["sources"].keys()):
            return None
        provider = "amplifier_module_" + plan["providers"][0]["module"].replace("-", "_")
        if provider + "/__init__.py" not in manifest["sources"]:
            return None
        sources = {}
        for logical, digest in manifest["sources"].items():
            package, relative = logical.split("/", 1)
            if not re.fullmatch(r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*\.py", logical):
                return None
            spec = importlib.util.find_spec(package)
            if spec is None or spec.origin is None:
                return None
            sources[str((Path(spec.origin).parent / relative).resolve())] = digest
        return ReviewedRootRecipe(plan, sources)
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        return None