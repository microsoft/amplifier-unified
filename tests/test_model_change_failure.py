"""Model-picker changes with the real context/loop and an offline provider."""
import asyncio
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.runtime_worker import Worker
from amplifier_web.service import AppService
from amplifier_web.management import Management
from test_context_continuity import ordinary_recovery_case, RecoveryWire, append_recovery_evidence


def source_codes():
    """Pin guidance coverage to the real dependency and host producers."""
    import amplifier_module_context_managed
    import amplifier_web.context_continuity
    import amplifier_web.portable_handoff
    sources = [*Path(amplifier_module_context_managed.__file__).parent.glob("*.py"),
               Path(amplifier_web.context_continuity.__file__),
               Path(amplifier_web.portable_handoff.__file__)]
    result = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"CompactionError", "ContextRecoveryError"}
                    and node.args and isinstance(node.args[0], ast.Constant)):
                result.add(node.args[0].value)
    # Indirect/native and journal dispositions are fixed identifiers too.
    result.update({"transition_unsupported", "recovery_outcome_unknown", "recovery_preparation_failed",
                   "disabled", "request_context_unavailable", "invalid_native_contract",
                   "authoritative_measurement_unavailable", "native_compaction_failed"})
    return sorted(result)


@pytest.mark.parametrize("code", source_codes())
def test_every_context_code_has_fixed_guidance(code):
    from amplifier_web.context_failures import UNKNOWN_CONTEXT_MESSAGE
    from amplifier_web.session_health import exception_details, generation_failure
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.context_continuity import ContextRecoveryError
    detail = generation_failure({"error_category": "context_compaction", "error_code": code,
        "error_message": "PRIVATE transcript, path and credential"})
    assert detail["code"] == code
    assert detail["summary"] != UNKNOWN_CONTEXT_MESSAGE[0]
    assert detail["summary"] and detail["guidance"]
    for error in (CompactionError(code, "PRIVATE"), ContextRecoveryError(code)):
        error.args = ("PRIVATE",)
        actual = exception_details(error)
        assert actual["code"] == code
        assert actual["summary"] == (
            "Conversation preparation can continue from saved progress."
            if code == "summary_work_paused" and type(error) is ContextRecoveryError else detail["summary"])
        assert "PRIVATE" not in json.dumps(actual)


@pytest.mark.parametrize("code", [None, "", [], {}, 1, True, "bad-code", "bad code",
                                  "Upper", "é", "a0", "a\n", "a" * 81])
def test_context_code_rejects_payloads_at_each_boundary(code):
    from amplifier_web.context_failures import context_code
    from amplifier_web.session_health import generation_failure, exception_details
    from amplifier_web.support_diagnostics import public_exception_chain, event_detail
    from amplifier_module_context_managed.errors import CompactionError
    assert context_code(code) is None
    detail = generation_failure({"error_category": "context_compaction", "error_code": code})
    assert "code" not in detail
    error = CompactionError("valid_code", "PRIVATE")
    error.code = code
    assert "code" not in exception_details(error)
    assert public_exception_chain([{"errorType": "CompactionError", "compactionCode": code}]) == [
        {"errorType": "CompactionError", "frames": []}]
    assert "error_code" not in event_detail({"error_code": code})


def test_future_context_code_is_retained_without_exception_text():
    from amplifier_web.session_health import generation_failure
    from amplifier_web.support_diagnostics import exception_chain, public_exception_chain, event_detail
    from amplifier_module_context_managed.errors import CompactionError
    code = "future_context_code"
    frames = public_exception_chain(exception_chain(CompactionError(code, "PRIVATE")))
    assert frames[0]["compactionCode"] == code
    assert "PRIVATE" not in json.dumps(frames)
    detail = generation_failure({"error_category": "context_compaction", "error_code": code,
                                 "error_message": "PRIVATE"})
    assert detail["code"] == code and "diagnostics" in detail["guidance"]
    assert event_detail({"error_code": code})["error_code"] == code


def test_compaction_class_is_certified_not_its_name_or_older_cause():
    from amplifier_web.support_diagnostics import exception_chain, public_exception_chain, terminal_compaction_code
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_web.session_health import exception_details
    code = "model_transition_required"
    error = CompactionError(code, "PRIVATE")
    forged = type("CompactionError", (RuntimeError,), {"code": code})("PRIVATE")
    subclass = type("CompactionError", (CompactionError,), {})(code, "PRIVATE")
    wrapped = RuntimeError("Unrelated final error")
    wrapped.__cause__ = error
    for invalid in (forged, subclass, wrapped):
        assert terminal_compaction_code(public_exception_chain(exception_chain(invalid))) is None
        assert "code" not in exception_details(invalid)
    assert public_exception_chain([{"errorType": "Other", "compactionCode": code}]) == [
        {"errorType": "Other", "frames": []}]


def captured_failure():
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
    from amplifier_web.support_diagnostics import exception_chain
    from amplifier_web.session_health import generation_failure
    error = CompactionError("model_transition_required", "PRIVATE")
    native = turn_failure(error)
    wrapper = ManagerTurnError(native)
    wrapper.__cause__ = error
    terminal = {**native, "event": "generation.failed", "generation_id": "g", "input_ids": ["i"]}
    return {"id": "root", "status": "error", "generations": [terminal],
            "failure": {**generation_failure(native), "generationId": "g", "inputIds": ["i"]}}, {
        "generationId": "g", "inputIds": ["i"], "errorType": "CompactionError",
        "exceptionChain": exception_chain(wrapper)}


@pytest.mark.parametrize("change", ["none", "generation", "input", "type", "stage", "code", "stop", "older_cause",
                                  "event_category"])
def test_only_matching_terminal_can_explain_a_dropped_compaction_code(change):
    from amplifier_web.session_health import terminal_exception_failure
    session, capture = captured_failure()
    if change == "generation":
        capture["generationId"] = "other"
    elif change == "input":
        capture["inputIds"] = ["other"]
    elif change == "type":
        capture["errorType"] = "RuntimeError"
    elif change == "stage":
        session["failure"]["stage"] = "provider_request"
    elif change == "code":
        session["failure"]["code"] = "summary_failed"
    elif change == "stop":
        session["status"] = "stopped"
    elif change == "older_cause":
        capture["exceptionChain"].insert(1, {"errorType": "RuntimeError", "frames": []})
    elif change == "event_category":
        session["generations"][0]["error_category"] = "unknown"
    before = copy.deepcopy(session)
    detail = terminal_exception_failure(session, capture)
    assert bool(detail and detail.get("code") == "model_transition_required") is (change == "none")
    if change == "event_category":
        assert detail == {**before["failure"], "exceptionChain": capture["exceptionChain"]}
    assert session == before  # Native terminal facts are never rewritten.
    assert "PRIVATE" not in json.dumps(detail)


def test_host_and_loop_pause_keep_their_distinct_full_payloads():
    from amplifier_web.session_health import exception_details, generation_failure, terminal_exception_failure
    from amplifier_web.context_continuity import ContextRecoveryError
    from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
    from amplifier_web.support_diagnostics import exception_chain
    loop = {
        "category": "context_compaction", "code": "summary_work_paused",
        "errorType": "CompactionError", "stage": "context_preparation",
        "summary": "Conversation preparation reached its local call allowance.",
        "guidance": "Completed summary progress is saved. Further preparation requires separate admission; it was not automatically retried. Original history and the checkpoint are preserved.",
        "effects": "not_rolled_back", "replayed": False, "retryable": False}
    assert generation_failure({"error_category": "context_compaction", "error_type": "CompactionError",
                               "error_code": "summary_work_paused"}) == loop
    error = ContextRecoveryError("summary_work_paused")
    host = {**loop, "errorType": "ContextRecoveryError",
        "summary": "Conversation preparation can continue from saved progress.",
        "guidance": "Review preparation to approve calls for your next message. One earlier call remains unconfirmed and counts against the original allowance."}
    assert exception_details(error) == {**host, "exceptionChain": [{
        "errorType": "ContextRecoveryError", "frames": [], "contextRecoveryCode": "summary_work_paused"}]}
    native = turn_failure(error)
    wrapper = ManagerTurnError(native)
    wrapper.__cause__ = error
    terminal = {**native, "event": "generation.failed", "generation_id": "g", "input_ids": ["i"]}
    current = {**generation_failure(native), "generationId": "g", "inputIds": ["i"]}
    capture = {"generationId": "g", "inputIds": ["i"], "errorType": "RuntimeError",
               "exceptionChain": exception_chain(wrapper)}
    assert terminal_exception_failure({"id": "root", "status": "error", "generations": [terminal],
                                      "failure": current}, capture) == {
        **current, **host, "errorType": "RuntimeError",
        "exceptionChain": capture["exceptionChain"],
        "nativeFailure": {key: current[key] for key in ("category", "errorType", "stage", "retryable")}}


def test_other_old_host_payloads_stay_unchanged():
    from amplifier_web.session_health import _context_recovery_details
    # These literals are the pre-PR host meanings, not the loop's projection.
    expected = {
        "transition_unsupported": ("provider_selection",
            "Managed Context needs an update to switch models in this chat.",
            "Select the previous model and connection, or update Managed Context in Settings and send again."),
        "transition_intent_required": ("provider_selection", "Choose a model and connection for this chat.",
            "Select the connection and model you want to use, then send a new message."),
        "recovery_checkpoint_rejected": ("persistence",
            "The saved checkpoint could not be validated for this model change.",
            "Keep the original chat history and restore a compatible, validated checkpoint before continuing."),
        "recovery_unreconciled": ("context_compaction", "Earlier conversation preparation could not be resumed.",
            "This preparation step stopped before a model request. Copy the chat diagnostics from Details & reporting.")}
    for code, (category, summary, guidance) in expected.items():
        assert _context_recovery_details(code) == {
            "category": category, "code": code, "stage": "context_preparation",
            "summary": summary, "guidance": guidance, "retryable": False,
            "effects": "not_rolled_back", "replayed": False}


def test_new_notices_use_real_places_and_do_not_blame_only_the_source():
    from amplifier_web.context_failures import CONTEXT_FAILURE_MESSAGES
    summary, guidance = CONTEXT_FAILURE_MESSAGES["transition_source_invalid"]
    assert summary == "Amplifier could not validate the history needed for this model change."
    assert guidance == ("Keep the original chat. Copy its diagnostics from Details & reporting and report this problem before trying the model change again.")
    old_codes = {"transition_unsupported", "transition_intent_required",
                 "recovery_checkpoint_rejected", "recovery_unreconciled"}
    for code, texts in CONTEXT_FAILURE_MESSAGES.items():
        if code not in old_codes:
            for word in ("Managed Context", "native compaction", "admission", "allowance", "contract"):
                assert word not in " ".join(texts), (code, word)
    assert CONTEXT_FAILURE_MESSAGES["disabled"] == (
        "This chat is set not to use the connection's own way to shorten history.",
        "Amplifier did not use another way. Copy the chat diagnostics from Details & reporting and report this problem.")
    # Sending again runs the history preparation again on a new worker. The
    # notice must say that it takes time and model calls, and must not say
    # that sending again cannot help (an experiment showed that it can).
    assert CONTEXT_FAILURE_MESSAGES["model_transition_required"] == (
        "This chat could not prepare its saved history for the model you chose.",
        "Send your message again. Amplifier then prepares this chat's history for the new model. "
        "This can take a while and uses extra model calls. If it fails again, select the previous model "
        "and connection, then send a new message. Then copy the chat diagnostics from Details & reporting "
        "and report this problem.")
    # The context module refuses this step at both of its raise sites; nothing is paused.
    assert CONTEXT_FAILURE_MESSAGES["ordinary_compaction_deferred"] == (
        "Amplifier did not start new summary work for this message. Saved work was kept.",
        "Copy the chat diagnostics from Details & reporting and report this problem.")


@pytest.mark.parametrize("mismatch", [False, True])
async def test_worker_certifies_code_snapshot_and_rejects_wrapper_code_mismatch(monkeypatch, mismatch):
    from amplifier_module_context_managed.errors import CompactionError
    from amplifier_module_loop_live.failures import ManagerTurnError, turn_failure
    publications = []
    monkeypatch.setattr("amplifier_web.runtime_worker.publish", publications.append)
    worker = Worker()
    generation = {"id": "g", "input_ids": ["i"]}
    worker.runtime = SimpleNamespace(session_id="root", generation=generation, stop_requested=False)
    error = CompactionError("summary_failed", "PRIVATE")
    failure = turn_failure(error)
    assert failure["error_code"] == "summary_failed"  # Forwarded by the installed loop.
    class Loop:
        async def execute(self):
            worker.observe({"type": "generation.failed", "session_id": "root",
                            "generation_id": "g", "input_ids": ["i"], **failure})
            if not mismatch:
                assert worker.failed_generation["error_code"] == "summary_failed"
            worker.runtime.generation = None
            wrapper = ManagerTurnError({**failure, **({"error_code": "native_failed"} if mismatch else {})})
            raise wrapper from error
    loop = Loop()
    worker.session = SimpleNamespace(session_id="root", coordinator=SimpleNamespace(
        get=lambda name: loop if name == "orchestrator" else None, get_capability=lambda _: None))
    worker.capture_manager_failures()
    with pytest.raises(ManagerTurnError):
        await loop.execute()
    captures = [row for row in publications if row.get("terminalFailure")]
    assert len(captures) == (0 if mismatch else 1)
    if captures:
        assert captures[0]["terminalFailure"]["exceptionChain"][1]["compactionCode"] == "summary_failed"


async def test_host_pause_can_offer_saved_progress_before_the_call_limit(tmp_path):
    from test_context_continuity import target_transition_fixture
    from amplifier_web.context_continuity import ContextRecoveryError
    from amplifier_web.session_health import exception_details
    case = await target_transition_fixture(tmp_path, research_parts=9)
    case.coordinator.context.config["summary_max_calls"] = 4
    case.coordinator.register_capability("context.recovery.additional_call_allowance", lambda: 2)
    case.coordinator.register_capability("web.provider_catalog", {
        "connectionKeys": {"fixture": "fixture-connection"}})
    compact = case.raw.compact_context
    async def interrupt(request):
        if case.raw.compactions:
            raise OSError("Synthetic unknown call")
        return await compact(request)
    case.raw.compact_context = interrupt
    with pytest.raises(OSError):
        await case.prepare()
    row = case.adapter._recovery_record()
    assert row["maxCalls"] == 4 and len(row["calls"]) == 2 and row["completedParts"] == 1
    assert [call["state"] for call in row["calls"]] == ["completed", "reserved"]
    case.origin.update(inputId="fresh-send", generationId="fresh-generation", inputIds=["fresh-send"])
    with pytest.raises(ContextRecoveryError) as paused:
        await case.prepare()
    assert paused.value.code == "summary_work_paused"
    detail = exception_details(paused.value)
    assert detail["summary"] == "Conversation preparation can continue from saved progress."
    assert detail["guidance"].startswith("Review preparation to approve calls for your next message.")
    assert len(row["calls"]) == 2  # A notice does not admit or retry a paid call.


@pytest.mark.parametrize("counter", [False, True], ids=["subscription-route", "counted-route"])
async def test_picker_change_preserves_original_failure_or_prepares_transition(tmp_path, monkeypatch, counter):
    """The subscription route has no request_budget; it never enters run_pending."""
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "home"))
    app = AppService(tmp_path / "app", workspace=tmp_path)
    app.management = Management(app)
    await app.dispatch("session.create", {"bundle": "anchors"})
    saved = app._session()
    wire = RecoveryWire()
    try:
        async with ordinary_recovery_case(tmp_path / "native", wire, live=True,
                selection={"instance": "openai", "model": "gpt-6.1-sol", "effort": "high"},
                provider_config={"base_url": "https://offline.invalid/v1"}) as case:
            # Retain the actual provider, SDK, context, and loop. This models the
            # subscription provider's absent counter, not a failing count service.
            if not counter:
                case.raw.request_budget = None
            case.raw.supports_native_compaction = lambda: False
            worker = Worker()
            worker.session, worker.runtime, worker.controls = case.session, case.runtime, case.controls
            worker.activation_gate, worker.activation = case.gate, case.activation
            worker.start_config = {"interruptionRevision": 0}
            publications = []
            case.runtime.capture_activation = case.gate.current
            case.runtime.observer = worker.observe
            monkeypatch.setattr("amplifier_web.runtime_worker.publish", publications.append)
            coord = case.session.coordinator
            coord.register_capability("web.context_transition.input", worker.transition_input)
            coord.register_capability("web.provider_catalog", {"connectionKeys": {"openai": "same-account"}})
            failures = []
            retaining = coord.get_capability("context.request_retention")
            async def observe_retention(**kwargs):
                try:
                    return await retaining(**kwargs)
                except Exception as error:
                    failures.append(error)
                    raise
            coord.register_capability("context.request_retention", observe_retention)
            worker.capture_manager_failures()
            token = case.gate.bind(case.activation)
            try:
                worker.execution = asyncio.create_task(case.session.execute(""))
            finally:
                case.gate.reset(token)

            async def send(identity, text):
                before = case.runtime.sequence
                await worker._command_serial({"op": "send", "id": "command-" + identity,
                    "input_id": identity, "text": text, "interruptionRevision": 0})
                return await case.runtime.wait_for(lambda event:
                    event["type"] in {"generation.finished", "generation.failed"}
                    and event.get("sequence", 0) > before and identity in event.get("input_ids", []),
                    timeout=10)

            try:
                assert (await send("seed", "SEED-EFFECT: preserve ORBIT."))["type"] == "generation.finished"
                await append_recovery_evidence(case.context, [str(i) for i in range(12)])
                assert (await send("source", "Establish source checkpoint."))["type"] == "generation.finished"
                await case.checkpoint()
                # Old checkpoints predate the optional connection-origin proof.
                # Preserve the real Context format and recompute its checksum.
                from amplifier_web.context_continuity import digest
                record = json.loads(case.adapter.path.read_bytes())
                assert record["summary"]
                record.pop("unifiedPortableOrigin", None)
                record["sha256"] = digest({key: value for key, value in record.items() if key != "sha256"})
                case.adapter.path.write_text(json.dumps(record), encoding="utf-8")
                case.adapter._accepted_record = copy.deepcopy(record)
                original = case.adapter.path.read_bytes()
                target = {"instance": "openai", "model": "gpt-6.1-astra", "effort": "high"}
                monkeypatch.setattr("amplifier_web.provider_catalog.configured_binding", lambda *a, **kw: "same-account")
                await app.dispatch("conversation.configure", {
                    "sessionId": saved["id"], "selection": target, "expectedRevision": 0})
                assert saved["pendingModelSelection"] == target

                manager = RuntimeManager()
                manager.start = AsyncMock()  # The already mounted process is lent to the host transport.
                async def control(sid, operation, arguments, **kwargs):
                    return await case.controls.perform(operation, arguments)
                manager.control = control
                await manager._start_for_input(copy.deepcopy(saved), app.on_runtime_event)
                assert coord.get("orchestrator").root_provider.selection == target
                before = len(wire.completions)
                wire.reducing_models.add(target["model"])
                terminal = await send("target", "Continue with the selected model.")
                if counter:
                    assert terminal["type"] == "generation.finished"
                    assert case.adapter.recovery_status()["state"] == "committed"
                    assert wire.completions[-1]["model"] == target["model"]
                    assert len(wire.completions) == before + 1
                    assert wire.effects == 1 and wire.unknown_invocations == 0
                    return
                assert terminal["type"] == "generation.failed"
                assert terminal["error_type"] == "CompactionError"
                assert failures[-1].code == "model_transition_required"
                assert "error_code" not in terminal  # The pinned loop drops this code.
                assert not case.adapter.recovery_path.exists()
                assert case.adapter.path.read_bytes() == original
                assert len(wire.completions) == before
                canonical = copy.deepcopy(await case.context.get_messages())
                with pytest.raises(RuntimeError, match="Execution failed: ManagerTurnError"):
                    await asyncio.wait_for(worker.execution, 5)
                from amplifier_web.session_health import generation_failure, terminal_exception_failure
                capture = next(row["terminalFailure"] for row in publications if row.get("terminalFailure"))
                detail = terminal_exception_failure({
                    "id": case.session.session_id, "status": "error",
                    "generations": [{**terminal, "event": terminal["type"]}],
                    "failure": {**generation_failure(terminal),
                        "generationId": terminal["generation_id"], "inputIds": terminal["input_ids"]},
                }, capture)
                assert detail["code"] == "model_transition_required"
                assert "model you chose" in detail["summary"]
                assert "previous model and connection" in detail["guidance"]
                # Feed the real worker's JSON-lines through the host reader and
                # saved UI projection, not just the pure formatting function.
                stdout = asyncio.StreamReader()
                stdout.feed_data(b"".join((json.dumps(row) + "\n").encode() for row in publications))
                stdout.feed_eof()
                class Process:
                    returncode = 0
                    async def wait(self):
                        return 0
                process = Process()
                process.stdout = stdout
                ready = asyncio.get_running_loop().create_future()
                ready.set_result({})
                row = {"process": process, "ready": ready, "pending": {}, "closing": False,
                       "inflight": set(),
                       "runtimeOwnerId": "fixture-process", "runtime_id": case.session.session_id,
                       "inputId": "target", "emit": app.on_runtime_event}
                manager.workers[saved["id"]] = row
                await manager._read(saved["id"], row)
                receipt = await app.dispatch("session.inspect", {"id": saved["id"]})
                assert receipt["result"]["failure"]["code"] == "model_transition_required"
                assert receipt["result"]["failure"]["summary"] == detail["summary"]
                assert "error_code" not in next(event for event in app._session(saved["id"])["generations"]
                                               if event["event"] == "generation.failed")
                await app.close()
                app = AppService(tmp_path / "app", workspace=tmp_path)
                reopened = await app.dispatch("session.inspect", {"id": saved["id"]})
                assert reopened["result"]["failure"] == receipt["result"]["failure"]
            finally:
                if not worker.execution.done():
                    from amplifier_module_loop_live.runtime import Input
                    await case.runtime.submit(Input("stop", activation=case.activation))
                    await asyncio.wait_for(worker.execution, 5)
        if not counter:
            # The failed loop is closed. A new mount gets the same canonical
            # messages, checkpoint and selected B, not a cleared checkpoint.
            interrupted_hashes = []
            for phase in ("fresh", "summary-interrupted", "after-interruption"):
                before = (len(wire.summaries), len(wire.completions), wire.effects)
                async with ordinary_recovery_case(tmp_path / "native", wire, canonical, live=True,
                    selection=target, provider_config={"base_url": "https://offline.invalid/v1"}) as cold:
                    cold.raw.request_budget = None
                    cold.raw.supports_native_compaction = lambda: False
                    assert cold.context.summary is None
                    assert cold.adapter.path.read_bytes() == original
                    new_worker = Worker()
                    new_worker.session, new_worker.runtime, new_worker.controls = cold.session, cold.runtime, cold.controls
                    new_worker.activation_gate, new_worker.activation = cold.gate, cold.activation
                    new_worker.start_config = {"interruptionRevision": 0}
                    cold.runtime.capture_activation = cold.gate.current
                    cold.runtime.observer = new_worker.observe
                    coord = cold.session.coordinator
                    coord.register_capability("web.context_transition.input", new_worker.transition_input)
                    coord.register_capability("web.provider_catalog", {"connectionKeys": {"openai": "same-account"}})
                    cold_errors = []
                    cold_retaining = coord.get_capability("context.request_retention")
                    async def observe_cold_retention(**kwargs):
                        try:
                            return await cold_retaining(**kwargs)
                        except Exception as error:
                            cold_errors.append({"type": type(error).__name__, "code": getattr(error, "code", None)})
                            raise
                    coord.register_capability("context.request_retention", observe_cold_retention)
                    await cold.controls.perform("provider.select", {**target, "expectedConnectionKey": "same-account"})
                    transport = wire.transport
                    requests = []
                    async def observe_original(request):
                        import httpx
                        assert cold.adapter.path.read_bytes() == original
                        body = json.loads(request.content)
                        summary = (request.url.path.endswith("/responses") and not body.get("tools")
                                   and "Prepare a factual continuation note" in str(body.get("instructions")))
                        if phase == "summary-interrupted" and summary and requests:
                            raise httpx.ConnectError("Synthetic connection loss", request=request)
                        prior_summaries = len(wire.summaries)
                        response = await transport(request)
                        if len(wire.summaries) > prior_summaries:
                            requests.append(digest(body))
                        return response
                    monkeypatch.setattr(cold.raw.client._client._transport, "handler", observe_original)
                    token = cold.gate.bind(cold.activation)
                    try:
                        new_worker.execution = asyncio.create_task(cold.session.execute(""))
                    finally:
                        cold.gate.reset(token)
                    try:
                        await new_worker._command_serial({"op": "send", "id": "command-" + phase,
                            "input_id": "input-" + phase, "text": "Continue with the selected model.",
                            "interruptionRevision": 0})
                        terminal = await cold.runtime.wait_for(lambda event:
                            event["type"] in {"generation.finished", "generation.failed"}, timeout=10)
                        print("COLD_EXPERIMENT", json.dumps({
                            "phase": phase, "terminal": terminal["type"], "code": terminal.get("error_code"),
                            "summaryCalls": len(wire.summaries) - before[0],
                            "foregroundCalls": len(wire.completions) - before[1],
                            "contextErrors": cold_errors,
                            "effects": wire.effects, "checkpointChanged": cold.adapter.path.read_bytes() != original,
                            "repeatedConfirmedRequests": len(set(requests) & set(interrupted_hashes))}))
                        assert wire.effects == before[2] == 1 and wire.unknown_invocations == 0
                        assert not cold.adapter.recovery_path.exists()
                        assert cold.adapter.path.read_bytes() == original
                        if phase == "summary-interrupted":
                            assert terminal["type"] == "generation.failed"
                            assert len(requests) == 1 and len(wire.completions) == before[1]
                            interrupted_hashes = requests[:]
                            canonical = copy.deepcopy(await cold.context.get_messages())
                            with pytest.raises(RuntimeError, match="Execution failed"):
                                await asyncio.wait_for(new_worker.execution, 5)
                        else:
                            assert terminal["type"] == "generation.finished"
                            assert len(wire.completions) == before[1] + 1
                            assert len(requests) == 12
                            if phase == "after-interruption":
                                # Restart alone loses the unjournaled paid prefix.
                                # Therefore it cannot meet the repair's no-rebuy rule.
                                assert set(requests) & set(interrupted_hashes)
                    finally:
                        if not new_worker.execution.done() and not cold.runtime.closed:
                            from amplifier_module_loop_live.runtime import Input
                            await cold.runtime.submit(Input("stop", activation=cold.activation))
                        await asyncio.gather(new_worker.execution, return_exceptions=True)
    finally:
        await app.close()