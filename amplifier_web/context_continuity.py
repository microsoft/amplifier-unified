"""Host persistence for optional derived compaction checkpoints.

The canonical transcript is the existing evidence store. This adapter writes no
second output archive and never runs model work or replays a tool on restore.
"""
import asyncio
import copy
import hashlib
import inspect
import json
import logging
from pathlib import Path

from .host.config import write_private


_REFUSAL_CODES = {
    "recovery_source_changed", "recovery_evidence_required", "native_checkpoint_invalid",
    "recovery_no_eligible_work", "recovery_retention_changed", "native_measurement_unavailable",
    "recovery_request_changed", "native_no_reduction", "native_input_oversized",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _source_digest(messages):
    """Hash the same canonical array without allocating one history-sized value."""
    if type(messages) is not list:
        return digest(messages)
    state = hashlib.sha256(b"[")
    try:
        for index, row in enumerate(messages):
            if index:
                state.update(b",")
            # Keep the array wrapper while encoding so nested values retain the
            # original serializer depth. Only one row-sized encoding is retained.
            encoded = json.dumps([row], sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False).encode()
            state.update(memoryview(encoded)[1:-1])
    except UnicodeEncodeError:
        # Preserve the original whole-input error position.
        return digest(messages)
    state.update(b"]")
    return state.hexdigest()


_UNSUPPORTED_TRANSITION_GUIDANCE = (
    "This version of Managed Context cannot switch models with this saved checkpoint. "
    "Try the previous model and connection, or update Managed Context in Settings and send again."
)


class ContextRecoveryError(RuntimeError):
    """Fixed, content-free failure at the host recovery boundary."""
    def __init__(self, code):
        self.code = code
        guidance = {
            "transition_intent_required": (
                "Choose an explicit model connection, then send a new ordinary message. "
                "Default drift, schedules, peer/control input and retries do not authorize transition preparation."),
            "recovery_checkpoint_rejected": (
                "The saved checkpoint cannot establish a model transition. Preserve the original history "
                "and use a compatible validated source. Choosing a model does not repair corruption or changed source."),
            "transition_unsupported": _UNSUPPORTED_TRANSITION_GUIDANCE,
        }.get(code, "Inspect the retained recovery receipt; no automatic retry is permitted.")
        super().__init__(f"Context recovery stopped [{code}]. {guidance}")


def _sync(callback):
    value = callback()
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise ContextRecoveryError("recovery_guard_unavailable")
    return value


async def _guarded(guard, callback, /, *args, **kwargs):
    if guard:
        guard()
    try:
        value = callback(*args, **kwargs)
        value = await value if inspect.isawaitable(value) else value
    except BaseException:
        # A stale observer must not replace the original cancellation/failure.
        if guard:
            try:
                guard()
            except BaseException:
                pass
        raise
    if guard:
        guard()
    return value


class ContextContinuity:
    def __init__(self, coordinator, session_id, directory, checkpoint, *, writer_guard=None):
        self.coordinator = coordinator
        self.session_id = session_id
        self.path = Path(directory) / "context-checkpoint.json"
        self.checkpoint = checkpoint
        self.loaded = False
        self.saved_digest = None
        self._identity = None
        self._identity_provider = None
        self.writer_guard = writer_guard
        self.recovery_path = Path(directory) / "context-recovery.json"
        self._journal = None
        self._recovery_busy = False
        self._pending_busy = False
        self._recovery_guard = None
        self._suppress_save = False
        self._accepted_record = None
        self._transition = None
        self._foreign_rejection = None
        self._prepared_identity = None
        self._preserved_identity = None
        self._reconciling = None
        self.status = {"supported": bool(coordinator.get_capability("context.checkpoint.export")), "status": "pending"}
        coordinator.register_capability("context.checkpoint_identity", self.current_identity)
        coordinator.register_capability("context.preserve_evidence", self.preserve)
        coordinator.register_capability("context.persist_checkpoint", self.save)
        coordinator.register_capability("web.continuity.status", self.public)
        coordinator.register_capability("web.continuity.recovery", self)
        coordinator.register_capability("context.recovery.run_pending", self.run_pending)
        coordinator.register_capability("context.transition_policy", lambda: "host-managed" if callable(
            coordinator.get_capability("web.context_transition.foreground")) else None)

    def public(self):
        getter = self.coordinator.get_capability("context.checkpoint.status")
        module_status = getter() if getter else {}
        if self.loaded and self.status["supported"] and (not self._identity or not all(self._identity.values())):
            return {"supported": True, "status": "unavailable", "reason": "An explicit provider and model identity is required.", "originalsAvailable": True}
        if self.status.get("status") in {"rejected", "transition_required", "prepared-originals"}:
            return copy.deepcopy(self.status)
        return {**copy.deepcopy(self.status), **module_status}

    def _provider(self):
        providers = self.coordinator.get("providers") or {}
        loop = self.coordinator.get("orchestrator")
        return (getattr(loop, "root_provider", None) or loop._select_provider(providers)) if providers else None

    def current_identity(self):
        """Current selection, not the identity of the last preservation call.

        Explicit host selections need no provider metadata or asynchronous work.
        Automatic providers resolve lazily in preserve(); never return a former
        provider's cached identity while a replacement is waiting to resolve.
        """
        provider = self._provider()
        from .host.session import SelectedProvider
        if isinstance(provider, SelectedProvider):
            selection = provider.selection
            if selection.get("instance") and selection.get("model"):
                return {"provider": selection["instance"], "model": selection["model"]}
        return copy.deepcopy(self._identity) if provider is self._identity_provider else None

    async def refresh_identity(self):
        providers = self.coordinator.get("providers") or {}
        provider = self._provider()
        if provider is None:
            self._identity = None
            self._identity_provider = None
            return
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
        defaults = info.get("defaults", {}) if isinstance(info, dict) else getattr(info, "defaults", {})
        name = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
        original = getattr(provider, "original", provider)
        instance = next((key for key, value in providers.items() if value is original), name)
        self._identity = {"provider": instance or name, "model": (defaults or {}).get("model") or (defaults or {}).get("default_model")}
        self._identity_provider = provider

    async def preserve(self, messages):
        restore_allowed = self._ordinary_restore_allowed()
        guard = self._recovery_guard
        previous_identity = self._preserved_identity
        await _guarded(guard, self.refresh_identity)
        if previous_identity != self._identity:
            self._prepared_identity = None
        self._preserved_identity = copy.deepcopy(self._identity)
        # Read before checkpoint(): that callback can itself call save(). An
        # unloaded accepted checkpoint must never be replaced by active defaults.
        record, restore, present, read_failed = None, None, False, False
        if not self.loaded and restore_allowed:
            restore = self.coordinator.get_capability("context.checkpoint.restore")
            present = bool(restore and self.path.exists())
            if present:
                try:
                    record = json.loads(self.path.read_text())
                    self._accepted_record = copy.deepcopy(record) if isinstance(record, dict) else None
                except (ValueError, OSError, TypeError) as exc:
                    read_failed = True
                    self.status.update(status="rejected", reason=str(exc), originalsAvailable=True)
        # Classification is passive and fully validates the original record on
        # Context's disposable source-identity view. Never reinterpret a reason
        # string from a rejected import as selection authority.
        accepted = self._accepted_record if restore_allowed else None
        self._foreign_rejection = None
        if isinstance(accepted, dict) and accepted.get("identity") != self._identity:
            classifier = self.coordinator.get_capability("context.checkpoint.transition")
            if callable(classifier):
                classified = classifier(copy.deepcopy(accepted), copy.deepcopy(self._identity))
            else:
                source = accepted.get("identity")
                if isinstance(source, dict) and all(isinstance(source.get(key), str)
                        and source[key].strip() for key in ("provider", "model")):
                    classified = {"status": "rejected", "code": "transition_unsupported",
                                  "reason": _UNSUPPORTED_TRANSITION_GUIDANCE, "originalsAvailable": True}
                else:
                    classified = {"status": "rejected", "code": "transition_source_invalid",
                                  "reason": "The saved checkpoint has an incomplete source identity.",
                                  "originalsAvailable": True}
            self.status = {"supported": self.status["supported"], **classified}
            self._transition = classified if classified.get("status") == "transition_required" else None
            if self._transition is None:
                self._foreign_rejection = ("transition_unsupported" if classified.get("code") == "transition_unsupported"
                                           else "recovery_checkpoint_rejected")
            self._suppress_save = True
        elif accepted is not None and previous_identity != self._identity and self.loaded:
            restore = self.coordinator.get_capability("context.checkpoint.restore")
            if callable(restore):
                restored = restore(copy.deepcopy(accepted), self._identity)
                self.status = {"supported": self.status["supported"], **restored}
                self._transition = None
                self._suppress_save = False
        settled_originals = restore_allowed and self._settled_originals(messages)
        if settled_originals:
            self._prepared_identity = copy.deepcopy(self._identity)
            self._transition = None
            self._suppress_save = False
            self.status.update(status="prepared-originals")
        else:
            self._prepared_identity = None
        # Native storage is already lossless for these messages. Commit it before
        # any request fitter sees and potentially clips a derived copy.
        current = _source_digest(messages)
        if current != self.saved_digest:
            await _guarded(guard, self.checkpoint)
            self.saved_digest = current
        if not self.loaded:
            restore_allowed = self._ordinary_restore_allowed()
            self.loaded = True
            if present and restore_allowed:
                try:
                    if (not read_failed and self._transition is None
                            and self.status.get("status") != "rejected"
                            and self._prepared_identity != self._identity):
                        self.status.update(restore(record, self._identity))
                except (ValueError, OSError, TypeError) as exc:
                    self.status.update(status="rejected", reason=str(exc), originalsAvailable=True)
                if self.coordinator.hooks:
                    try:
                        await _guarded(guard, self.coordinator.hooks.emit, "context:checkpoint_restored", {
                            key: value for key, value in self.status.items()
                            if key in {"status", "reason", "throughMessage", "completedParts", "originalsAvailable"}})
                    except Exception as exc:
                        # Observability cannot invalidate an accepted checkpoint
                        # or turn successful recovery into a conversation fault.
                        logging.getLogger(__name__).warning("Context checkpoint restore observer failed (%s)", type(exc).__name__)
        # References identify exact original content in the canonical transcript.
        # Process tool output can additionally contain operation_output references
        # supplied by its operation journal; do not copy that archive here.
        return [{"kind": "transcript", "sessionId": self.session_id, "messageIndex": index,
                 "sha256": digest(row), "toolCallId": row.get("tool_call_id"), "sourceRevision": current}
                for index, row in enumerate(messages)
                if row.get("role") == "tool" and (
                    (type(row) is dict and type(row.get("content")) is str and len(row["content"]) > 16000)
                    or len(json.dumps(row, ensure_ascii=False)) > 16000)]

    def save(self):
        if not self.loaded or self._recovery_busy:
            return
        if not self._ordinary_restore_allowed():
            return
        if self._transition is not None or self._foreign_rejection:
            return
        if self._suppress_save and self._prepared_identity != self._identity:
            return
        export = self.coordinator.get_capability("context.checkpoint.export")
        if export is None or not self._identity or not all(self._identity.values()):
            return
        writer = self.writer_guard
        if writer is not None:
            self._check_writer()
        record = export(self._identity)
        if record is not None:
            text = json.dumps(record, ensure_ascii=False)
            if self.writer_guard is not writer:
                raise ContextRecoveryError("recovery_writer_unavailable")
            if writer is not None:
                self._check_writer()
            write_private(self.path, text)
            self._accepted_record = copy.deepcopy(record)
            self._prepared_identity = None
            self._suppress_save = False
            self.status = {"supported": True, "status": "progress_saved" if record.get("progress") else "saved", "sourceRevision": record["sourceRevision"], "originalsAvailable": True}

    def _ordinary_restore_allowed(self):
        try:
            self._recovery_record()
        except ContextRecoveryError:
            # A private journal read failure fences derived state, not the
            # canonical transcript. Explicit recovery still sees the error.
            if self._recovery_guard is not None:
                raise
            return False
        if any(row["state"] != "committed" and row.get("candidateSha256")
               and row is not self._reconciling for row in self._journal["attempts"].values()):
            # A stored candidate is not activation/commit.
            if self._recovery_guard is not None:
                raise ContextRecoveryError("recovery_unreconciled")
            return False
        return True

    def _recovery_record(self):
        # One read per mount; ordinary no-intent preparation does not scan history
        # or repeatedly parse private native windows. This is the sole journal.
        if self._journal is None:
            try:
                journal = json.loads(self.recovery_path.read_text())
                if (journal.get("version") != 1 or not isinstance(journal.get("attempts"), dict)
                        or journal.get("current") not in journal["attempts"]):
                    raise ValueError()
                for key, row in journal["attempts"].items():
                    source = row.get("sourceRevision") or {}
                    contract = row.get("contract")
                    candidate = row.get("candidateSha256")
                    if (not isinstance(key, str) or not 0 < len(key) <= 200 or row.get("commandId") != key
                            or row.get("state") not in {"pending", "running", "installing", "committed", "failed", "refused", "unknown"}
                            or type(row.get("interruptionRevision")) is not int or row["interruptionRevision"] < 0
                            or type(row.get("targetBytes")) is not int or row["targetBytes"] < 1
                            or not isinstance(row.get("identity"), dict) or not isinstance(contract, dict)
                            or set(contract) != {"version", "sha256"} or type(contract["version"]) is not int
                            or contract["version"] != 1 or not isinstance(contract["sha256"], str)
                            or len(contract["sha256"]) != 64
                            or any(char not in "0123456789abcdef" for char in contract["sha256"])
                            or ((row.get("state") == "installing" or "candidateSha256" in row)
                                and (not isinstance(candidate, str) or len(candidate) != 64
                                     or any(char not in "0123456789abcdef" for char in candidate)))
                            or type(source.get("messages")) is not int or source["messages"] < 1
                            or not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64
                            or type(row.get("completedParts")) is not int or row["completedParts"] < 0):
                        raise ValueError()
                    if row.get("kind") == "model-transition":
                        calls = row.get("calls")
                        checkpoint = row.get("sourceCheckpoint")
                        retired = row["state"] == "committed"
                        if (type(row.get("maxCalls")) is not int or row["maxCalls"] < 1
                                or not isinstance(calls, list) or len(calls) > row["maxCalls"]
                                or any(not isinstance(call, dict) or call.get("state") not in {"reserved", "completed"}
                                       or not isinstance(call.get("report"), dict) for call in calls)
                                or not isinstance(row.get("foreground"), dict)
                                or not row["foreground"].get("selectionRevision")
                                or not row["foreground"].get("inputId")
                                or not retired and row.get("sourceCheckpointDigest") != digest(checkpoint)
                                or checkpoint is not None and (
                                    not isinstance(checkpoint, dict)
                                    or checkpoint.get("sha256") != digest({k: v for k, v in checkpoint.items() if k != "sha256"}))
                                or any(call["report"].get("targetIdentity") != row["identity"]
                                       or call["report"].get("sourceCheckpointSha256") != row.get("sourceCheckpointSha256")
                                       for call in calls)):
                            raise ValueError()
            except FileNotFoundError:
                journal = {"version": 1, "current": None, "attempts": {}}
            except (ValueError, OSError, TypeError, AttributeError, KeyError):
                self._suppress_save = True
                raise ContextRecoveryError("recovery_journal_unavailable") from None
            self._journal = journal
        current = self._journal["attempts"].get(self._journal["current"])
        if current and current["state"] != "committed" and current.get("candidateSha256"):
            self._suppress_save = True
        return current

    def _check_writer(self):
        if not callable(self.writer_guard):
            raise ContextRecoveryError("recovery_writer_unavailable")
        outcome = _sync(self.writer_guard)
        if outcome is not None and outcome is not True:
            raise ContextRecoveryError("recovery_writer_unavailable")

    def _write_recovery(self):
        self._check_writer()
        text = json.dumps(self._journal, ensure_ascii=False, allow_nan=False)
        write_private(self.recovery_path, text)
        if self.recovery_path.read_text() != text:
            raise ContextRecoveryError("recovery_save_unconfirmed")

    def recovery_status(self):
        return self._recovery_public(self._recovery_record())

    def _recovery_public(self, record):
        result = {"supported": callable(self.coordinator.get_capability("context.recovery.recover")),
                  "state": "none", "attempts": len(self._journal["attempts"])}
        if record:
            state, code = record["state"], record.get("code")
            if state in {"running", "installing"} and not self._recovery_busy:
                state, code = "unknown", "recovery_interrupted"
            result.update(commandId=record["commandId"], state=state, code=code,
                          sourceMessages=record["sourceRevision"]["messages"],
                          completedParts=record.get("completedParts", 0))
            if record.get("kind") == "model-transition":
                result.update(kind="model-transition", outcome=record.get("outcome"),
                              logicalCalls=len(record["calls"]), maxCalls=record["maxCalls"])
            result["verification"] = {key: value for key, value in record.get("verification", {}).items()
                                      if key in {"before", "after", "limit", "throughMessage"} and type(value) is int}
        return result

    def _fresh_guard(self, *, transition=False):
        contract_name = "web.context_transition.contract" if transition else "web.context_recovery.contract"
        cap = self.coordinator.get_capability
        names = ("live.activation", "live.runtime", "web.context_recovery.epoch",
                 contract_name, "context.recovery.recover", "context.history",
                 "web.app_access.bridge")
        bound = {name: cap(name) for name in names}
        gate, runtime = bound["live.activation"], bound["live.runtime"]
        writer = self.writer_guard
        cancellation = getattr(self.coordinator, "cancellation", None)
        if (not callable(writer) or gate is None or runtime is None or cancellation is None
                or not all(callable(bound[name]) for name in names[2:])):
            raise ContextRecoveryError("recovery_guard_unavailable")
        token = gate.current()
        epoch = _sync(bound["web.context_recovery.epoch"])
        def contract():
            value = _sync(bound[contract_name])
            if not isinstance(value, dict) or not value:
                raise ContextRecoveryError("recovery_contract_unavailable")
            return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
        configuration = contract()
        if type(epoch) is not int:
            raise ContextRecoveryError("recovery_guard_unavailable")
        def check():
            gate.check(token)
            current_epoch = _sync(bound["web.context_recovery.epoch"])
            if (gate.current() is not token or self.writer_guard is not writer
                    or any(cap(name) is not value for name, value in bound.items())
                    or getattr(self.coordinator, "cancellation", None) is not cancellation
                    or getattr(cancellation, "is_cancelled", True)
                    or getattr(runtime, "stop_requested", True) or getattr(runtime, "closed", True)
                    or type(current_epoch) is not int or current_epoch != epoch):
                raise ContextRecoveryError("recovery_interrupted")
            if contract() != configuration:
                raise ContextRecoveryError("recovery_contract_changed")
            outcome = _sync(writer)
            if outcome is not None and outcome is not True:
                raise ContextRecoveryError("recovery_writer_unavailable")
        check()
        # Keep the exact comparison only in this invocation. The journal must
        # not duplicate materialized credentials, prompts or tool schemas.
        return check, {"version": 1, "sha256": digest(configuration)}

    async def _interruption_revision(self):
        bridge = self.coordinator.get_capability("web.app_access.bridge")
        if not callable(bridge):
            raise ContextRecoveryError("recovery_stop_unavailable")
        response = await bridge("dispatch", {"action": "coordination.list", "args": {
            "sessionId": self.session_id, "limit": 1, "includeWorkers": False}})
        result = response.get("result") if isinstance(response, dict) else None
        items = result.get("items") if isinstance(result, dict) else None
        if (not isinstance(items, list) or len(items) != 1
                or not isinstance(items[0], dict)
                or items[0].get("target") != {"sessionId": self.session_id}
                or not isinstance(items[0].get("signal"), dict)
                or type(items[0]["signal"].get("interruptionRevision")) is not int
                or items[0]["signal"]["interruptionRevision"] < 0):
            raise ContextRecoveryError("recovery_stop_unavailable")
        return items[0]["signal"]["interruptionRevision"]

    async def request_recovery(self, command_id, interruption_revision, target_bytes=1_500_000):
        if (not isinstance(command_id, str) or not 0 < len(command_id) <= 200
                or type(interruption_revision) is not int or interruption_revision < 0
                or type(target_bytes) is not int or target_bytes < 1):
            raise ContextRecoveryError("recovery_arguments_invalid")
        current = self._recovery_record()
        existing = self._journal["attempts"].get(command_id)
        if existing:
            if (existing["interruptionRevision"], existing["targetBytes"]) != (interruption_revision, target_bytes):
                raise ContextRecoveryError("recovery_command_changed")
            return self._recovery_public(existing)
        if self._foreign_rejection:
            raise ContextRecoveryError(self._foreign_rejection)
        if self._recovery_busy or current and current["state"] in {"pending", "running", "installing", "unknown"}:
            raise ContextRecoveryError("recovery_unreconciled")
        self._recovery_busy = True
        record = None
        try:
            guard, configuration = self._fresh_guard()
            await _guarded(guard, self.refresh_identity)
            if not self._identity or not all(self._identity.values()):
                raise ContextRecoveryError("recovery_identity_unavailable")
            messages = await _guarded(guard, self.coordinator.get_capability("context.history"))
            if not isinstance(messages, list) or not messages:
                raise ContextRecoveryError("recovery_source_unavailable")
            source = {"messages": len(messages), "sha256": digest(messages)}
            if await _guarded(guard, self._interruption_revision) != interruption_revision:
                raise ContextRecoveryError("recovery_interrupted")
            record = {"commandId": command_id, "state": "pending", "code": None,
                      "interruptionRevision": interruption_revision, "targetBytes": target_bytes,
                      "contract": configuration, "identity": copy.deepcopy(self._identity),
                      "sourceRevision": source, "progress": None, "completedParts": 0}
            self._journal["attempts"][command_id] = record
            self._journal["current"] = command_id
            self._write_recovery()
            guard()
            return self._recovery_public(record)
        except BaseException:
            if record is not None:
                # A failed acknowledgement is not permission to arm again.
                record.update(state="unknown", code="recovery_save_unconfirmed")
                try:
                    self._write_recovery()
                except BaseException:
                    pass
            raise
        finally:
            self._recovery_busy = False

    def _foreground(self):
        getter = self.coordinator.get_capability("web.context_transition.foreground")
        value = _sync(getter) if callable(getter) else None
        if (not isinstance(value, dict) or not value.get("generationId") or not value.get("inputId")
                or not value.get("selectionRevision")
                or value.get("selection", {}).get("instance") != (self._identity or {}).get("provider")
                or value.get("selection", {}).get("model") != (self._identity or {}).get("model")):
            raise ContextRecoveryError("transition_intent_required")
        return copy.deepcopy(value)

    def _settled_originals(self, messages):
        """Reuse a completed zero-paid disposition, never rearm its campaign."""
        if self._foreign_rejection:
            return False
        empty = self.coordinator.get_capability("context.checkpoint.prepared_originals")
        selection = self.coordinator.get_capability("web.context_transition.selection")
        if not callable(empty) or not callable(selection) or empty(copy.deepcopy(self._identity)) is not True:
            return False
        current = _sync(selection)
        checkpoint_digest = digest(self._accepted_record)
        try:
            stored = json.loads(self.path.read_text()) if self.path.exists() else None
        except (ValueError, OSError, TypeError):
            return False
        if digest(stored) != checkpoint_digest:
            return False
        for row in self._journal["attempts"].values():
            verification = row.get("verification") or {}
            if (row.get("kind") != "model-transition" or row["state"] != "committed"
                    or row.get("outcome") != "prepared-originals" or row.get("calls")
                    or row.get("progress") or row.get("candidateSha256") or row.get("installedCandidate")
                    or row["identity"] != self._identity
                    or verification.get("status") != "prepared-originals"
                    or verification.get("targetIdentity") != self._identity
                    or row.get("sourceCheckpointDigest") != checkpoint_digest):
                continue
            foreground = row["foreground"]
            if current != {key: foreground.get(key) for key in ("selection", "selectionRevision", "connection")}:
                continue
            source = row["sourceRevision"]
            if (verification.get("sourceRevision") == source and 0 < source["messages"] <= len(messages)
                    and _source_digest(messages[:source["messages"]]) == source["sha256"]):
                return True
        return False

    def _transition_guard(self, record):
        check, contract = self._fresh_guard(transition=True)
        foreground = self._foreground()
        loop = self.coordinator.get("orchestrator")
        selected = getattr(loop, "root_provider", None)
        options = copy.deepcopy(getattr(selected, "selection", None))
        providers = dict(self.coordinator.get("providers") or {})
        if contract != record["contract"]:
            raise ContextRecoveryError("recovery_contract_changed")
        if foreground != record["foreground"]:
            raise ContextRecoveryError("transition_input_changed")
        def guard():
            check()
            mounted = self.coordinator.get("providers") or {}
            if (self._foreground() != foreground or self.coordinator.get("orchestrator") is not loop
                    or getattr(loop, "root_provider", None) is not selected
                    or getattr(selected, "selection", None) != options
                    or set(mounted) != set(providers)
                    or any(mounted[key] is not value for key, value in providers.items())):
                raise ContextRecoveryError("transition_input_changed")
        guard()
        return guard

    def _check_transition_file(self, record):
        """The sole source checkpoint cannot change unnoticed before install."""
        try:
            current = json.loads(self.path.read_text()) if self.path.exists() else None
        except (ValueError, OSError, TypeError):
            raise ContextRecoveryError("recovery_checkpoint_rejected") from None
        expected = record["sourceCheckpointDigest"]
        if (record is self._reconciling and isinstance(current, dict)
                and current.get("sha256") == record.get("candidateSha256")
                and current.get("sha256") == digest({k: v for k, v in current.items() if k != "sha256"})):
            return
        if digest(current) != expected:
            raise ContextRecoveryError("recovery_source_changed")

    async def _rebind_transition(self, record, foreground, contract, guard, *, progress=None):
        """A new admitted input owns its current epoch, never an old Stop token."""
        prior = record["foreground"]
        if (foreground["inputId"] == prior["inputId"]
                or any(foreground.get(key) != prior.get(key)
                       for key in ("selectionRevision", "selection", "connection"))):
            raise ContextRecoveryError("recovery_unreconciled")
        # Bind the exact new foreground/selected provider around the Stop read.
        admission = {**record, "foreground": foreground, "contract": contract}
        current_guard = self._transition_guard(admission)
        guard()
        self._check_transition_file(record)
        epoch = await _guarded(current_guard, self._interruption_revision)
        messages = await _guarded(current_guard, self.coordinator.get_capability("context.history"))
        source = record["sourceRevision"]
        if (len(messages) < source["messages"]
                or _source_digest(messages[:source["messages"]]) != source["sha256"]):
            raise ContextRecoveryError("recovery_source_changed")
        self._check_transition_file(record)
        record.setdefault("preparations", []).append({key: copy.deepcopy(record.get(key)) for key in (
            "state", "code", "foreground", "contract", "interruptionRevision", "completedParts")})
        record.update(state="pending", code=None, foreground=copy.deepcopy(foreground),
                      contract=contract, interruptionRevision=epoch)
        if progress is not None:
            record["progress"] = progress
        self._journal["current"] = record["commandId"]
        self._write_recovery()
        current_guard()

    def _check_other_recovery_ownership(self, own):
        for row in self._journal["attempts"].values():
            if row is own:
                continue
            if (row["state"] in {"pending", "running", "installing", "unknown"}
                    or row["state"] != "committed" and (
                        row.get("candidateSha256") or row.get("kind") != "model-transition"
                        and (row.get("progress") or row.get("completedParts")))):
                raise ContextRecoveryError("recovery_unreconciled")

    async def _reconcile_transition(self, record):
        """Inspect a possibly committed install, never replay its write or calls."""
        self._check_other_recovery_ownership(record)
        foreground = self._foreground()
        guard, contract = self._fresh_guard(transition=True)
        if (contract != record["contract"] or record["identity"] != self._identity
                or foreground["inputId"] == record["foreground"]["inputId"]
                or any(call["state"] != "completed" for call in record["calls"])):
            raise ContextRecoveryError("recovery_unreconciled")
        try:
            candidate = json.loads(self.path.read_text())
        except (ValueError, TypeError, OSError):
            raise ContextRecoveryError("recovery_unreconciled") from None
        if (candidate.get("sha256") != record.get("candidateSha256")
                or candidate.get("sha256") != digest({k: v for k, v in candidate.items() if k != "sha256"})):
            raise ContextRecoveryError("recovery_unreconciled")
        self._accepted_record = copy.deepcopy(record["sourceCheckpoint"])
        self.loaded = True  # Do not lazily activate the uncommitted candidate.
        self._suppress_save = True
        self._reconciling = record
        try:
            await self._rebind_transition(record, foreground, contract, guard, progress=candidate)
        except BaseException:
            self._reconciling = None
            raise

    async def _prepare_transition(self):
        """Create one bounded journal transaction, never a rejected-import escape."""
        foreground = self._foreground()
        guard, contract = self._fresh_guard(transition=True)
        messages = await _guarded(guard, self.coordinator.get_capability("context.history"))
        if not isinstance(messages, list) or not messages:
            raise ContextRecoveryError("recovery_source_unavailable")
        checkpoint = copy.deepcopy(self._accepted_record)
        if checkpoint is None and not foreground.get("originals"):
            return None
        source_digest = digest(checkpoint)
        command = "model-transition-" + digest({
            "session": self.session_id, "selectionRevision": foreground["selectionRevision"],
            "checkpoint": source_digest})
        existing = self._journal["attempts"].get(command)
        # Check other ownership before either creating OR rebinding this row.
        # Its own acknowledged progress has separate, stricter resume guards.
        self._check_other_recovery_ownership(existing)
        if existing:
            if existing["state"] == "committed":
                if existing["contract"] != contract:
                    raise ContextRecoveryError("recovery_contract_changed")
                source = existing["sourceRevision"]
                if digest(messages[:source["messages"]]) != source["sha256"]:
                    raise ContextRecoveryError("recovery_source_changed")
                if existing.get("outcome") == "prepared-originals":
                    # A cold/reverted context must stage and measure originals
                    # again, not pretend its old in-memory view still exists.
                    existing.update(state="pending", foreground=foreground, originalsOnly=True,
                                    sourceCheckpoint=checkpoint)
                    self._journal["current"] = command
                    self._write_recovery()
                    return existing
            # Only acknowledged completed progress can continue on another
            # legitimate foreground input. Never repeat a reserved/unknown call.
            effect_free = (not existing["calls"] and existing.get("progress") is None
                           and not existing.get("candidateSha256") and not existing.get("installedCandidate"))
            if (existing["state"] not in {"failed", "refused"}
                    or existing["calls"] and not existing.get("progress")
                    or any(call["state"] != "completed" for call in existing["calls"])
                    or existing.get("candidateSha256")
                    or existing["foreground"]["inputId"] == foreground["inputId"]
                    or existing["contract"] != contract and not effect_free):
                raise ContextRecoveryError("recovery_unreconciled")
            source = existing["sourceRevision"]
            if digest(messages[:source["messages"]]) != source["sha256"]:
                raise ContextRecoveryError("recovery_source_changed")
            await self._rebind_transition(existing, foreground, contract, guard)
            return existing
        maximum = getattr(self.coordinator.get("context"), "config", {}).get("summary_max_calls", 16)
        if type(maximum) is not int or maximum < 1:
            raise ContextRecoveryError("transition_budget_unavailable")
        epoch = await _guarded(guard, self._interruption_revision)
        record = {"commandId": command, "kind": "model-transition", "state": "pending", "code": None,
            "interruptionRevision": epoch, "targetBytes": 1_500_000, "contract": contract,
            "identity": copy.deepcopy(self._identity), "foreground": foreground,
            "sourceRevision": {"messages": len(messages), "sha256": digest(messages)},
            "sourceCheckpointDigest": source_digest, "sourceCheckpoint": checkpoint,
            "sourceCheckpointSha256": (checkpoint or {}).get("sha256"),
            "progress": None, "completedParts": 0, "calls": [], "maxCalls": maximum}
        self._journal["attempts"][command] = record
        self._journal["current"] = command
        self._write_recovery()
        guard()
        return record

    async def run_pending(self, *, provider, retain_contents=(), count_view):
        # Own the whole transaction before its first await. A second request
        # must never replace a record that a paid callback is already updating.
        if self._pending_busy:
            raise ContextRecoveryError("recovery_unreconciled")
        self._pending_busy = True
        try:
            return await self._run_pending(provider=provider, retain_contents=retain_contents,
                                           count_view=count_view)
        finally:
            self._pending_busy = False

    def _source_continuation(self, record):
        """Observe a safe non-target source unit without restoring it again."""
        if (record.get("kind") != "model-transition" or record.get("candidateSha256")
                or record.get("installedCandidate") or self._recovery_busy
                or self._identity == record["identity"]):
            return False
        source_identity = (record.get("sourceCheckpoint") or {}).get("identity")
        canonical_source = (record.get("sourceCheckpoint") is None
                            and record.get("sourceCheckpointSha256") is None
                            and record.get("sourceCheckpointDigest") == digest(None))
        if source_identity != self._identity and not canonical_source:
            return False
        accepted = self._accepted_record
        try:
            current = json.loads(self.path.read_text()) if self.path.exists() else None
        except (ValueError, OSError, TypeError):
            return False
        if digest(current) != digest(accepted):
            return False
        if isinstance(accepted, dict) and accepted.get("identity") == self._identity:
            classify = self.coordinator.get_capability("context.checkpoint.transition")
            return bool(callable(classify) and classify(
                copy.deepcopy(accepted), copy.deepcopy(self._identity)).get("status") == "compatible")
        empty = self.coordinator.get_capability("context.checkpoint.prepared_originals")
        return bool(canonical_source and accepted is None and callable(empty)
                    and empty(copy.deepcopy(self._identity)) is True)

    async def _run_pending(self, *, provider, retain_contents=(), count_view):
        if self._recovery_busy:
            raise ContextRecoveryError("recovery_unreconciled")
        try:
            record = self._recovery_record()
        except ContextRecoveryError as exc:
            if exc.code != "recovery_journal_unavailable":
                raise
            return None  # Ordinary fitting uses canonical history, never this journal.
        historical_recovery = (record is not None and record.get("kind") != "model-transition"
                               and record["state"] not in {"pending", "committed"})
        policy = self.coordinator.get_capability("context.transition_policy")
        if ((record is None or record["state"] == "committed")
                and (not callable(policy) or _sync(policy) != "host-managed")):
            # Mounted Context wrappers also serve finite/delegated consumers.
            # Without actual host participation they keep Context's ordinary
            # compatibility path, not an impossible root-Send admission.
            return None
        transition = record is not None and record.get("kind") == "model-transition"
        if transition and record["state"] != "committed" and record.get("candidateSha256"):
            await self.refresh_identity()
            await self._reconcile_transition(record)
        if record is None or record["state"] == "committed" or transition or historical_recovery:
            previous_identity = copy.deepcopy(self._identity)
            await self.refresh_identity()
            if historical_recovery and not self._ordinary_restore_allowed():
                return None  # No derived activation or spending; ordinary originals remain usable.
            checkpointless = (self._accepted_record is None and
                self._prepared_identity != self._identity and
                self.coordinator.get_capability("web.context_transition.originals") is True)
            if (self.loaded and previous_identity == self._identity and self._transition is None
                    and not checkpointless
                    and (record is None or record["state"] == "committed" or historical_recovery)):
                if self._accepted_record is None or self._accepted_record.get("identity") == self._identity:
                    return None  # Ordinary preparation will preserve once, as before.
            messages = await self.coordinator.get_capability("context.history")()
            await self.preserve(messages)
            if self._foreign_rejection:
                raise ContextRecoveryError(self._foreign_rejection)
            if transition and self._source_continuation(record):
                return None  # Passive validation never resets source failure/progress.
            checkpointless = (self._accepted_record is None and
                self._prepared_identity != self._identity and
                self.coordinator.get_capability("web.context_transition.originals") is True)
            if record is not None and record is self._reconciling:
                pass  # This exact digest needs measured activation, not rearming.
            elif self._transition is not None or checkpointless:
                try:
                    record = await self._prepare_transition()
                except ContextRecoveryError as exc:
                    # Checkpointless ordinary conversations remain ordinary.
                    # A validated cross-identity checkpoint needs explicit intent.
                    if self._transition is None and exc.code == "transition_intent_required":
                        return None
                    raise
                transition = record is not None and record.get("kind") == "model-transition"
        if (record is None or record["state"] == "committed"
                or historical_recovery and record.get("kind") != "model-transition"):
            # Historical explicit recovery is inert, but cannot bypass detecting
            # a separate selected-model transition or its global paid/install fences.
            return None
        if self._recovery_busy or record["state"] != "pending":
            raise ContextRecoveryError("recovery_unreconciled")
        self._recovery_busy = True
        entered = False
        compaction_error = ()
        retired_payload = None
        try:
            record.update(state="running", code=None)
            self._write_recovery()  # Consume durably before any provider callback.
            guard, configuration = self._fresh_guard(transition=transition)
            if transition:
                guard = self._transition_guard(record)
            self._recovery_guard = guard
            if configuration != record["contract"]:
                raise ContextRecoveryError("recovery_contract_changed")
            await _guarded(guard, self.refresh_identity)
            if self._identity != record["identity"]:
                raise ContextRecoveryError("recovery_identity_changed")
            async def check_stop():
                if await _guarded(guard, self._interruption_revision) != record["interruptionRevision"]:
                    raise ContextRecoveryError("recovery_interrupted")
            await check_stop()
            messages = await _guarded(guard, self.coordinator.get_capability("context.history"))
            source = record["sourceRevision"]
            if len(messages) < source["messages"] or digest(messages[:source["messages"]]) != source["sha256"]:
                raise ContextRecoveryError("recovery_source_changed")
            # Restore before the module snapshots active derived state. Its later
            # preserve callback must not perform a lazy restore inside that fence.
            await _guarded(guard, self.preserve, messages)
            if self.status.get("status") == "rejected":
                raise ContextRecoveryError(self._foreign_rejection or "recovery_checkpoint_rejected")

            def admit_call(report):
                guard()
                if record.get("originalsOnly"):
                    raise ContextRecoveryError("transition_budget_exhausted")
                self._check_transition_file(record)
                calls = record["calls"]
                if (len(calls) >= record["maxCalls"]
                        or any(call["state"] != "completed" for call in calls)):
                    raise ContextRecoveryError("transition_budget_exhausted")
                if (not isinstance(report, dict) or report.get("targetIdentity") != record["identity"]
                        or report.get("sourceCheckpointSha256") != record["sourceCheckpointSha256"]
                        or report.get("method") not in {"native", "semantic"}
                        or type(report.get("maxOutputTokens")) is not int or report["maxOutputTokens"] < 1
                        or not isinstance(report.get("requestSha256"), str) or len(report["requestSha256"]) != 64):
                    raise ContextRecoveryError("transition_call_invalid")
                calls.append({"state": "reserved", "report": copy.deepcopy(report)})
                self._write_recovery()  # An uncertain reservation is never replayed.
                guard()
                return True

            def save_progress(progress, _metadata):
                guard()
                if transition:
                    calls = record["calls"]
                    if (not calls or calls[-1]["state"] != "reserved"
                            or _metadata.get("disposition") != "completed"
                            or _metadata.get("call") != calls[-1]["report"]):
                        raise ContextRecoveryError("transition_call_unconfirmed")
                record["progress"] = copy.deepcopy(progress)
                record["completedParts"] += 1
                if transition:
                    calls[-1]["state"] = "completed"
                try:
                    self._write_recovery()
                except BaseException:
                    if transition:
                        calls[-1]["state"] = "reserved"
                    raise  # In-memory intent is not a persistence acknowledgement.
                guard()

            async def install_candidate(candidate, verification):
                await check_stop()
                if transition:
                    self._check_transition_file(record)
                    latest = await _guarded(guard, self.coordinator.get_capability("context.history"))
                    if digest(latest) != digest(messages):
                        raise ContextRecoveryError("recovery_source_changed")
                sha = candidate.get("sha256")
                if (not isinstance(sha, str) or sha != digest({key: value for key, value in candidate.items() if key != "sha256"})
                        or verification.get("sha256") != sha):
                    raise ContextRecoveryError("recovery_candidate_invalid")
                guard()
                self._suppress_save = True
                if transition and record is self._reconciling:
                    if sha != record["candidateSha256"]:
                        raise ContextRecoveryError("recovery_candidate_invalid")
                    record["installedCandidate"] = sha
                    self._write_recovery()
                    guard()
                    return {"sha256": sha}  # Confirmed readback, no second write.
                record.update(state="installing", candidateSha256=sha, verification=copy.deepcopy(verification))
                self._write_recovery()  # A crash from here needs reconciliation.
                guard()
                write_private(self.path, json.dumps(candidate, ensure_ascii=False, allow_nan=False))
                if json.loads(self.path.read_text()) != candidate:
                    raise ContextRecoveryError("recovery_install_unconfirmed")
                guard()
                record["installedCandidate"] = sha
                self._write_recovery()
                guard()
                return {"sha256": sha}

            # This optional dependency is needed only for an explicitly armed
            # operation. Ordinary continuity never imports the recovery module.
            from amplifier_module_context_managed.errors import CompactionError
            compaction_error = CompactionError
            entered = True
            async def selected_count(rows):
                envelope = await _guarded(guard, count_view, rows)
                from .host.session import SelectedProvider
                selected = getattr(self.coordinator.get("orchestrator"), "root_provider", None)
                if (provider is selected and isinstance(selected, SelectedProvider)
                        and selected.selection.get("instance") == self._identity["provider"]
                        and selected.selection.get("model") == self._identity["model"]):
                    # Bind the provider that assembled and measured this exact
                    # request. Preserve dispatch identity for Surface refit/cache.
                    envelope = {**envelope, "effective_identity": copy.deepcopy(self._identity)}
                return envelope
            result = await _guarded(guard, self.coordinator.get_capability("context.recovery.recover"),
                provider=provider, count_view=selected_count if transition else count_view,
                identity=copy.deepcopy(self._identity),
                save_progress=save_progress, install=install_candidate, guard=guard,
                retain_contents=retain_contents, target_bytes=record["targetBytes"],
                progress=record["progress"], source_revision=copy.deepcopy(source),
                **({"source_checkpoint": copy.deepcopy(record["sourceCheckpoint"]),
                    "transition_source": ({"kind": "canonical-originals", "sourceRevision": copy.deepcopy(source)}
                                          if record["sourceCheckpoint"] is None else None),
                    **({"installed_candidate_sha256": record["candidateSha256"]}
                       if record is self._reconciling else {}),
                    "admit_call": admit_call, "max_calls": (0 if record.get("originalsOnly") else
                                                          record["maxCalls"] - len(record["calls"]))}
                   if transition else {}))
            await check_stop()
            verification = result.get("recovery", {})
            fitted_originals = transition and result.get("outcome") == "transition_fitted"
            if fitted_originals:
                if (verification.get("status") != "prepared-originals"
                        or verification.get("targetIdentity") != record["identity"]
                        or verification.get("sourceRevision") != source
                        or record["calls"] or record.get("candidateSha256")):
                    raise ContextRecoveryError("recovery_install_unconfirmed")
                self._check_transition_file(record)
                self._prepared_identity = copy.deepcopy(self._identity)
                record.update(outcome="prepared-originals", verification=copy.deepcopy(verification))
                self.status.update(status="prepared-originals")
            elif (verification.get("status") != "installed" or not record.get("installedCandidate")
                    or verification.get("sha256") != record["installedCandidate"]):
                raise ContextRecoveryError("recovery_install_unconfirmed")
            else:
                self._accepted_record = json.loads(self.path.read_text())
                self._prepared_identity = None
                self.status = {"supported": True, "status": "saved", "originalsAvailable": True}
                record["outcome"] = "checkpoint-installed"
            record.update(state="committed", code=None)
            if transition:
                # Keep transaction evidence, not a hidden per-model checkpoint
                # bank. The newly accepted checkpoint is now the sole source.
                retired_payload = (record.pop("sourceCheckpoint", None), record["progress"])
                record["progress"] = None
            self._write_recovery()
            guard()
            self._suppress_save = False
            self._transition = None
            return result
        except BaseException as exc:
            if retired_payload is not None:
                record["sourceCheckpoint"], record["progress"] = retired_payload
            # Provider/host effects cannot be rolled back. In particular, never
            # overwrite a candidate after storage succeeded but activation failed.
            state = "unknown" if entered or self._suppress_save else "failed"
            code = ("recovery_outcome_unknown" if entered else
                    exc.code if isinstance(exc, ContextRecoveryError) else "recovery_preparation_failed")
            if isinstance(exc, asyncio.CancelledError) and not entered:
                code = "recovery_interrupted"
            if (entered and not self._suppress_save and isinstance(exc, compaction_error)
                    and exc.code in _REFUSAL_CODES):
                state, code = "refused", exc.code
            if transition and not record.get("candidateSha256"):
                uncertain = any(call["state"] == "reserved" for call in record["calls"])
                state = "unknown" if uncertain else "refused"
                code = (exc.code if isinstance(exc, ContextRecoveryError) or isinstance(exc, compaction_error)
                        else "recovery_outcome_unknown" if uncertain else "recovery_preparation_failed")
            record.update(state=state, code=code)
            try:
                self._write_recovery()
            except BaseException:
                pass  # The prior running/installing receipt remains non-retryable.
            raise
        finally:
            self._recovery_guard = None
            self._recovery_busy = False
            self._reconciling = None


def install(coordinator, session_id, directory, checkpoint, *, writer_guard=None):
    return ContextContinuity(coordinator, session_id, directory, checkpoint, writer_guard=writer_guard)
