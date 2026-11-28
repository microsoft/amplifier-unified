"""Model-picker changes with the real context/loop and an offline provider."""
import asyncio
import ast
import copy
import json
from pathlib import Path
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
        assert actual["summary"] == detail["summary"]
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
    subclass = type("OtherCompaction", (CompactionError,), {})(code, "PRIVATE")
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


@pytest.mark.parametrize("change", ["none", "generation", "input", "type", "stage", "code", "stop", "older_cause"])
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
    before = copy.deepcopy(session)
    detail = terminal_exception_failure(session, capture)
    assert bool(detail and detail.get("code") == "model_transition_required") is (change == "none")
    assert session == before  # Native terminal facts are never rewritten.
    assert "PRIVATE" not in json.dumps(detail)


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
    finally:
        await app.close()