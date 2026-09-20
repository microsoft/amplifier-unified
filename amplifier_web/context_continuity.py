"""Host persistence for optional derived compaction checkpoints.

The canonical transcript is the existing evidence store. This adapter writes no
second output archive and never runs model work or replays a tool on restore.
"""
import copy
import hashlib
import inspect
import json
from pathlib import Path

from .host.config import write_private


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class ContextContinuity:
    def __init__(self, coordinator, session_id, directory, checkpoint):
        self.coordinator = coordinator
        self.session_id = session_id
        self.path = Path(directory) / "context-checkpoint.json"
        self.checkpoint = checkpoint
        self.loaded = False
        self.saved_digest = None
        self._identity = None
        self.status = {"supported": bool(coordinator.get_capability("context.checkpoint.export")), "status": "pending"}
        coordinator.register_capability("context.checkpoint_identity", lambda: copy.deepcopy(self._identity))
        coordinator.register_capability("context.preserve_evidence", self.preserve)
        coordinator.register_capability("web.continuity.status", self.public)

    def public(self):
        getter = self.coordinator.get_capability("context.checkpoint.status")
        module_status = getter() if getter else {}
        if self.loaded and self.status["supported"] and (not self._identity or not all(self._identity.values())):
            return {"supported": True, "status": "unavailable", "reason": "An explicit provider and model identity is required.", "originalsAvailable": True}
        if self.status.get("status") == "rejected":
            return copy.deepcopy(self.status)
        return {**copy.deepcopy(self.status), **module_status}

    async def refresh_identity(self):
        providers = self.coordinator.get("providers") or {}
        loop = self.coordinator.get("orchestrator")
        provider = (getattr(loop, "root_provider", None) or loop._select_provider(providers)) if providers else None
        if provider is None:
            self._identity = None
            return
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
        defaults = info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})
        name = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
        original = getattr(provider, "original", provider)
        instance = next((key for key, value in providers.items() if value is original), name)
        self._identity = {"provider": instance or name, "model": (defaults or {}).get("model") or (defaults or {}).get("default_model")}

    async def preserve(self, messages):
        await self.refresh_identity()
        # Native storage is already lossless for these messages. Commit it before
        # any request fitter sees and potentially clips a derived copy.
        current = digest(messages)
        if current != self.saved_digest:
            await self.checkpoint()
            self.saved_digest = current
        if not self.loaded:
            self.loaded = True
            restore = self.coordinator.get_capability("context.checkpoint.restore")
            if restore and self.path.exists():
                try:
                    record = json.loads(self.path.read_text())
                    self.status.update(restore(record, self._identity))
                except (ValueError, OSError, TypeError) as exc:
                    self.status.update(status="rejected", reason=str(exc), originalsAvailable=True)
        # References identify exact original content in the canonical transcript.
        # Process tool output can additionally contain operation_output references
        # supplied by its operation journal; do not copy that archive here.
        return [{"kind": "transcript", "sessionId": self.session_id, "messageIndex": index,
                 "sha256": digest(row), "toolCallId": row.get("tool_call_id"), "sourceRevision": current}
                for index, row in enumerate(messages)
                if row.get("role") == "tool" and len(json.dumps(row, ensure_ascii=False)) > 16000]

    def save(self):
        export = self.coordinator.get_capability("context.checkpoint.export")
        if export is None or not self._identity or not all(self._identity.values()):
            return
        record = export(self._identity)
        if record is not None:
            write_private(self.path, json.dumps(record, ensure_ascii=False))
            self.status = {"supported": True, "status": "saved", "sourceRevision": record["sourceRevision"], "originalsAvailable": True}


def install(coordinator, session_id, directory, checkpoint):
    return ContextContinuity(coordinator, session_id, directory, checkpoint)
