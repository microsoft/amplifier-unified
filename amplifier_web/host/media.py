"""Narrow root tool-image ownership and private provider projection.

No transcript writer, migration, historical image conversion or child expansion.
Foundation owns originals, final-hook witnessing and primary-checkpoint proof.
The host selects a reviewed cooperative construction and current read policy.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import inspect
import json
import os

NAMESPACE = "experimental.media.v1"
_ISSUED = ContextVar("unified_request_only_images", default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def registration_coordinator(coordinator):
    getter = getattr(coordinator, "get_capability", None)
    return (getter("web.media.registration") if callable(getter) else None) or coordinator


def registration_hooks(coordinator):
    return registration_coordinator(coordinator).hooks


class HostRegistrationView:
    """Keep existing Work/computer wrappers on actual participating mounts."""
    def __init__(self, coordinator, participant):
        object.__setattr__(self, "native", coordinator)
        object.__setattr__(self, "participant", participant)

    def __getattr__(self, name):
        return getattr(self.participant, name)

    def get_capability(self, name):
        value = self.participant.get_capability(name)
        if name != "session.media.tool_post" or value is None:
            return value
        async def tool_post(event, *, hooks=None, coordinator=None):
            # The loop executes with this host's mount-decoration view. It is
            # not a Foundation-issued route token: translate ONLY our exact
            # pair to its actual issuer-owned participant, keeping the real
            # bridge, final-hook processing and witness unchanged.
            state = self.native.get_capability("web.media.construction")
            if (coordinator is not self or hooks is not self.participant.hooks
                    or self.native.get_capability("web.media.registration") is not self
                    or not state or state[0] is not value
                    or self.native.get_capability(name) is not value
                    or value.participant("unified-host") is not self.participant):
                raise ValueError("Unknown host tool-post route")
            return await value(event, hooks=self.participant.hooks, coordinator=self.participant)
        return tool_post

    def __setattr__(self, name, value):
        setattr(self.participant, name, value)

    async def mount(self, kind, value, name=None):
        if kind == "tools":
            guard = self.native.get_capability("web.work_guard_tools")
            if callable(guard):
                value = guard((name or getattr(value, "name", None), value))
            sponsor = self.native.get_capability("web.computer_episodes")
            if sponsor is not None:
                value = sponsor.wrap(name or getattr(value, "name", None), value)
        return await self.participant.mount(kind, value, name=name)

    async def mount_adapter(self, kind, value, *, previous, name):
        return await self.participant.mount_adapter(kind, value, previous=previous, name=name)


def replace_adapter(coordinator, kind, name, previous, wrapped):
    if previous is wrapped:
        return
    if coordinator.get_capability("web.media.registration") is None:
        (coordinator.get(kind) or {})[name] = wrapped
        return
    pending = coordinator.get_capability("web.media.adapters")
    if not any(row[0:2] == (kind, name) and row[2] is previous and row[3] is wrapped for row in pending):
        pending.append((kind, name, previous, wrapped))


async def flush_adapters(coordinator):
    pending = coordinator.get_capability("web.media.adapters") or []
    while pending:
        kind, name, previous, wrapped = pending[0]
        await registration_coordinator(coordinator).mount_adapter(
            kind, wrapped, previous=previous, name=name)
        pending.pop(0)


@contextmanager
def hook_configuration(coordinator):
    state = coordinator.get_capability("web.media.construction")
    if state is None:
        yield
        return
    bridge, graph, selection = state
    cap = coordinator.get_capability(NAMESPACE)
    if bridge.graph_receipt is None:
        cap.enabled = False
        with graph.configure():
            yield
    else:
        # transition admission can refuse while a counted candidate holds its
        # lifetime. Such a refusal must not revoke that unchanged candidate.
        with graph.transition() as successor:
            cap.enabled = False
            coordinator.register_capability("web.media.construction", (bridge, successor, selection))
            yield
        seal_hook_graph(coordinator)


def seal_hook_graph(coordinator):
    state = coordinator.get_capability("web.media.construction")
    if state is None:
        return False
    bridge, graph, selection = state
    cap = coordinator.get_capability(NAMESPACE)
    cap.enabled = False
    if not (selection.accepts_plan(coordinator.config) and selection.accepts_construction(graph)):
        return False
    try:
        receipt = bridge.graph_receipt or graph.seal()
    except ValueError:
        return False
    report = cap.report("unified-host", event="readiness", outcome="inspect")
    loop_ready = any(row.get("role") == "loop" and type(row.get("contract")) is int and row["contract"] == 1
                     and row.get("ready") is True
                     for row in report.get("consumers", {}).values())
    cap.enabled = bool(bridge.validate_receipt(receipt) and loop_ready
                       and callable(coordinator.get_capability("session.durable_checkpoint")))
    return cap.enabled


class _RequestOnlyIssuance:
    """One task's ephemeral preparation, consumable by one active bind.

    Binding transfers its exact blocks to a request association. Exiting without
    binding discards them; retaining this object never permits a later bind.
    Upload input receipts have a separate, deliberately durable lifetime.
    """
    def __init__(self):
        self.task = asyncio.current_task()
        self.blocks = []
        self.open = True
        self.consumed = False

    def consume(self):
        if (not self.open or self.consumed or self.task is not asyncio.current_task()
                or _ISSUED.get() is not self):
            return ()
        self.consumed = True
        blocks, self.blocks = tuple(self.blocks), []
        return blocks

    def close(self):
        self.open = False
        self.blocks.clear()
        self.task = None


@contextmanager
def request_only_scope():
    issued = _RequestOnlyIssuance()
    token = _ISSUED.set(issued)
    try:
        yield issued
    finally:
        issued.close()
        _ISSUED.reset(token)


def record_request_only(message, *, validate):
    issued = _ISSUED.get()
    if (type(issued) is _RequestOnlyIssuance and issued.open and not issued.consumed
            and issued.task is asyncio.current_task()):
        for block in message.content if isinstance(message.content, list) else ():
            if getattr(block, "type", None) == "image":
                issued.blocks.append((block, validate))
    return message


class RequestOnlyRegistry:
    """Exact private request/block issuance; no metadata-only blanket grant."""
    def __init__(self, issuance_path=None):
        self.requests = {}
        self.uploads = {}
        self.issuance_path = issuance_path
        if issuance_path is not None and issuance_path.exists():
            if issuance_path.is_symlink():
                raise ValueError("Upload issuance owner is unavailable")
            value = json.loads(issuance_path.read_text())
            if type(value) is not dict or set(value) != {"v", "inputs"} or value["v"] != 1:
                raise ValueError("Invalid private upload issuance")
            self.uploads = value["inputs"]

    def issue_upload(self, input_id, content):
        if not isinstance(input_id, str) or not input_id:
            raise ValueError("Upload issuance requires its actual admitted input ID")
        entry = {"content": _digest(content), "images": {
            str(index): _digest(block["source"]) for index, block in enumerate(content)
            if isinstance(block, dict) and block.get("type") == "image"}}
        if input_id in self.uploads and self.uploads[input_id] != entry:
            raise ValueError("Admitted upload input identity cannot be rebound")
        self.uploads[input_id] = entry
        if self.issuance_path is not None:
            from .config import write_private
            write_private(self.issuance_path, json.dumps({"v": 1, "inputs": self.uploads}))

    def bind(self, request, issued=None):
        if id(request) in self.requests:
            return False  # A new bind cannot replace an existing association.
        ephemeral = issued.consume() if type(issued) is _RequestOnlyIssuance else ()
        task = asyncio.current_task()
        slots = []
        for i, message in enumerate(request.messages):
            input_id = ((message.metadata or {}).get("amplifier_input") or {}).get("id")
            upload = self.uploads.get(input_id, {}) if message.role == "user" else {}
            if upload and (not isinstance(message.content, list)
                           or upload["content"] != _digest([
                               block.model_dump(exclude_none=True) for block in message.content])):
                upload = {}
            for j, block in enumerate(message.content if isinstance(message.content, list) else ()):
                if getattr(block, "type", None) != "image":
                    continue
                validator = next((validate for original, validate in ephemeral if original is block), None)
                if upload.get("images", {}).get(str(j)) == _digest(block.source):
                    slots.append((i, j, block, _digest(block.source), None, None))
                elif validator is not None:
                    slots.append((i, j, block, _digest(block.source), validator, task))
        if slots:
            self.requests[id(request)] = (request, slots)
        return bool(slots)

    def validate(self, request, *, message_index, block_index, purpose):
        row = self.requests.get(id(request))
        if row is None or row[0] is not request:
            return False
        block = request.messages[message_index].content[block_index]
        return any(i == message_index and j == block_index and original is block
                   and _digest(block.source) == digest
                   and (owner is None or owner is asyncio.current_task() and not owner.done())
                   and (validate is None or validate() is True)
                   for i, j, original, digest, validate, owner in row[1])

    def release(self, request):
        self.requests.pop(id(request), None)


class SourcePolicy:
    """Bind originals to actual read_file calls, then recheck current policy.

    Call arguments come only from the acknowledged canonical assistant/result
    pair. A producer-supplied path/ref marker alone is never a source grant.
    """
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.pending = {}
        self.paths = {}
        self.store = None

    async def observe(self, event, data):
        from amplifier_core import HookResult
        from ..read_policy import media_read_source
        arguments = data.get("tool_input") or {}
        if (data.get("tool_name") == "read_file"
                and isinstance(arguments, dict) and isinstance(arguments.get("file_path"), str)):
            source = media_read_source(self.coordinator, arguments["file_path"])
            if source is not None:
                self.pending[data.get("tool_call_id")] = (arguments["file_path"], source)
        return HookResult()

    def settle(self, canonical):
        from ..read_policy import read_file_call_paths, media_read_source
        paths = read_file_call_paths(canonical, self.coordinator.session_id)
        self.paths = paths
        for row in canonical:
            for ref in ((row.get("metadata") or {}).get(NAMESPACE) or {}).get("refs", []):
                identity = (ref.get("provenance") or {}).get("id")
                path = paths.get(identity)
                source = media_read_source(self.coordinator, path)
                if identity in self.pending and (path, source) == self.pending[identity]:
                    self.store.classify_origin(ref, classification="read-file." + _digest([path, source]))
        for row in canonical:
            if row.get("role") == "tool":
                self.pending.pop(row.get("tool_call_id"), None)

    def validate(self, ref, *, purpose):
        from amplifier_foundation.session.media import MediaRefusal
        from ..read_policy import media_read_source
        origin = ref["provenance"]
        path = self.paths.get(origin["id"])
        # Publication validates staged bytes before a canonical append exists.
        # This is only the actual observed call; hydration still requires the
        # independent committed-checkpoint proof and the settled relation below.
        if (origin["kind"] == "tool" and origin["session_id"] == self.coordinator.session_id
                and origin["id"] in self.pending):
            argument, source = self.pending[origin["id"]]
            return source is not None and media_read_source(self.coordinator, argument) == source
        source = media_read_source(self.coordinator, path)
        if not (origin["kind"] == "tool" and origin["session_id"] == self.coordinator.session_id
                and self.store.get_origin_classification(ref) == "read-file." + _digest([path, source])
                and path is not None and source is not None):
            raise MediaRefusal("Current read_file source policy is unavailable or refused", code="source_policy")
        return True


def bind_media(session, store, *, selection=None):
    """Before initialize. Unknown graphs do not install or enable producers."""
    from .media_assembly import ReviewedRootRecipe, root_participants
    participants = root_participants(session.config)
    if (type(selection) is not ReviewedRootRecipe or not participants
            or not selection.accepts_plan(session.config) or os.name != "posix"):
        return
    try:
        from amplifier_foundation.session.media import bind_session_media
        from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge
    except ImportError:
        return
    coordinator = session.coordinator
    bridge = install_media_hook_bridge(coordinator)
    graph = bridge.begin_participating_graph(session, owner="unified-host", participants=tuple(participants))
    registration = HostRegistrationView(coordinator, graph.participant("unified-host"))
    coordinator.register_capability("web.media.registration", registration)
    coordinator.register_capability("web.media.construction", (bridge, graph, selection))
    coordinator.register_capability("web.media.adapters", [])
    policy = SourcePolicy(coordinator)
    # This receipt is minted ONLY by the existing attachment encoder, not from
    # loaded inline images. It contains hashes/positions, never pixels or paths.
    registry = RequestOnlyRegistry(store.directory(session.session_id) / "media-upload-issuance.json")
    media = store.media(session.session_id, policy_verifier=policy.validate,
                        request_only_verifier=registry.validate)
    policy.store = media
    cap = bind_session_media(coordinator, store=media, enabled=False)
    coordinator.register_capability("web.media.store", media)
    coordinator.register_capability("web.media.source_policy", policy)
    coordinator.register_capability("web.media.request_only", registry)
    registration.hooks.register("tool:pre", policy.observe, priority=-150, name="unified-media-source")
    async def close():
        cap.enabled = False
        media.close()
        registry.requests.clear()
        registry.uploads.clear()
    coordinator.register_cleanup(close)


def acknowledge_media(coordinator, store, session_id, canonical):
    media = coordinator.get_capability("web.media.store")
    if media is None:
        return
    # No extra checkpoint scan for the ordinary unselected/text-only path.
    policy = coordinator.get_capability("web.media.source_policy")
    if any(NAMESPACE in (row.get("metadata") or {}) for row in canonical):
        store.acknowledge_media(session_id, canonical, media)
    policy.settle(canonical)


def has_media(request):
    return any(NAMESPACE in (message.metadata or {}) for message in request.messages)


def effective_options(request, options):
    result = copy.deepcopy(options or {})
    for name in ("model", "reasoning_effort"):
        value = getattr(request, name, None)
        if value is not None:
            result.setdefault(name, value)
    return result


def _support(provider, request, model, options):
    from amplifier_foundation.session.media_projection import validate_media_request_support
    indexed = getattr(provider, "get_media_request_support_for_request", None)
    if indexed is not None:
        if not callable(indexed):
            raise ValueError("Indexed media support must be synchronous")
        snapshot, kwargs = request.model_copy(deep=True), copy.deepcopy(options)
        owner = _support_owner(provider)
        before = snapshot.model_dump_json()
        sources = [block.source for message in snapshot.messages
                   for block in message.content if isinstance(message.content, list)
                   if getattr(block, "type", None) == "image"]
        try:
            value = indexed(request=snapshot, model=model, request_options=kwargs)
            if inspect.isawaitable(value):
                if inspect.iscoroutine(value):
                    value.close()
                raise ValueError("Indexed media support must be synchronous")
            if (snapshot.model_dump_json() != before or kwargs != options
                    or _support_owner(provider) != owner):
                raise ValueError("Indexed media support changed its read-only input")
            return validate_media_request_support(value, request)
        finally:
            for source in sources:
                source.clear()
            snapshot.messages = []
    raise ValueError("Reviewed media requires request-indexed provider support")


def _support_owner(provider):
    """Retain the exact getter endpoint, not just its reproducible return value."""
    getter = getattr(provider, "get_media_request_support_for_request", None)
    return (getattr(getter, "__self__", None), getattr(getter, "__func__", getter))


def _supported_scope(coordinator, provider, request, options):
    from .media_assembly import supported_request
    state = coordinator.get_capability("web.media.construction")
    return state is not None and supported_request(state[2].plan, provider, request, options)


class HostMediaProjection:
    """One private counted candidate; no hydrated data in canonical messages."""
    def __init__(self, coordinator, provider, source, options, prepared, lifetime, support):
        self.coordinator, self.provider, self.source = coordinator, provider, source
        self.options, self.support = copy.deepcopy(options), copy.deepcopy(support)
        self.snapshot = source.model_copy(deep=True)
        self.capability = coordinator.get_capability(NAMESPACE)
        self.prepared, self.request, self.lifetime = prepared, prepared.request, lifetime
        self.model = source.model  # Explicit qualified request, never a default/alias fallback.
        self.canonical = self.canonical_snapshot = None
        from ..provider_capture import provider_chain
        self.chain = provider_chain(provider)
        if not self.chain:
            raise ValueError("Prepared media provider identity is unavailable")
        self.binding = self._binding()
        self.support_owner = _support_owner(provider)
        prepared.set_binding_validator(self.validate_binding)

    def _binding(self):
        from ..provider_capture import normalized_options
        return (normalized_options({"config": getattr(self.provider, "config", {}),
                                    "default_model": getattr(self.provider, "default_model", None)}),
                self.coordinator.get_capability("web.worker_run"))

    def validate_binding(self):
        from ..provider_capture import same_chain
        if (self.prepared is None or self.source != self.snapshot
                or self.canonical is not None and self.canonical != self.canonical_snapshot
                or not same_chain(self.chain, self.provider)
                or self.coordinator.get_capability(NAMESPACE) is not self.capability
                or self.capability.enabled is not True or self._binding() != self.binding
                or not _supported_scope(self.coordinator, self.provider, self.source, self.options)
                or self.support_owner != _support_owner(self.provider)
                or self.support != _support(self.provider, self.snapshot, self.model, self.options)
                or self.lifetime[1]() is not True):
            raise ValueError("Prepared media source/provider/construction changed")
        if self.capability.enabled is not True:
            raise ValueError("Media admission changed during validation")
        return True

    def bind_canonical(self, request):
        self.canonical, self.canonical_snapshot = request, request.model_copy(deep=True)

    def validate(self):
        self.validate_binding()
        self.prepared.validate(request_options=self.options)

    def release(self):
        if self.prepared is None:
            return
        try:
            self.prepared.release()
        finally:
            self.lifetime[0].__exit__(None, None, None)
            registry = self.coordinator.get_capability("web.media.request_only")
            registry.release(self.source)
            self.prepared = self.request = self.source = self.snapshot = None
            self.canonical = self.canonical_snapshot = None
            self.chain = None
            self.support_owner = None


class WarningProjection:
    """An unsupported root stays text usable without reading stored pixels."""
    def __init__(self, request, options, *, coordinator=None):
        from amplifier_core.message_models import TextBlock
        self.coordinator = coordinator
        self.source, self.snapshot = request, request.model_copy(deep=True)
        self.options = effective_options(request, options)
        self.request = request.model_copy(deep=True)
        for message in self.request.messages:
            if NAMESPACE in (message.metadata or {}):
                message.metadata = {key: value for key, value in message.metadata.items() if key != NAMESPACE}
                warning = TextBlock(text="[Image unavailable: supported reviewed root/model configuration is not active; no pixels supplied from storage]")
                if isinstance(message.content, str):
                    message.content = [TextBlock(text=message.content), warning]
                else:
                    message.content.append(warning)

    def bind_canonical(self, request):
        self.canonical, self.canonical_snapshot = request, request.model_copy(deep=True)

    def validate(self):
        if self.source != self.snapshot or self.canonical != self.canonical_snapshot:
            raise ValueError("Reference-only request changed after fitting")

    def release(self):
        registry = self.coordinator.get_capability("web.media.request_only") if self.coordinator is not None else None
        if registry is not None:
            registry.release(self.source)
        self.request = self.source = self.snapshot = self.canonical = self.canonical_snapshot = None


async def prepare_media(coordinator, provider, request, *, request_options=None):
    if coordinator is None or not has_media(request):
        return None  # Preserve the existing inline upload/surface path unchanged.
    cap = coordinator.get_capability(NAMESPACE)
    state = coordinator.get_capability("web.media.construction")
    if (cap is None or state is None or cap.enabled is not True
            or not state[0].validate_receipt(state[0].graph_receipt)):
        if cap is not None:
            cap.enabled = False
        return WarningProjection(request, request_options, coordinator=coordinator)
    options = effective_options(request, request_options)
    if not _supported_scope(coordinator, provider, request, options):
        return WarningProjection(request, request_options, coordinator=coordinator)
    model = request.model  # _supported_scope requires the same explicit portable/wire model.
    support = _support(provider, request, model, options)
    if not support["positions"]:
        # Valid unsupported facts are an initial, unmarked text-only fit.
        # Never hydrate or hand a marked warning to a provider that correctly
        # refuses unsupported private carriers. A positive fitted candidate
        # changing support still fails validate_binding; it cannot downgrade.
        return WarningProjection(request, options, coordinator=coordinator)
    context = state[0].request_scope()
    validate = context.__enter__()
    prepared = None
    try:
        if validate() is not True:
            raise ValueError("Media construction changed")
        prepared = await cap.prepare_request(
            request, provider_identity={"provider": type(provider).__module__,
                "account": str(id(provider)), "media_support": support,
                "generation": str(coordinator.get_capability("web.worker_run") or id(coordinator))},
            model=model, request_options=options, purpose="model",
            execution_scope={"text_only": True} if
                (request.metadata or {}).get("purpose") == "context-compaction" else None)
        projection = HostMediaProjection(coordinator, provider, request, options, prepared,
                                         (context, validate), support)
        projection.validate()
        return projection
    except BaseException:
        if prepared is not None:
            prepared.release()
        context.__exit__(None, None, None)
        coordinator.get_capability("web.media.request_only").release(request)
        raise