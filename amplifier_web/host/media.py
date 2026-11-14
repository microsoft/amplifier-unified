"""Session-owned media binding and private provider projections.

Foundation owns reference validation, assets and checkpoint proof. This host
chooses storage and execution scope; a reference is never an authority grant.
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

NAMESPACE = "experimental.media.v1"
_HOOK_BRIDGE = "web.media.hook_bridge"
_HOOK_GRAPH = "web.media.hook_graph"
_HOST_PARTICIPANT = "unified-host"
_REQUEST_ONLY = ContextVar("unified_request_only_images", default=None)
_CONSTRUCTIONS = {}


def registration_coordinator(coordinator):
    """An explicit registration view; execution keeps the real coordinator."""
    getter = getattr(coordinator, "get_capability", None)
    bridge = getter(_HOOK_BRIDGE) if callable(getter) else None
    if bridge is not None:
        return bridge.participant(_HOST_PARTICIPANT)
    return coordinator


def registration_hooks(coordinator):
    """Select the construction-owned registration view, never replace Core."""
    return getattr(registration_coordinator(coordinator), "hooks", None)


def mint_graph_owner(coordinator, obj, *, kind, associations=()):
    selection = coordinator.get_capability("web.media.graph_selection")
    if selection is not None:
        return selection.mint_owner(obj, kind=kind, associations=associations)
    return False

class RequestGraphLifetime:
    """Synchronous detached validator; no ContextVar token crosses a yield."""
    def __init__(self, factory):
        self.context = factory()
        self.validator = self.context.__enter__()
        self.closed = False
        try:
            self.validate()
        except BaseException:
            self.release()
            raise

    def validate(self):
        validator = self.validator if callable(self.validator) else getattr(self.validator, "validate", None)
        if self.closed or not callable(validator) or validator() is not True:
            raise ValueError("Prepared media graph generation is unavailable")
        return True

    def release(self):
        if not self.closed:
            self.closed = True
            self.context.__exit__(None, None, None)


def request_graph_lifetime(coordinator):
    if coordinator is None:
        return None
    state = _CONSTRUCTIONS.get(id(coordinator))
    if state is None or state[0] is not coordinator:
        return None
    bridge = state[1]
    return RequestGraphLifetime(bridge.request_scope)


def neutral_control_media(coordinator):
    store = coordinator.get_capability("web.media.store")
    if store is None:
        return False
    from amplifier_foundation.session.media import NeutralSessionMediaStore
    return type(store) is NeutralSessionMediaStore


def begin_hook_graph(session, *, selection=None):
    """Capture this actual construction, before any host/module registration.

    Participant IDs select the supplied cooperative registration views. They are
    not qualification: only the issuer's actual construction and emission
    witnesses can qualify a result. Unknown raw/native registrations stay out.
    """
    coordinator = session.coordinator
    from .media_graph import NamedMediaGraph
    if type(selection) is not NamedMediaGraph or not selection.accepts_plan(session.config):
        return
    try:
        from amplifier_core import AmplifierSession
        from amplifier_foundation.session.media_hook_bridge import (
            MediaHookBridge, install_media_hook_bridge,
        )
    except ImportError:
        return
    if type(session) is not AmplifierSession:
        return  # Structural/text-only hosts cannot manufacture a Core witness.
    if not callable(getattr(MediaHookBridge, "begin_participating_graph", None)):
        return
    config = session.config
    participants = [_HOST_PARTICIPANT]
    for kind, default in (("orchestrator", "loop-basic"), ("context", "context-simple")):
        spec = config.get("session", {}).get(kind, default)
        participants.append(spec if isinstance(spec, str) else spec["module"])
    for kind in ("providers", "tools", "hooks"):
        participants.extend(row["module"] for row in config.get(kind, []) if row.get("module"))
    bridge = install_media_hook_bridge(coordinator)
    begin = getattr(bridge, "begin_participating_graph", None)
    if not callable(begin):
        return  # Older safe bridges cannot produce a positive construction.
    graph = begin(session, owner=_HOST_PARTICIPANT, participants=tuple(dict.fromkeys(participants)))
    coordinator.register_capability(_HOOK_BRIDGE, bridge)
    coordinator.register_capability(_HOOK_GRAPH, graph)
    coordinator.register_capability("web.media.graph_selection", selection)
    _CONSTRUCTIONS[id(coordinator)] = [coordinator, bridge, graph, selection, None]


def seal_hook_graph(coordinator):
    """Called by the actual final host preparation phase, never a readiness hook."""
    state = _CONSTRUCTIONS.get(id(coordinator))
    if state is None or state[0] is not coordinator:
        return False
    _, bridge, graph, selection, _ = state
    if (coordinator.get_capability(_HOOK_GRAPH) is not graph or
            coordinator.get_capability(_HOOK_BRIDGE) is not bridge or
            coordinator.get_capability("web.media.graph_selection") is not selection):
        return False
    if (selection is None or not selection.accepts_plan(coordinator.config)
            or not selection.accepts_construction(graph)):
        coordinator.register_capability("web.media.source_join", None)
        enable_media(coordinator)
        return False
    if bridge.graph_receipt is None:
        try:
            graph.seal()
        except ValueError:
            return False
    coordinator.register_capability("web.media.source_join", (
        bridge, graph, bridge.graph_receipt, selection))
    state[4] = bridge.graph_receipt
    enable_media(coordinator)
    return bridge.validate_receipt(bridge.graph_receipt) is True


@contextmanager
def hook_configuration(coordinator):
    """Track actual saved settings or an idle edit in a new issuer generation."""
    graph = coordinator.get_capability(_HOOK_GRAPH)
    bridge = coordinator.get_capability(_HOOK_BRIDGE)
    if graph is None or bridge is None:
        yield
        return
    coordinator.register_capability("web.media.source_join", None)
    capability = coordinator.get_capability(NAMESPACE)
    if capability is not None:
        capability.enabled = False
    if bridge.graph_receipt is None:
        with graph.configure():
            yield
    else:
        with graph.transition() as successor:
            state = _CONSTRUCTIONS.get(id(coordinator))
            if state is None or state[0] is not coordinator or state[2] is not graph:
                raise ValueError("Media construction ownership changed")
            state[2], state[4] = successor, None
            coordinator.register_capability(_HOOK_GRAPH, successor)
            yield
        seal_hook_graph(coordinator)


async def mount_media_adapter(coordinator, kind, value, *, previous, name):
    """Record the actual host wrapper edge before sealing a named construction."""
    if value is previous:
        return
    bridge = coordinator.get_capability(_HOOK_BRIDGE)
    if bridge is None:
        return
    view = bridge.participant(_HOST_PARTICIPANT)
    await view.mount_adapter(kind, value, previous=previous, name=name)


def replace_media_adapter(coordinator, kind, name, previous, wrapped):
    """Retain a synchronous host wrapper decision for its async mount boundary."""
    if wrapped is previous:
        return
    if coordinator.get_capability(_HOOK_BRIDGE) is None:
        (coordinator.get(kind) or {})[name] = wrapped
        return
    pending = coordinator.get_capability("web.media.pending_adapters")
    if pending is None:
        pending = []
        coordinator.register_capability("web.media.pending_adapters", pending)
    if not any(row[0] == kind and row[1] == name and row[2] is previous and row[3] is wrapped
               for row in pending):
        pending.append((kind, name, previous, wrapped))


async def flush_media_adapters(coordinator):
    pending = coordinator.get_capability("web.media.pending_adapters") or []
    while pending:
        kind, name, previous, wrapped = pending[0]
        await mount_media_adapter(coordinator, kind, wrapped, previous=previous, name=name)
        pending.pop(0)


async def control_tool_post(coordinator, event):
    """Settle the separate UI/control invocation once, without claiming a turn.

    These calls have no canonical tool-message append owner. They can return the
    final post-hook text, but must drop staged durable refs rather than borrow a
    loop checkpoint or return a promotable uncommitted carrier.
    """
    boundary = coordinator.get_capability("session.media.tool_post")
    if callable(boundary):
        settled = await boundary(event, hooks=coordinator.hooks, coordinator=coordinator)
        processed = settled["hook_result"]
        value = settled["result"] if settled.get("complete") is True else None
    else:
        handlers = coordinator.hooks.list_handlers("tool:post").get("tool:post", [])
        processed = await coordinator.hooks.emit("tool:post", event)
        processed = await coordinator.process_hook_result(processed, "tool:post", event["tool_name"])
        # Core's aggregate can lose replacement/null/Continue-after-mutation.
        # Without the structural owner there is no truthful effective result.
        value = None if handlers else event["result"]
    if processed.action == "deny":
        return {"success": False, "error": {"message": processed.reason or "Denied by session policy"}}
    if isinstance(value, dict):
        output = value.get("output")
        if isinstance(output, dict) and NAMESPACE in output:
            value = copy.deepcopy(value)
            value["output"].pop(NAMESPACE, None)
            value["mediaDelivery"] = {"available": False,
                "reason": "Control invocations do not append canonical media history"}
    return value


async def drop_control_media(capability, session_id, call_id, *, neutral=False):
    """Settle the captured actual origin, never mutable post-event identity."""
    if not neutral and capability is not None and callable(getattr(capability, "publish", None)):
        await capability.publish([], origin={"kind": "tool", "session_id": session_id,
                                             "id": call_id}, disposition="drop")


async def prepare_control_result(coordinator, result, call_id):
    """Externalize typed Core image parts before any post observer can see them."""
    from amplifier_core import ToolResult
    from amplifier_core.message_models import ImageBlock, TextBlock
    if not isinstance(result, ToolResult):
        return result
    images = any(isinstance(block, ImageBlock) for block in result.content or ())
    capability = coordinator.get_capability(NAMESPACE)
    if images and capability is not None and getattr(capability, "enabled", False):
        admitted = await capability.ingest(result, source_format="core-tool-result",
            origin={"kind": "tool", "session_id": coordinator.session_id, "id": call_id}, policy=None)
        return admitted["value"].model_dump()
    if images:
        result = result.model_copy(deep=True)
        result.content = [TextBlock(text="[Image unavailable: control invocation has no durable media admission]")
                          if isinstance(block, ImageBlock) else block for block in result.content]
    return result.model_dump()


def record_request_only(message, *, validate=None):
    """Called only where an authorized delivery owner mints its typed message."""
    issued = _REQUEST_ONLY.get()
    if issued is not None and issued["task"] is asyncio.current_task():
        for block in message.content if isinstance(message.content, list) else ():
            if getattr(block, "type", None) == "image":
                issued["blocks"].append((block, validate))
    return message


@contextmanager
def request_only_scope():
    issued = {"blocks": [], "task": asyncio.current_task()}
    token = _REQUEST_ONLY.set(issued)
    try:
        yield issued["blocks"]
    finally:
        _REQUEST_ONLY.reset(token)


class RequestOnlyRegistry:
    """Exact private issuance associations; metadata never creates admission."""
    def __init__(self):
        self.requests = {}

    def bind(self, request, issued):
        blocks = [(i, j, block, json.dumps(block.source, sort_keys=True), validator)
                  for i, message in enumerate(request.messages)
                  for j, block in enumerate(message.content if isinstance(message.content, list) else ())
                  for original, validator in issued if block is original]
        if blocks:
            self.requests[id(request)] = (request, blocks, asyncio.current_task())

    def transfer(self, source, target):
        if id(source) in self.requests:
            self.bind(target, [(row[2], row[4]) for row in self.requests[id(source)][1]])

    def validate(self, request, *, message_index, block_index, purpose):
        row = self.requests.get(id(request))
        if row is None or row[0] is not request:
            return False
        if row[2] is not asyncio.current_task():
            return False
        block = request.messages[message_index].content[block_index]
        return any(i == message_index and j == block_index and original is block
                   and json.dumps(block.source, sort_keys=True) == encoded
                   and (validator is None or validator() is True)
                   for i, j, original, encoded, validator in row[1])

    def release(self, request):
        self.requests.pop(id(request), None)

    def contains(self, request):
        return id(request) in self.requests


class SourcePolicy:
    """Per-origin source classification, not a bearer screenshot-consent token."""
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.pending = {}
        self.store = None

    def _tools(self):
        for tool in (self.coordinator.get("tools") or {}).values():
            yield tool

    def computer(self, ref, *, purpose):
        from amplifier_foundation.session.media import MediaRefusal
        for tool in self._tools():
            validator = getattr(tool, "_validate_media_ref", None)
            if callable(validator):
                try:
                    accepted = validator(ref, purpose=purpose)
                except ValueError:
                    continue
                if accepted is True:
                    self.store.classify_origin(ref, classification="computer-use")
                    return True
        raise MediaRefusal("Original screenshot source policy is unavailable", code="source_policy")

    def validate(self, ref, *, purpose):
        from amplifier_foundation.session.media import MediaRefusal
        provenance = ref["provenance"]
        if provenance["kind"] != "tool":
            return True
        classification = self.store.get_origin_classification(ref)
        if classification == "computer-use":
            return self.computer(ref, purpose=purpose)
        if classification != "tool":
            raise MediaRefusal("Original image source policy is unavailable", code="source_policy")
        return True

    async def observe(self, event, data):
        from amplifier_core import HookResult
        identity, name = data.get("tool_call_id"), data.get("tool_name")
        tool = (self.coordinator.get("tools") or {}).get(name)
        if identity and tool is not None:
            self.pending[identity] = "computer-use" if callable(getattr(tool, "_validate_media_ref", None)) else "tool"
        return HookResult()

    def settle(self, canonical):
        for row in canonical:
            envelope = (row.get("metadata") or {}).get(NAMESPACE)
            if not isinstance(envelope, dict):
                continue
            for ref in envelope.get("refs", []):
                provenance = ref.get("provenance") or {}
                identity = provenance.get("id")
                if (provenance.get("kind") == "tool"
                        and provenance.get("session_id") == self.coordinator.session_id
                        and identity in self.pending):
                    self.store.classify_origin(ref, classification=self.pending[identity])
        for row in canonical:
            if row.get("role") == "tool":
                self.pending.pop(row.get("tool_call_id"), None)
        # An earlier tool:post save may precede this call's canonical append.
        # Keep its classification until the actual settled checkpoint.

def has_media(request):
    return any(NAMESPACE in (getattr(message, "metadata", None) or {})
               for message in getattr(request, "messages", ()))


def effective_options(request, options):
    result = copy.deepcopy(options or {})
    for key in ("model", "reasoning_effort"):
        value = getattr(request, key, None)
        if value is not None:
            result.setdefault(key, value)
    return result


def _durable_media_supported():
    return os.name == "posix"


def bind_media(session, store, *, selection=None):
    """Run before initialization, including for every newly resumed coordinator."""
    coordinator = session.coordinator
    try:
        from amplifier_foundation.session.media import (
            bind_session_media,
        )
    except ImportError:
        # Old generations stay text-usable, but cannot silently send ref metadata
        # as if it were vision. prepare_media refuses their ref-bearing requests.
        coordinator.register_capability("web.media.unavailable", True)
        return
    begin_hook_graph(session, selection=selection)
    policy = SourcePolicy(coordinator)
    request_only = RequestOnlyRegistry()
    coordinator.register_capability("web.media.request_only", request_only)
    coordinator.register_capability("computer_use.media_policy", policy.computer)
    coordinator.register_capability("web.media.source_policy", policy)
    registration_hooks(coordinator).register("tool:pre", policy.observe,
                                            name="unified-media-source", priority=-100)
    if _durable_media_supported():
        media = store.media(session.session_id, policy_verifier=policy.validate,
                            request_only_verifier=request_only.validate)
    else:
        from amplifier_foundation.session.media import NeutralSessionMediaStore
        media = NeutralSessionMediaStore(session.session_id)
    policy.store = media
    mint_graph_owner(coordinator, policy, kind="media-source-policy")
    # Module readiness reports describe compatibility, never execution authority.
    # Enable only after the selected loop and actual checkpoint seam are mounted.
    capability = bind_session_media(coordinator, store=media, enabled=False)
    original_publish = capability.publish
    async def publish(refs, *, origin, disposition):
        if (origin.get("kind") == "tool" and origin.get("session_id") == coordinator.session_id
                and origin.get("id") in policy.pending):
            for ref in refs:
                provenance = ref.get("provenance") or {}
                if (provenance.get("kind"), provenance.get("session_id"), provenance.get("id")) != (
                        origin["kind"], origin["session_id"], origin["id"]):
                    raise ValueError("Media publication does not match the actual tool origin")
                # Publish validates bytes before the canonical append; bind the
                # genuine observed tool owner before that source-policy check.
                media.classify_origin(ref, classification=policy.pending[origin["id"]])
        return await original_publish(refs, origin=origin, disposition=disposition)
    capability.publish = publish
    if coordinator.get_capability(_HOOK_BRIDGE) is not None:
        original_ingest = capability.ingest
        async def ingest(*args, **kwargs):
            # Later raw registration/configuration/remount invalidates its
            # receipt. A cached flag cannot authorize another producer.
            enable_media(coordinator)
            return await original_ingest(*args, **kwargs)
        capability.ingest = ingest
    original_report = capability.report
    def report(component, *, event, outcome, details=None):
        facts = dict(details or {})
        if event == "consumer-ready":
            facts["ready"] = outcome == "ready"
        return original_report(component, event=event, outcome=outcome, details=facts)
    capability.report = report
    projected = {}
    coordinator.register_capability("web.media.projections", projected)
    original_validate = capability.validate
    def validate(value, *, purpose, request_options=None):
        projection = projected.get(id(value))
        if (projection is not None and projection.request is value
                and projection.request_only and purpose in {"transport", "provider-send"}):
            if not projection.transport_armed:
                raise ValueError("Request-only image requires a fresh host consent check before another transport attempt")
            projection.transport_armed = False
        return original_validate(value, purpose=purpose, request_options=request_options)
    capability.validate = validate
    coordinator.register_capability("web.media.store", media)
    from ..attachments import encode
    async def encode_input(command):
        return await encode(command, coordinator)
    coordinator.register_capability("live.attachments.encode", encode_input)
    cleanup = getattr(coordinator, "register_cleanup", None)
    if callable(cleanup):
        async def close():
            capability.enabled = False
            if coordinator.get_capability(_HOOK_BRIDGE) is not None:
                coordinator.register_capability("session.media.tool_post", None)
            request_only.requests.clear()
            projected.clear()
            media.close()
            state = _CONSTRUCTIONS.get(id(coordinator))
            if state is not None and state[0] is coordinator:
                _CONSTRUCTIONS.pop(id(coordinator), None)
        cleanup(close)


def enable_media(coordinator):
    capability = coordinator.get_capability(NAMESPACE)
    if capability is None or not callable(getattr(capability, "report", None)):
        return
    readiness = capability.report("unified-host", event="readiness", outcome="inspect")
    consumers = readiness.get("consumers") or {}
    loop_ready = any(isinstance(facts, dict) and facts.get("role") == "loop"
                     and type(facts.get("contract")) is int and facts["contract"] == 1
                     and facts.get("ready") is True
                     for facts in consumers.values())
    bridge = coordinator.get_capability(_HOOK_BRIDGE)
    join = coordinator.get_capability("web.media.source_join")
    selection = coordinator.get_capability("web.media.graph_selection")
    graph = coordinator.get_capability(_HOOK_GRAPH)
    state = _CONSTRUCTIONS.get(id(coordinator))
    qualified = (bridge is not None and isinstance(join, tuple) and len(join) == 4 and
                 state is not None and state[0] is coordinator and state[1] is bridge and
                 state[2] is graph and state[3] is selection and state[4] is join[2] and
                 join[0] is bridge and join[1] is graph and
                 join[2] is bridge.graph_receipt and join[3] is selection and
                 bridge.validate_receipt(join[2]) is True and
                 selection.accepts_plan(coordinator.config) and selection.accepts_construction(graph))
    capability.enabled = bool(qualified and loop_ready and _durable_media_supported() and
                              callable(coordinator.get_capability("session.durable_checkpoint")))


def acknowledge_media(coordinator, store, session_id, canonical):
    """ACK the actual settled save; never save again or turn a report into proof."""
    media = coordinator.get_capability("web.media.store")
    if media is None:
        return
    policy = coordinator.get_capability("web.media.source_policy")
    if policy is not None:
        policy.settle(canonical)
    if not any(NAMESPACE in (row.get("metadata") or {}) for row in canonical):
        return  # Ordinary text sessions do not acquire an extra O(history) scan.
    if not _durable_media_supported():
        # The checkpoint owner has not qualified directory durability on Windows.
        return
    store.acknowledge_media(session_id, canonical, media)


def _provider_owner(provider):
    from ..provider_capture import provider_chain
    try:
        chain = provider_chain(provider)
    except TypeError:  # Slotted/unhashable plugins still have their own identity.
        chain = None
    # Only edges issued by host wrappers identify an underlying account. An
    # arbitrary plugin.original attribute is data, not a trusted identity edge.
    return chain[-1]() if chain else provider


def _scope(coordinator):
    getter = coordinator.get_capability("web.work_origin_scope")
    value = getter() if callable(getter) else None
    return copy.deepcopy(value)


def _media_support(provider, source, model, options):
    """Use indexed eligibility when present; never broaden it through v1."""
    missing = object()
    indexed = getattr(provider, "get_media_request_support_for_request", missing)
    if indexed is not missing:
        if not callable(indexed):
            raise ValueError("Indexed media request support must be synchronous")
        snapshot, selected_options = source.model_copy(deep=True), copy.deepcopy(options)
        before, frozen_options = snapshot.model_dump_json(), copy.deepcopy(selected_options)
        try:
            support = indexed(request=snapshot, model=model, request_options=selected_options)
            if inspect.isawaitable(support):
                if inspect.iscoroutine(support):
                    support.close()
                raise ValueError("Indexed media request support must be synchronous")
            if snapshot.model_dump_json() != before or selected_options != frozen_options:
                raise ValueError("Indexed media support mutated its read-only input")
            from amplifier_foundation.session.media_projection import validate_media_request_support
            return validate_media_request_support(support, source)
        finally:
            for message in snapshot.messages:
                for block in message.content if isinstance(message.content, list) else ():
                    if getattr(block, "type", None) == "image":
                        block.source.clear()
            snapshot.messages = []
    support = getattr(provider, "get_media_request_support", None)
    value = support(model=model, request_options=copy.deepcopy(options)) if callable(support) else None
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise ValueError("Media support must be synchronously verifiable")
    return value if isinstance(value, dict) and value.get("v") == 1 else {"v": 1, "positions": []}


def _live_binding(coordinator, provider, model, options, source):
    from ..provider_capture import normalized_options
    value = _media_support(provider, source, model, options)
    return (
        normalized_options({"config": getattr(_provider_owner(provider), "config", None),
                            "support": value, "default_model": getattr(provider, "default_model", None)}),
        coordinator.get_capability("web.worker_run"),
    )


class HostMediaProjection:
    """One candidate's witness. Never stored in Message metadata or history."""
    def __init__(self, coordinator, provider, source, options, prepared, scope, model=None):
        self.coordinator, self.provider = coordinator, provider
        self.owner = _provider_owner(provider)
        providers = coordinator.get("providers") or {}
        self.mounts = [name for name, mounted in providers.items()
                       if _provider_owner(mounted) is self.owner]
        self.source, self.snapshot = source, source.model_copy(deep=True)
        self.options, self.scope = copy.deepcopy(options), scope
        self.prepared = prepared
        self.request = prepared.request
        self.capability = coordinator.get_capability(NAMESPACE)
        registry = coordinator.get_capability("web.media.request_only")
        self.request_only = registry is not None and registry.contains(source)
        self.transport_armed = False
        self.transport_started = False
        self.model = model
        self.canonical = self.canonical_snapshot = None
        self.graph_lifetime = None
        self.live_binding = _live_binding(coordinator, provider, model, options, self.snapshot)
        install = getattr(prepared, "set_binding_validator", None)
        if callable(install):
            install(self.validate_binding)
        projected = coordinator.get_capability("web.media.projections")
        if projected is not None:
            projected[id(self.request)] = self

    def validate_binding(self):
        if self.graph_lifetime is not None:
            self.graph_lifetime.validate()
        if (self.prepared is None or self.source != self.snapshot
                or self.canonical is not None and self.canonical != self.canonical_snapshot
                or self.coordinator.get_capability(NAMESPACE) is not self.capability
                or _provider_owner(self.provider) is not self.owner
                or any(_provider_owner((self.coordinator.get("providers") or {}).get(name))
                       is not self.owner for name in self.mounts)
                or _live_binding(self.coordinator, self.provider, self.model, self.options, self.snapshot) != self.live_binding
                or _scope(self.coordinator) != self.scope):
            raise ValueError("Prepared media request identity changed")
        return True

    def bind_canonical(self, request):
        self.canonical, self.canonical_snapshot = request, request.model_copy(deep=True)

    def validate(self):
        self.validate_binding()
        self.prepared.validate(request_options=self.options)

    def matches_options(self, options):
        from ..provider_capture import normalized_options
        return normalized_options(effective_options(self.source, options)) == normalized_options(self.options)

    def release(self):
        if self.graph_lifetime is not None:
            self.graph_lifetime.release()
            self.graph_lifetime = None
        projected = self.coordinator.get_capability("web.media.projections")
        if projected is not None:
            projected.pop(id(self.request), None)
        if self.prepared is not None:
            self.prepared.release()
            self.prepared = None
        registry = self.coordinator.get_capability("web.media.request_only")
        if registry is not None:
            registry.release(self.source)
            registry.release(getattr(self, "projection_source", None))
        self.request = self.source = self.snapshot = None
        self.canonical = self.canonical_snapshot = None
        self.projection_source = None


async def prepare_media(coordinator, provider, request, *, request_options=None,
                        purpose="model"):
    capability = coordinator.get_capability(NAMESPACE) if coordinator is not None else None
    if not has_media(request) and not (
            getattr(capability, "version", None) == 1
            and (callable(getattr(provider, "get_media_request_support", None))
                 or hasattr(provider, "get_media_request_support_for_request"))):
        return None
    if capability is None or getattr(capability, "version", None) != 1:
        capability = NeutralMedia(coordinator)
    from ..provider_capture import normalized_options
    options = effective_options(request, request_options)
    if normalized_options(options) is None:
        raise ValueError("Prepared media request options are unsupported")
    owner = _provider_owner(provider)
    model = options.get("model") or request.model or getattr(provider, "default_model", None)
    support = _media_support(provider, request, model, options)
    # Unknown support is explicitly no-vision. Do not infer tool placement from
    # a general vision model tag or change the user's selected account/model.
    if not isinstance(support, dict) or support.get("v") not in {1, 2}:
        support = {"v": 1, "positions": []}
    scope = _scope(coordinator)
    from ..execution_events import CALL_PURPOSE
    if CALL_PURPOSE.get() and purpose != "context-compaction":
        scope = {**(scope or {}), "text_only": True}
    identity = {"provider": type(owner).__module__ + "." + type(owner).__qualname__,
                "account": str(id(owner)),
                "generation": str(coordinator.get_capability("web.worker_run") or id(coordinator)),
                "media_support": support}
    projection_source = request.model_copy(update={"model": model})
    registry = coordinator.get_capability("web.media.request_only")
    if registry is not None:
        registry.transfer(request, projection_source)
    try:
        prepared = await capability.prepare_request(
            projection_source, provider_identity=identity, model=model, request_options=options,
            purpose=purpose, execution_scope=scope,
        )
    except BaseException:
        if registry is not None:
            registry.release(request)
            registry.release(projection_source)
        raise
    try:
        projection = HostMediaProjection(coordinator, provider, request, options, prepared, _scope(coordinator), model)
    except BaseException:
        prepared.release()
        if registry is not None:
            registry.release(projection_source)
        raise
    projection.projection_source = projection_source
    return projection


class NeutralPrepared:
    """Validated reference-only history remains usable by text-only consumers."""
    def __init__(self, request, options, validate):
        self.request, self.options, self._validate = request, options, validate

    def validate(self, *, request_options=None):
        if self.request is None or request_options != self.options:
            raise ValueError("Neutral media request changed")
        self._validate()

    def release(self):
        self.request = self._validate = None


class NeutralMedia:
    """No hydration; identity is still checked by the canonical media owner."""
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def prepare_request(self, request, *, request_options, **kwargs):
        from amplifier_foundation.session.media import validate_blob_ref
        store = self.coordinator.get_capability("web.media.store") if self.coordinator is not None else None
        if store is None:
            raise ValueError("Image reference ownership cannot be verified by this installed reader")
        refs = []
        projected = request.model_copy(deep=True)
        for message in projected.messages:
            if isinstance(message.content, list):
                from amplifier_core.message_models import TextBlock
                message.content = [
                    TextBlock(text="[Image unavailable: compatible media handling is not installed; no pixels supplied]")
                    if getattr(block, "type", None) == "image" else block
                    for block in message.content]
            envelope = (message.metadata or {}).get(NAMESPACE)
            if envelope is None:
                continue
            if not isinstance(envelope, dict) or set(envelope) != {"refs"} or not isinstance(envelope["refs"], list):
                raise ValueError("Malformed media reference envelope")
            refs.extend(validate_blob_ref(ref) for ref in envelope["refs"])
            warning = "[Image unavailable: compatible media handling is not installed; no pixels supplied]"
            if isinstance(message.content, str):
                message.content += "\n" + warning
            else:
                from amplifier_core.message_models import TextBlock
                message.content.append(TextBlock(text=warning))
        def validate():
            for ref in refs:
                # Expiry prevents pixels, not truthful text-only history. Scope,
                # publication and canonical checkpoint proof remain mandatory.
                store.validate_ref(ref, check_expiry=False)
        validate()
        return NeutralPrepared(projected, request_options, validate)


class MediaOnlyDelivery:
    """No surface refresh, consent renewal, Work observation or fresh capture."""
    async def prepare(self, request, provider):
        return request

    def commit(self, request):
        pass


def execution_providers(coordinator, providers):
    """Install after native selection; custom finite loops need the same seam."""
    from ..surface_delivery import SurfaceProvider
    enable_media(coordinator)
    transform = (coordinator.get_capability("web.provider_transform")
                 or coordinator.get_capability("web.media.provider_transform"))
    if not callable(transform):
        cached = []
        def transform(provider):
            if isinstance(provider, SurfaceProvider) and provider.coordinator is coordinator:
                return provider
            found = next((wrapped for source, wrapped in cached if source is provider), None)
            if found is None:
                found = SurfaceProvider(provider, MediaOnlyDelivery(), coordinator=coordinator)
                from ..provider_capture import bind_wrapper, observe
                observe(bind_wrapper, found, provider)
                cached.append((provider, found))
            return found
        coordinator.register_capability("web.media.provider_transform", transform)
    from .session import SelectedProvider
    loop = coordinator.get("orchestrator")
    if isinstance(getattr(loop, "root_provider", None), SelectedProvider):
        loop.root_provider.execution_adapter = transform
    return {name: transform(provider) for name, provider in providers.items()}


async def adopt_messages(coordinator, parent, messages):
    """Only the delegate's already-filtered inherited history is considered."""
    target = coordinator.get_capability("web.media.store")
    source = parent.get_capability("web.media.store")
    if target is None or source is None:
        return copy.deepcopy(messages)
    from amplifier_foundation.session.media import validate_blob_ref
    rows = copy.deepcopy(messages)
    for row in rows:
        envelope = (row.get("metadata") or {}).get(NAMESPACE)
        if envelope is None:
            continue
        if not isinstance(envelope, dict) or set(envelope) != {"refs"} or not isinstance(envelope["refs"], list):
            raise ValueError("Invalid inherited media envelope")
        refs = [validate_blob_ref(ref) for ref in envelope["refs"]]
        foreign = [ref for ref in refs if ref["session_id"] != target.session_id]
        if any(ref["session_id"] != source.session_id for ref in foreign):
            raise ValueError("Inherited media belongs to an unrelated session")
        if foreign:
            adopted = await target.adopt(foreign, source)
            iterator = iter(adopted)
            envelope["refs"] = [next(iterator) if ref in foreign else ref for ref in refs]
    return rows