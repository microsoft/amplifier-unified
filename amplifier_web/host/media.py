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
_REQUEST_ONLY = ContextVar("unified_request_only_images", default=None)


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


def bind_media(session, store):
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
    policy = SourcePolicy(coordinator)
    request_only = RequestOnlyRegistry()
    coordinator.register_capability("web.media.request_only", request_only)
    coordinator.register_capability("computer_use.media_policy", policy.computer)
    coordinator.register_capability("web.media.source_policy", policy)
    coordinator.hooks.register("tool:pre", policy.observe, name="unified-media-source",
                               priority=-100)
    if _durable_media_supported():
        media = store.media(session.session_id, policy_verifier=policy.validate,
                            request_only_verifier=request_only.validate)
    else:
        from amplifier_foundation.session.media import NeutralSessionMediaStore
        media = NeutralSessionMediaStore(session.session_id)
    policy.store = media
    # Module readiness reports describe compatibility, never execution authority.
    # Enable only after the selected loop and actual checkpoint seam are mounted.
    capability = bind_session_media(coordinator, store=media, enabled=False)
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
            request_only.requests.clear()
            projected.clear()
            media.close()
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
    # The composition owner records this only after qualifying the exact
    # post-hook carrier/registration graph. Readiness names alone cannot prove
    # that an unobserved raw/native hook did not replace or redact a result.
    graph = coordinator.get_capability("web.media.graph_qualified") is True
    capability.enabled = bool(graph and loop_ready and _durable_media_supported() and
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


def _live_binding(coordinator, provider, model, options):
    from ..provider_capture import normalized_options
    support = getattr(provider, "get_media_request_support", None)
    value = support(model=model, request_options=options) if callable(support) else None
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise ValueError("Media support must be synchronously verifiable")
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
        self.model = model
        self.canonical = self.canonical_snapshot = None
        self.live_binding = _live_binding(coordinator, provider, model, options)
        install = getattr(prepared, "set_binding_validator", None)
        if callable(install):
            install(self.validate_binding)
        projected = coordinator.get_capability("web.media.projections")
        if projected is not None:
            projected[id(self.request)] = self

    def validate_binding(self):
        if (self.prepared is None or self.source != self.snapshot
                or self.canonical is not None and self.canonical != self.canonical_snapshot
                or self.coordinator.get_capability(NAMESPACE) is not self.capability
                or _provider_owner(self.provider) is not self.owner
                or any(_provider_owner((self.coordinator.get("providers") or {}).get(name))
                       is not self.owner for name in self.mounts)
                or _live_binding(self.coordinator, self.provider, self.model, self.options) != self.live_binding
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
            and callable(getattr(provider, "get_media_request_support", None))):
        return None
    if capability is None or getattr(capability, "version", None) != 1:
        capability = NeutralMedia(coordinator)
    from ..provider_capture import normalized_options
    options = effective_options(request, request_options)
    if normalized_options(options) is None:
        raise ValueError("Prepared media request options are unsupported")
    owner = _provider_owner(provider)
    model = options.get("model") or request.model or getattr(provider, "default_model", None)
    support = getattr(provider, "get_media_request_support", None)
    support = support(model=model, request_options=options) if callable(support) else None
    if inspect.isawaitable(support):
        if inspect.iscoroutine(support):
            support.close()
        raise ValueError("Media support must be synchronously verifiable")
    # Unknown support is explicitly no-vision. Do not infer tool placement from
    # a general vision model tag or change the user's selected account/model.
    if not isinstance(support, dict) or support.get("v") != 1:
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