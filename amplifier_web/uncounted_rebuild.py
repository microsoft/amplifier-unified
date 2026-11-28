"""Journal only an uncounted cold rebuild; never admit a warm transition."""
import copy

from .context_continuity import ContextRecoveryError, _guarded, _source_digest, digest
from .provider_catalog import require_mounted_binding


def validate(record):
    """Extend the existing journal reader, failing closed on unknown versions."""
    if "uncountedVersion" not in record:
        return
    source = record.get("summarySource", {})
    calls = record.get("calls", [])
    if (type(record["uncountedVersion"]) is not int or record["uncountedVersion"] != 1
            or record.get("kind") != "model-transition"
            or type(source.get("messages")) is not int or source["messages"] < 1
            or source["messages"] > record["sourceRevision"]["messages"]
            or not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64
            or not isinstance(record.get("summaryPolicy"), str) or len(record["summaryPolicy"]) != 64
            or record["completedParts"] != sum(call["state"] == "completed" for call in calls)):
        raise ValueError("Invalid uncounted rebuild record")
    for index, call in enumerate(calls):
        report = call["report"]
        if (report.get("sequence") != index or report.get("method") != "semantic"
                or any(not isinstance(report.get(key), str) or len(report[key]) != 64
                       for key in ("requestSha256", "optionsSha256"))
                or call["state"] == "reserved" and index != len(calls) - 1
                or call["state"] == "completed" and (
                    not isinstance(call.get("text"), str) or not call["text"].strip()
                    or call.get("resultSha256") != digest(call["text"]))):
            raise ValueError("Invalid uncounted summary result")


async def open_rebuild(host, *, provider, summarizer, through, policy):
    """Return request-bound reserve/confirm callbacks, or leave ordinary work alone.

    Cached validated note text is replayed locally through Context's deterministic
    partition. No provider request, canonical input or tool is replayed. The
    original covered prefix stays fixed when a later admitted input adds a tail.
    """
    context = host.coordinator.get("context")
    record = host._recovery_record()
    if (host._transition is None or host._foreign_rejection or context.summary is not None
            or callable(getattr(provider, "request_budget", None)) or provider is not host._provider()):
        return None
    if summarizer is not provider:
        raise ContextRecoveryError("transition_persistence_required")
    if host._pending_busy or host._recovery_busy:
        raise ContextRecoveryError("recovery_unreconciled")
    check, contract = host._fresh_guard(transition=True)
    foreground = host._foreground()
    if not foreground.get("connection"):
        raise ContextRecoveryError("recovery_identity_unavailable")
    require_mounted_binding(host.coordinator, host._identity["provider"], foreground["connection"])
    messages = await _guarded(check, host.coordinator.get_capability("context.history"))
    epoch = await _guarded(check, host._interruption_revision)
    if record is not None and record["state"] != "committed":
        validate(record)
        if record.get("uncountedVersion") != 1:
            raise ContextRecoveryError("recovery_unreconciled")
        host._check_other_recovery_ownership(record)
        if (record["identity"] != host._identity or record["contract"] != contract
                or record["summaryPolicy"] != policy or any(
                    record["foreground"].get(key) != foreground.get(key)
                    for key in ("selection", "selectionRevision", "connection"))):
            raise ContextRecoveryError("recovery_contract_changed")
        source = record["sourceRevision"]
        if len(messages) < source["messages"] or _source_digest(messages[:source["messages"]]) != source["sha256"]:
            raise ContextRecoveryError("recovery_source_changed")
        summary_source = record["summarySource"]
        if _source_digest(messages[:summary_source["messages"]]) != summary_source["sha256"]:
            raise ContextRecoveryError("recovery_source_changed")
    else:
        record = await host._prepare_transition()
        if record is None:
            raise ContextRecoveryError("recovery_source_unavailable")
        if type(through) is not int or not 0 < through <= len(messages):
            raise ContextRecoveryError("recovery_source_changed")
        record.update(uncountedVersion=1, summaryPolicy=policy,
                      summarySource={"messages": through, "sha256": _source_digest(messages[:through])})
    host._check_transition_file(record)
    if any(call["state"] != "completed" for call in record["calls"]):
        raise ContextRecoveryError("recovery_outcome_unknown")
    if through < record["summarySource"]["messages"]:
        raise ContextRecoveryError("recovery_retention_changed")
    record.update(foreground=foreground, interruptionRevision=epoch, state="failed")
    host._write_recovery()
    guard = host._transition_guard(record)
    guard()
    pinned_messages = digest(messages)
    owner = object()
    host._uncounted_owner = owner

    def current():
        guard()
        require_mounted_binding(host.coordinator, record["identity"]["provider"], foreground["connection"])
        if host._uncounted_owner is not owner or host._recovery_record() is not record:
            raise ContextRecoveryError("recovery_unreconciled")
        host._check_transition_file(record)

    async def current_source():
        current()
        latest = await _guarded(current, host.coordinator.get_capability("context.history"))
        if digest(latest) != pinned_messages:
            raise ContextRecoveryError("recovery_source_changed")
        if await _guarded(current, host._interruption_revision) != epoch:
            raise ContextRecoveryError("recovery_interrupted")
        current()

    def persist():
        try:
            host._write_recovery()
        except BaseException as error:
            # A failed save/readback has an unknown effect. Re-read the same
            # durable record next time; never trust an in-memory completion.
            host._journal = None
            if isinstance(error, Exception):
                raise ContextRecoveryError("recovery_save_unconfirmed") from error
            raise
        current()

    async def reserve(report):
        await current_source()
        if (not isinstance(report, dict) or set(report) != {"sequence", "requestSha256", "optionsSha256"}
                or type(report["sequence"]) is not int or report["sequence"] < 0
                or any(not isinstance(report[key], str) or len(report[key]) != 64
                       for key in ("requestSha256", "optionsSha256"))):
            raise ContextRecoveryError("transition_call_invalid")
        sequence = report["sequence"]
        bound = {**report, "method": "semantic", "targetIdentity": copy.deepcopy(record["identity"]),
                 "sourceCheckpointSha256": record["sourceCheckpointSha256"]}
        calls = record["calls"]
        if sequence < len(calls):
            saved = calls[sequence]
            if saved["report"] != bound:
                raise ContextRecoveryError("summary_progress_incompatible")
            if saved["state"] != "completed":
                raise ContextRecoveryError("recovery_outcome_unknown")
            return saved["text"]
        if sequence != len(calls) or any(call["state"] != "completed" for call in calls):
            raise ContextRecoveryError("recovery_outcome_unknown")
        if len(calls) >= record["maxCalls"]:
            raise ContextRecoveryError("transition_budget_exhausted")
        calls.append({"state": "reserved", "report": bound})
        record.update(state="unknown", code="recovery_outcome_unknown")
        persist()  # No await between confirmed reservation and return.
        return None

    async def confirm(report, text):
        await current_source()
        call = record["calls"][-1]
        if (call["state"] != "reserved" or any(call["report"].get(key) != value for key, value in report.items())
                or not isinstance(text, str) or not text.strip()):
            raise ContextRecoveryError("transition_call_unconfirmed")
        call.update(state="completed", text=text, resultSha256=digest(text))
        record["completedParts"] += 1
        record.update(state="failed", code=None)
        persist()

    return {"through": record["summarySource"]["messages"], "reserve": reserve, "confirm": confirm}
