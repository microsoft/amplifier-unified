"""Opt-in binding to the provider-owned native transport; no wire protocol here."""
import copy
import json
import inspect
import time
import uuid
from urllib.parse import urlparse

from .host.config import app_home, write_private
from .session_files import validate_id


class NativeProviderHost:
    def __init__(self, coordinator, loop, directory):
        self.coordinator, self.loop = coordinator, loop
        self.directory = directory
        self.provider = None
        self.identity = None
        self.reason = "Native provider transport is not enabled for this bundle."
        self.state = {"version": 1, "sessionId": coordinator.session_id, "receipts": []}
        self.path = directory / "native-provider-state.json"
        self.checkpoint_path = directory / "native-provider-checkpoint.json"
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved.get("sessionId") == coordinator.session_id:
                self.state = saved
                if self.state.get("outcome") in {"submitting", "running", "pending"}:
                    self.state["outcome"] = "unknown"
                    self.state["reason"] = "Host restarted without a terminal provider receipt; no work was replayed."
                    self.save()
        coordinator.register_capability("web.native_provider", self)

    def save(self):
        write_private(self.path, json.dumps(self.state, ensure_ascii=False))

    def selected_mount(self):
        providers = self.coordinator.get("providers") or {}
        selected = self.loop._select_provider(providers)
        current, visited, link = selected, set(), None
        while current is not None and id(current) not in visited:
            for name, mounted in providers.items():
                if current is mounted:
                    return selected, mounted, name, link
            visited.add(id(current))
            link, current = current, getattr(current, "original", None)
        return selected, None, None, None

    def selected_identity(self):
        selected, original, instance, _ = self.selected_mount()
        info = selected.get_info() if selected else None
        if inspect.isawaitable(info):
            if inspect.iscoroutine(info):
                info.close()
            info = None
        defaults = (info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})) or {}
        return {"instance": instance, "model": defaults.get("model"),
                "selection": copy.deepcopy(getattr(selected, "selection", {}))}

    def status(self):
        current = self.selected_identity()
        selected = self.provider is not None and current == self.identity
        status = self.provider.native_status() if selected else {"supported": False, "reason": self.reason if not self.provider else "The selected provider changed; remount to enable its native transport."}
        return {**status, "enabled": bool(getattr(self.loop, "config", {}).get("native_provider") is True),
                "identity": current, "model": current["model"], "steering": "native" if selected else "request_boundary",
                "receipt": copy.deepcopy({k: v for k, v in self.state.items() if k != "receipts"}),
                "receipts": copy.deepcopy(self.state.get("receipts", [])[-32:]),
                "originalsAvailable": True}

    async def lifecycle(self, kind, data):
        if self.selected_identity() != self.identity:
            raise ValueError("Native provider identity changed during an owned request")
        generation = getattr(self.coordinator.get_capability("live.runtime"), "generation", None) or {}
        if kind == "native.submitting":
            self.state.update(requestId=str(uuid.uuid4()), outcome="submitting", identity=self.identity,
                              generationId=generation.get("id"), updatedAt=time.time())
        elif kind == "native.response.created":
            self.state.update(outcome="running", responseId=data.get("response_id"))
        elif kind == "native.completed":
            self.state.update(outcome="pending" if data.get("pending_steering") else "completed", responseId=data.get("response_id"))
        elif kind == "native.outcome_unknown":
            self.state.update(outcome="unknown", reason=data.get("reason"))
        # Receipts contain identities and phases only, never model input/output,
        # hidden reasoning, credentials, or encrypted compaction payloads.
        allowed = {"input_id", "response_id", "steer_id", "accepted", "execution_replayed", "reason"}
        receipt = {"id": str(uuid.uuid4()), "event": kind, "requestId": self.state.get("requestId"),
                   "time": time.time(), **{k: v for k, v in data.items() if k in allowed}}
        self.state["receipts"] = (self.state.get("receipts", []) + [receipt])[-128:]
        self.state["updatedAt"] = time.time()
        self.save()  # A failed submitting receipt prevents the external send.

    async def compact(self):
        if not self.provider or self.selected_identity() != self.identity:
            raise ValueError(self.status().get("reason", "Native provider unavailable"))
        checkpoint = self.coordinator.get_capability("live.checkpoint")
        if checkpoint:
            await checkpoint()  # Originals are durable before deriving any state.
        canonical = await self.coordinator.get("context").get_messages()
        revision = copy.deepcopy(canonical)
        result = await self.provider.native_compact(canonical=canonical, identity=self.identity)
        if await self.coordinator.get("context").get_messages() != revision or self.selected_identity() != self.identity:
            self.provider._discard_checkpoint("history_changed_during_compaction")
            raise ValueError("History changed during compaction; provider state was discarded")
        record = self.provider.native_export_checkpoint()
        write_private(self.checkpoint_path, json.dumps({"sessionId": self.coordinator.session_id, "record": record}, ensure_ascii=False))
        return {**self.status(), "checkpoint": result["checkpoint"]}


async def install_native(loop, coordinator, providers):
    """Wrap only an explicitly enabled, currently selected supported provider."""
    if coordinator.get_capability("web.native_provider"):
        return coordinator.get("providers") or providers
    sid = coordinator.session_id
    validate_id(sid)
    host = NativeProviderHost(coordinator, loop, app_home() / "sessions" / sid)
    if getattr(loop, "config", {}).get("native_provider") is not True:
        return providers
    try:
        from amplifier_module_provider_openai import OpenAIProvider
        from amplifier_module_provider_openai.native import NativeResponsesProvider
    except ImportError:
        host.reason = "The installed provider does not include its optional native transport."
        return providers
    selected, original, _, link = host.selected_mount()
    identity = host.selected_identity()
    if type(original) is not OpenAIProvider or identity["model"] != "gpt-6-astra":
        host.reason = "Native steering is available only for the selected OpenAI gpt-6-astra model."
        return providers
    endpoint = urlparse(original.base_url or "https://api.openai.com/v1")
    if endpoint.scheme != "https" or endpoint.netloc != "api.openai.com" or endpoint.path.rstrip('/') != '/v1':
        host.reason = "This configured endpoint does not support the native OpenAI transport."
        return providers
    if "complete" in original.__dict__:
        host.reason = "The provider already has an instance wrapper; native transport must be installed before it."
        return providers
    from amplifier_module_loop_live.scope import LIVE_OWNER
    def owned_loop():
        owner = LIVE_OWNER.get()
        return owner if getattr(owner, "coordinator", None) is coordinator else None
    host.identity = identity
    host.provider = NativeResponsesProvider.wrap(original, owner_getter=owned_loop, lifecycle=host.lifecycle)
    if host.state.get("outcome") in {"unknown", "pending"}:
        host.provider.request_uncertain = True
    if host.checkpoint_path.exists():
        try:
            saved = json.loads(host.checkpoint_path.read_text())
            if saved.get("sessionId") != sid:
                raise ValueError("session mismatch")
            host.provider.native_restore_checkpoint(saved["record"],
                canonical=await coordinator.get("context").get_messages(), identity=identity)
        except (OSError, ValueError, KeyError, TypeError):
            host.provider._discard_checkpoint("unavailable")
    await coordinator.mount("providers", host.provider, name=identity["instance"])
    if link is not None:
        link.original = host.provider
    return coordinator.get("providers") or providers
