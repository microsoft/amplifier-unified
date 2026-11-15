"""One cooperative, packaged media recipe for an ordinary declared OpenAI graph.

Call ``select_from_declaration`` on the *authored*, configured Bundle mount plan
before prepare/create_session (and before opening canonical history). Pass its
return value to the existing ``media_graph_selection`` initialization seam. Do
not pass PreparedBundle.mount_plan, a session config, or construction evidence.

This selector changes no selections, defaults, credentials, environment, files,
or module search path. A matching custom bundle is fine; its name is not a grant.
Unknown inputs return None, preserving ordinary neutral/text behavior. Selection
is not admission: Foundation's actual issuer receipt, SourcePolicy and the
provider's actual request-support/account/options freeze remain mandatory.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import re
import sys

from .media_graph import NamedMediaGraph

RECIPE = "unified-openai-static-sol-local-capture-v1"
MODEL = "gpt-6.1-sol"
_LOOP_SOURCE = "git+https://github.com/microsoft/amplifier-module-loop-live@main"
_MODULES = {
    "loop-live": "amplifier_module_loop_live",
    "context-simple": "amplifier_module_context_simple",
    "provider-openai": "amplifier_module_provider_openai",
    "hook-context-intelligence": "amplifier_module_hook_context_intelligence",
    "tool-delegate": "amplifier_module_tool_delegate",
}
# These are the only optional provider settings reviewed for this recipe. In
# particular no dynamic credentials, native runtime, workload, extra_body,
# extra_request_params, recording, routing or alternate endpoint is selected.
_PROVIDER_KEYS = frozenset({"api_key", "default_model", "base_url", "use_streaming",
                            "reasoning_effort", "max_output_tokens"})
_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


def _plain(value, depth=0):
    """Bounded JSON primitives with exact Python types, not coercible objects."""
    if depth > 12:
        return False
    if value is None or type(value) in (str, bool, int):
        return type(value) is not str or len(value) <= 16384
    if type(value) is list:
        return len(value) <= 32 and all(_plain(item, depth + 1) for item in value)
    if type(value) is dict:
        return len(value) <= 32 and all(type(key) is str and _plain(item, depth + 1)
                                       for key, item in value.items())
    return False


def _catalog_sources():
    """Map fixed logical paths to import origins; never scan a site-packages tree.

    Expected digests are shipped literals. Reading current bytes verifies the
    reviewed join; it never manufactures an expected digest from those bytes.
    """
    catalog = json.loads(Path(__file__).with_name("media_sources.json").read_text())
    if (type(catalog) is not dict or set(catalog) != {"recipe", "revisions", "sources"}
            or catalog["recipe"] != RECIPE or type(catalog["sources"]) is not dict
            or not 1 <= len(catalog["sources"]) <= 128):
        raise ValueError("Invalid packaged media source catalog")
    roots, sources = {}, {}
    for logical, expected in catalog["sources"].items():
        if (type(logical) is not str or not re.fullmatch(r"[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*\.py", logical)
                or type(expected) is not str or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ValueError("Invalid media source identity")
        package, relative = logical.split("/", 1)
        if package not in roots:
            spec = importlib.util.find_spec(package)
            if (spec is None or type(spec.loader) is not importlib.machinery.SourceFileLoader
                    or not spec.origin or Path(spec.origin).name != "__init__.py"):
                raise ValueError("Unreviewed media import loader")
            roots[package] = Path(spec.origin).resolve().parent
        path = roots[package] / relative
        data = path.read_bytes()
        if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Packaged media source changed")
        sources[str(path.resolve())] = expected
    return roots, sources


def _source(value, package, roots):
    # Restrict the declaration as well as the eventual callback bytes. Explicit
    # local source roots/package directories are useful for ordinary custom
    # bundles; their names do not excuse a byte or callback mismatch.
    if type(value) is not str:
        return False
    root = roots[package]
    if value in (str(root), str(root.parent)):
        return True
    return (package == _MODULES["loop-live"] and value == _LOOP_SOURCE)


def _spec(value, module, roots):
    return (type(value) is dict and set(value) <= {"module", "source", "config"}
            and value.get("module") == module
            and _source(value.get("source"), _MODULES[module], roots)
            and type(value.get("config", {})) is dict)


def _declaration(plan, roots, background_delegate):
    if (not _plain(plan) or type(plan) is not dict
            or set(plan) - {"session", "providers", "hooks", "agents", "tools"}
            or not {"session", "providers", "hooks"} <= set(plan)
            or type(plan.get("agents", {})) is not dict or plan.get("agents", {}) != {}
            or type(plan.get("tools", [])) is not list):
        return False
    tools = plan.get("tools", [])
    # A single reviewed production delegate with ordinary defaults. Its source
    # must already be in the shipped literal catalog, not declared authority.
    if tools and (len(tools) != 1 or "amplifier_module_tool_delegate" not in roots
                  or not _spec(tools[0], "tool-delegate", roots)
                  or tools[0].get("config", {}) != {}):
        return False
    session = plan["session"]
    if type(session) is not dict or set(session) != {"orchestrator", "context"}:
        return False
    loop, context = session["orchestrator"], session["context"]
    if not _spec(loop, "loop-live", roots) or not _spec(context, "context-simple", roots):
        return False
    loop_config = loop.get("config", {})
    # Both omitted defaults and an already-host-adapted declaration are useful.
    # No other loop or context option is silently normalized away.
    if set(loop_config) - {"configured_bundle", "background_delegate", "inherit_effective_model"}:
        return False
    if "configured_bundle" in loop_config and loop_config["configured_bundle"] is not True:
        return False
    if "background_delegate" in loop_config and loop_config["background_delegate"] is not background_delegate:
        return False
    if "inherit_effective_model" in loop_config and type(loop_config["inherit_effective_model"]) is not bool:
        return False
    if context.get("config", {}) != {}:
        return False
    providers, hooks = plan["providers"], plan["hooks"]
    if type(providers) is not list or len(providers) != 1 or type(hooks) is not list or len(hooks) != 1:
        return False
    provider, capture = providers[0], hooks[0]
    if not _spec(provider, "provider-openai", roots) or not _spec(capture, "hook-context-intelligence", roots):
        return False
    config = provider.get("config", {})
    key = config.get("api_key")
    if (set(config) - _PROVIDER_KEYS or config.get("default_model") != MODEL
            or type(key) is not str or not key.strip() or "${" in key
            or config.get("base_url", "https://api.openai.com/v1") != "https://api.openai.com/v1"):
        return False
    if "use_streaming" in config and type(config["use_streaming"]) is not bool:
        return False
    if "reasoning_effort" in config and config["reasoning_effort"] not in _EFFORTS:
        return False
    if "max_output_tokens" in config and not (type(config["max_output_tokens"]) is int
                                               and 1 <= config["max_output_tokens"] <= 32768):
        return False
    cfg = capture.get("config", {})
    if (set(cfg) != {"destinations", "context_intelligence_server_url", "context_intelligence_api_key", "base_path"}
            or type(cfg["destinations"]) is not dict or cfg["destinations"] != {}
            or type(cfg["context_intelligence_server_url"]) is not str or cfg["context_intelligence_server_url"] != ""
            or type(cfg["context_intelligence_api_key"]) is not str or cfg["context_intelligence_api_key"] != ""
            or type(cfg["base_path"]) is not str or not Path(cfg["base_path"]).is_absolute()
            or "${" in cfg["base_path"] or ".." in Path(cfg["base_path"]).parts):
        return False
    return True


def _assemble(plan, roots, sources, *, child_plans=()):
    """Explicit reviewed semantic owners, not discovered callback dependencies."""
    from amplifier_core import HookResult, ModuleCoordinator, ProviderInfo
    from amplifier_core.message_models import ChatResponse, TextBlock
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_module_loop_live.orchestrator import BundleLiveOrchestrator, ConversationProviderPin
    from amplifier_module_context_simple import SimpleContextManager
    from amplifier_module_provider_openai import OpenAIProvider
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler
    from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver
    from amplifier_web.preparation_context import PreparedDelivery
    from amplifier_web.runtime_worker import Worker, ActivityRun
    from .children import ChildCheckpoint
    from .prompt_events import PromptEvents
    from .media import SourcePolicy
    from functools import partial
    import asyncio
    import time
    import uuid
    import amplifier_web.runtime_worker as worker
    import amplifier_module_loop_live.orchestrator as live
    import amplifier_module_context_simple as context
    import amplifier_module_provider_openai as provider
    import amplifier_module_hook_context_intelligence as capture

    # Whole environments are selected, never a pool of individually accepted
    # primitive values. None/int/bool remain distinct in NamedMediaGraph.
    def at(logical, qualname):
        package, relative = logical.split("/", 1)
        return (str((roots[package] / relative).resolve()), qualname)
    environments = {
        at("amplifier_module_context_simple/__init__.py", "mount"): {"config": None},
        at("amplifier_module_loop_live/orchestrator.py", "mount"): {"config": None},
        at("amplifier_module_provider_openai/__init__.py", "mount"): {"config": None},
        at("amplifier_module_hook_context_intelligence/__init__.py", "mount"): {"config": None},
        at("amplifier_core/loader.py", "ModuleLoader.load.<locals>.mount_with_config_fs"): [
            {"config": plan["session"]["orchestrator"]["config"]},
            {"config": plan["session"]["context"].get("config", {})},
            {"config": plan["providers"][0]["config"]},
            {"config": plan["hooks"][0]["config"]},
        ],
        at("amplifier_module_provider_openai/__init__.py", "mount.<locals>.<lambda>"): [
            {"_totals": {"cost_usd": None, "has_data": False}}, {},
        ],
    }
    dependencies = ()
    if plan.get("tools"):
        import amplifier_module_tool_delegate as delegate
        dependencies = (delegate.mount, delegate.DelegateTool)
        environments[at("amplifier_core/loader.py", "ModuleLoader.load.<locals>.mount_with_config_fs")].append(
            {"config": plan["tools"][0].get("config", {})})
    return NamedMediaGraph(
        RECIPE, plans=[plan, *child_plans], sources=sources,
        dependencies=(HookResult, ModuleCoordinator, ProviderInfo, ChatResponse, TextBlock,
                      BundleLiveOrchestrator, ConversationProviderPin, SimpleContextManager,
                      OpenAIProvider, asyncio, time, uuid, worker.publish,
                      live.mount, context.mount, provider.mount, capture.mount,
                      capture.on_session_ready, LoggingHandler.__call__, *dependencies),
        capability_owners=(
            ("capture-handler", "context_intelligence._hook_state", ("logging_handler",)),
            ("capture-resolver", "context_intelligence.hook_config_resolver", ())),
        environments=environments,
        owner_rules=(("media-source-policy", SourcePolicy, ("coordinator",)),
                     ("worker", Worker, ("media_graph_selection", "telemetry", "response_blocks")),
                     ("runtime", Runtime, ("session_id",)),
                     ("coordinator", ModuleCoordinator, ("session_id",)),
                     ("activity-run", ActivityRun, ("worker", "coordinator", "identity")),
                     ("prompt-events", PromptEvents, ("coordinator", "current")),
                     ("child-checkpoint", ChildCheckpoint,
                      ("registry", "identity", "store", "binding", "child", "row", "metadata", "completion")),
                     ("checkpoint", partial, ("func", "args", "keywords")),
                     ("capture-handler", LoggingHandler, ("_resolver",)),
                     ("capture-resolver", HookConfigResolver, ()),
                     ("request-delivery", PreparedDelivery, ("owner", "delivery"))),
    )


def select_from_declaration(authored_plan, *, workspace, session_id, bundle_name,
                            application_host="Amplifier Web", background_delegate=True):
    """Return NamedMediaGraph or None; never enable pixels or modify the input.

    Input is the configured, source-normalized *authored* Bundle declaration
    immediately before prepare. No new bundle/profile is selected here, and no
    missing tools/hooks/defaults are removed to force a match. Only the ordinary
    live overlay, host identity and Foundation event injection are anticipated.
    API keys remain private in-memory plan data, never written to the catalog.
    Call again for another root/child declaration; do not reuse a root grant for
    a child with a different graph. The parent owns constructor/FD ordering.
    """
    if (sys.platform != "linux" or sys.implementation.name != "cpython"
            or sys.version_info[:2] != (3, 13) or type(background_delegate) is not bool):
        return None
    if (type(session_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", session_id)
            or type(bundle_name) is not str or not bundle_name or len(bundle_name) > 4000
            or type(application_host) is not str
            or application_host not in ("Amplifier Web", "Amplifier Unified")
            or not isinstance(workspace, (str, Path)) or not Path(workspace).is_absolute()):
        return None
    try:
        roots, sources = _catalog_sources()
        if not _declaration(authored_plan, roots, background_delegate):
            return None
        # Build an independent expected plan from the restricted declaration,
        # not a copy of anything prepared/observed after construction.
        plan = copy.deepcopy(authored_plan)
        loop_config = {"configured_bundle": True, "background_delegate": background_delegate}
        if "inherit_effective_model" in plan["session"]["orchestrator"].get("config", {}):
            loop_config["inherit_effective_model"] = plan["session"]["orchestrator"]["config"]["inherit_effective_model"]
        plan["session"]["orchestrator"].update(source=_LOOP_SOURCE, config=loop_config)
        from amplifier_web.session_files import project_slug
        workspace = Path(workspace)
        plan.update(agents={}, application_host=application_host, root_session_id=session_id,
                    project_slug=project_slug(workspace), bundle_name=bundle_name,
                    project_dir=str(workspace), project_name=workspace.name)
        # Fixed Foundation886 event contract; source join verifies the producer.
        plan["hooks"][0]["config"]["additional_events"] = ["session:config", "mentions:resolved"]
        child_plans = []
        if plan.get("tools"):
            # Independently anticipate only ordinary self-delegation's default
            # tool exclusion and host ceilings, before any prepare/create call.
            # Retained credentials, model roles and unknown overlays cannot
            # turn an observed child plan into another admitted declaration.
            child = copy.deepcopy(plan)
            child.update(tools=[], agents={}, _unified_image_policy={}, _unified_desktop_policy={})
            child_plans.append(child)
        return _assemble(plan, roots, sources, child_plans=child_plans)
    except (ImportError, OSError, ValueError, TypeError, AttributeError, KeyError):
        return None
