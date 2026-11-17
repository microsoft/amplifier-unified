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
_PROVIDERS = {"provider-openai-chatgpt", "provider-openai"}
_HOOKS = {"hook-context-intelligence"}
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


def supported_plan(plan):
    """Select explicit module roles, never every configured module ID."""
    def module(spec):
        return spec if isinstance(spec, str) else spec.get("module") if isinstance(spec, dict) else None
    session = plan.get("session", {})
    return (
        module(session.get("orchestrator")) == "loop-live"
        and module(session.get("context")) == "context-simple"
        and len(plan.get("tools", [])) == 1
        and module(plan["tools"][0]) == "tool-filesystem"
        and len(plan.get("providers", [])) == 1
        and module(plan["providers"][0]) in _PROVIDERS
        and all(module(row) in _HOOKS for row in plan.get("hooks", []))
        and len(plan.get("hooks", [])) <= 1
        and not plan.get("agents") and not plan.get("spawn")
    )


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
        return plan == self.plan and self.current()

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
    if not supported_plan(plan):
        return None
    try:
        manifest = json.loads(Path(__file__).with_name("media_sources.json").read_text())
        if (manifest["recipe"] != RECIPE or manifest["status"] != "reviewed"
                or not _REQUIRED_SOURCES <= manifest["sources"].keys()):
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