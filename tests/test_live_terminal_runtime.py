"""Opt-in terminal adapter acceptance against the real isolated runtime."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def test_terminal_clients_follow_detach_reconnect_and_stop_real_worker(tmp_path):
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    if not python:
        pytest.skip("set UNIFIED_RUNTIME_PYTHON to Python with the pinned runtime and host dependencies")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([python, str(root / "tests/fixtures/live_terminal_probe.py"), str(tmp_path)],
                            cwd=root, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == dict.fromkeys(("two_live_clients", "idle_resume", "duplicate_runs_once",
                                      "detach_keeps_work", "reconnect_restores_history",
                                      "explicit_stop_cancels_provider"), True)


def _check_worker_schema_diagnostic_contract(tmp_path):
    """Nested local-helper controls, not real worker/producer qualification."""
    import asyncio
    import importlib.util

    source = Path(__file__).parent / "fixtures/live_terminal_probe.py"
    spec = importlib.util.spec_from_file_location("producer_diagnostic_controls", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    root = tmp_path / "diagnostic-controls"
    root.mkdir()
    rows, calls, passed = [], [], []
    secret = "DO-NOT-RETAIN-PRIVATE-DIAGNOSTIC"
    config, environment = {"private": secret}, {"private": secret}
    schema = {"fields": [], "private": secret}

    async def success(module_id, **kwargs):
        calls.append((module_id, kwargs))
        return schema

    observed = fixture._producer_schema_observer(root, success, lambda name, row: rows.append((name, row)))
    assert asyncio.run(observed("provider-regularproducer", config=config, environment=environment)) is schema
    assert len(calls) == 1 and calls[0][1]["config"] is config and calls[0][1]["environment"] is environment
    assert len(rows) == 1 and fixture._producer_valid_schema_record(rows[0][1], rows[0][0])
    passed.append("unchanged-success-and-arguments-once")
    assert asyncio.run(observed("provider-unrelated")) is schema
    assert len(calls) == 2 and len(rows) == 1
    passed.append("unrelated-provider-unobserved")

    error = ModuleNotFoundError(secret, name=fixture._SCHEMA_MODULE)
    failures = []

    async def fails(module_id, **kwargs):
        failures.append(module_id)
        raise error

    observed_failure = fixture._producer_schema_observer(root, fails, lambda name, row: rows.append((name, row)))
    try:
        asyncio.run(observed_failure("provider-regularproducer"))
    except ModuleNotFoundError as caught:
        assert caught is error
        tb = caught.__traceback__
        while tb and tb.tb_frame.f_code is not fails.__code__:
            tb = tb.tb_next
        assert tb is not None
    else:
        raise AssertionError("Observer swallowed the original failure")
    assert len(failures) == 1 and rows[-1][1]["exception"]["missingModule"] == fixture._SCHEMA_MODULE
    assert secret not in json.dumps(rows)
    passed.append("same-exception-and-original-traceback-once")

    def broken_writer(*args):
        raise OSError(secret)

    assert asyncio.run(fixture._producer_schema_observer(root, success, broken_writer)(
        "provider-regularproducer")) is schema
    with pytest.raises(ModuleNotFoundError) as captured:
        asyncio.run(fixture._producer_schema_observer(root, fails, broken_writer)("provider-regularproducer"))
    assert captured.value is error and len(failures) == 2
    passed.append("observer-write-failure-preserves-outcome")
    unknown = fixture._producer_exception_shape(ModuleNotFoundError(secret, name=secret), root)
    assert unknown["missingModule"] == "other" and secret not in json.dumps(unknown)
    passed.append("unknown-module-and-message-not-exported")
    capped_rows, before = [], len(calls)
    bounded = fixture._producer_schema_observer(
        root, success, lambda name, row: capped_rows.append((name, row)))
    for _ in range(6):
        assert asyncio.run(bounded("provider-regularproducer")) is schema
    assert len(calls) == before + 6
    assert [name for name, _ in capped_rows] == [
        "schema-1", "schema-2", "schema-3", "schema-4", "overflow", "overflow"]
    passed.append("observation-cap-does-not-cap-original-calls")

    receipts = root / "app/logs/workers"
    receipts.mkdir(parents=True)
    name = "startup-" + "1" * 32 + ".log"
    path = receipts / name
    header = {"phase": "session-mount", "exitCode": 1, "private": secret,
              "failure": json.dumps({"errorType": "ModuleNotFoundError", "message": secret,
                                    "frames": [{"file": "/private/" + secret, "line": 7,
                                                "function": secret, "locals": {"secret": secret}}]})}
    path.write_text(json.dumps(header) + "\n\n" + secret)
    derivative = fixture._producer_startup_diagnostic(root, name)
    assert derivative == {"status": "read", "present": True, "name": name, "phase": "session-mount",
                          "exitCode": 1, "exceptionClass": "ModuleNotFoundError",
                          "frames": [{"location": "other", "line": 7}]}
    assert secret not in json.dumps(derivative)
    passed.append("strict-receipt-derivative-no-private-text")
    assert fixture._producer_startup_diagnostic(root, "../" + name)["status"] == "invalid-name"
    assert fixture._producer_startup_diagnostic(root, "startup-" + "2" * 32 + ".log")["status"] == "unavailable"
    passed.append("invalid-and-missing-receipt-no-fallback")
    for body in ("not-json", "[]", json.dumps({"failure": "not-json"}), "x" * 60_001):
        path.write_text(body)
        assert fixture._producer_startup_diagnostic(root, name)["status"] == "malformed-or-unavailable"
    passed.append("malformed-and-oversized-receipt-refused")
    path.write_text(json.dumps({"phase": "session-mount", "exitCode": None}))
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "metadata-only"
    passed.append("metadata-only-receipt-not-original-exception-proof")
    path.unlink()
    private = root / "private-receipt"
    private.write_text(json.dumps(header) + "\n\n" + secret)
    path.symlink_to(private)
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "unavailable"
    path.unlink()
    path.write_text(json.dumps(header))
    moved = root / "held-workers"
    receipts.rename(moved)
    receipts.symlink_to(moved, target_is_directory=True)
    assert fixture._producer_startup_diagnostic(root, name)["status"] == "malformed-or-unavailable"
    receipts.unlink()
    moved.rename(receipts)
    passed.append("file-and-directory-symlinks-refused")

    directory = root / "schema-diagnostics"
    directory.mkdir()
    assert fixture._producer_schema_diagnostics(root)["gaps"] == [
        "observer-not-installed", "schema-outcome-not-observed"]
    (directory / "installed.json").write_text(json.dumps({"status": "installed"}))
    (directory / "schema-1.json").write_text(json.dumps(rows[0][1]))
    assert fixture._producer_schema_diagnostics(root)["gaps"] == []
    corrupted = {**rows[0][1], "ownedPackageOriginMatches": secret}
    (directory / "schema-1.json").write_text(json.dumps(corrupted))
    readback = fixture._producer_schema_diagnostics(root)
    assert "unreadable-schema-1" in readback["gaps"] and secret not in json.dumps(readback)
    for corrupted in (
        {**rows[0][1], "schemaShape": {**rows[0][1]["schemaShape"], "fieldCount": secret}},
        {**rows[1][1], "exception": {**rows[1][1]["exception"], "missingModule": secret}},
        {**rows[1][1], "exception": {**rows[1][1]["exception"],
                                    "frames": [{"location": secret, "line": 7}]}},
    ):
        (directory / "schema-1.json").write_text(json.dumps(corrupted))
        readback = fixture._producer_schema_diagnostics(root)
        assert "unreadable-schema-1" in readback["gaps"] and secret not in json.dumps(readback)
    passed.append("missing-and-corrupt-observation-stays-gap")
    for fault in ("extraction", "persistence"):
        for callback_raises in (False, True):
            report = {"sessionId": "owned-synthetic", "runtimeEvents": [],
                      "startupDiagnostics": [], "status": "running"}
            payload = {"sessionId": "owned-synthetic", "phase": "worker_startup",
                       "diagnosticReceipt": name, "error": secret}
            payload_before = json.dumps(payload)
            callback_calls, save_calls, extract_calls = [], [], []
            callback_error = ValueError("product callback failure")
            returned = object()

            def extract(*args):
                extract_calls.append(args)
                if fault == "extraction":
                    raise OSError(secret)
                return derivative

            def persist():
                save_calls.append(True)
                raise OSError(secret)

            async def callback(kind, original_payload):
                callback_calls.append((kind, original_payload))
                if callback_raises:
                    raise callback_error
                return returned

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_startup_diagnostic", extract)
                observer = fixture._producer_event_observer(root, report, persist, callback)
                if callback_raises:
                    with pytest.raises(ValueError) as caught:
                        asyncio.run(observer("runtime.error", payload))
                    assert caught.value is callback_error
                else:
                    assert asyncio.run(observer("runtime.error", payload)) is returned
            assert len(callback_calls) == 1 and callback_calls[0][1] is payload
            assert json.dumps(payload) == payload_before and len(extract_calls) == 1
            assert len(save_calls) == (fault == "persistence")
            assert report["status"] == "failed" and report["diagnosticIncomplete"] is True
            assert report["diagnosticFailures"][0]["phase"] == (
                "event-receipt-extraction" if fault == "extraction" else "event-receipt-save")
            assert report["secondaryFailures"] and "primaryFailure" not in report
            assert secret not in json.dumps(report)
            passed.append(f"event-{fault}-callback-{'raises' if callback_raises else 'returns'}")

    for fault in ("collection", "persistence"):
        for has_primary in (False, True):
            primary = {"phase": "producer-admission", "exceptionClass": "ValueError"}
            report = {"status": "running", "startupDiagnostics": []}
            if has_primary:
                report["primaryFailure"] = primary
            cleanup_calls, collect_calls, save_calls = [], [], []
            primary_error = ValueError("product primary failure")
            returned = object()

            def collect(*args):
                collect_calls.append(args)
                if fault == "collection":
                    raise OSError(secret)
                return {"rows": [], "gaps": ["schema-outcome-not-observed"]}

            def persist():
                save_calls.append(True)
                if fault == "persistence":
                    raise OSError(secret)

            async def cleanup():
                cleanup_calls.append(True)
                return returned

            async def attempt():
                try:
                    if has_primary:
                        raise primary_error
                finally:
                    result = await fixture._producer_cleanup_with_diagnostics(root, report, persist, cleanup)
                    assert result is returned

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_schema_diagnostics", collect)
                if has_primary:
                    with pytest.raises(ValueError) as caught:
                        asyncio.run(attempt())
                    assert caught.value is primary_error and report["primaryFailure"] is primary
                else:
                    asyncio.run(attempt())
                    assert "primaryFailure" not in report
            assert len(cleanup_calls) == len(collect_calls) == len(save_calls) == 1
            assert report["status"] == "failed" and report["diagnosticIncomplete"] is True
            assert report["diagnosticFailures"][0]["phase"] == (
                "precleanup-collection" if fault == "collection" else "precleanup-save")
            assert report["secondaryFailures"] and secret not in json.dumps(report)
            passed.append(f"precleanup-{fault}-{'with' if has_primary else 'without'}-primary")

    report = {"status": "observed", "secondaryFailures": [], "primaryFailure": primary}
    secondary = report["secondaryFailures"]
    assert fixture._producer_diagnostic_step(root, report, "final-cleanup-save", broken_writer) is False
    assert report["primaryFailure"] is primary and report["secondaryFailures"] is secondary
    assert secondary and report["status"] == "failed" and secret not in json.dumps(report)
    passed.append("final-save-failure-marks-incomplete-without-replacing-primary")

    async def cleanup_failure():
        cleanup_calls.append(True)
        raise primary_error

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixture, "_producer_schema_diagnostics", lambda root: {"rows": [], "gaps": []})
        with pytest.raises(ValueError) as caught:
            asyncio.run(fixture._producer_cleanup_with_diagnostics(root, report, lambda: None, cleanup_failure))
        assert caught.value is primary_error
    assert len(cleanup_calls) == 2
    passed.append("cleanup-exception-not-swallowed")

    # Core validates providers by calling mount with a validation coordinator,
    # then calls mount again for the session. Reproduce the old receipt collision
    # using the actual validators, without model/tool execution or another worker.
    from amplifier_core.testing import MockCoordinator
    from amplifier_core.validation.base import ValidationResult
    from amplifier_core.validation.provider import ProviderValidator
    from amplifier_core.validation.tool import ToolValidator

    mount_root = root / "mount-lifecycle"
    counts = mount_root / "counts"
    counts.mkdir(parents=True)
    validator = ProviderValidator()
    legacy_receipt = counts / "legacy-mounted.json"

    async def legacy_mount(coordinator, config=None):
        fixture._producer_record(counts, "legacy-mounted", {"pid": os.getpid()})
        await fixture.mount_producer_provider(coordinator, config)

    async def mount_controls():
        legacy_result = ValidationResult(module_type="provider", module_path="synthetic-legacy")
        await validator._check_protocol_compliance(legacy_result, legacy_mount, config={})
        assert legacy_result.passed and legacy_receipt.exists()
        legacy_session = MockCoordinator()
        with pytest.raises(FileExistsError):
            await legacy_mount(legacy_session, {})
        assert not legacy_session.get("providers")
        legacy_receipt.unlink()
        passed.append("legacy-validation-consumes-exclusive-mount-receipt")

        provider_result = ValidationResult(module_type="provider", module_path="synthetic-provider")
        tool_result = ValidationResult(module_type="tool", module_path="synthetic-tool")
        await validator._check_protocol_compliance(
            provider_result, fixture.mount_producer_provider, config={})
        await ToolValidator()._check_protocol_compliance(
            tool_result, fixture.mount_producer_tools, config={})
        assert provider_result.passed and tool_result.passed and not list(counts.iterdir())
        session = MockCoordinator()
        await fixture.mount_producer_provider(session, {})
        await fixture.mount_producer_tools(session, {})
        provider = session.get("providers", "regularproducer")
        assert provider.coordinator is session and provider.calls == 0
        assert not list(counts.iterdir())  # Validation and mount are not readiness.
        passed.append("provider-and-tool-validation-then-mount-have-no-receipt-side-effect")

        await fixture.producer_session_ready(session)
        receipt = json.loads((counts / "worker-mounted.json").read_text())
        assert receipt == {"pid": os.getpid(), "phase": "session-ready"}
        with pytest.raises(FileExistsError):
            await fixture.producer_session_ready(session)
        assert len(list(counts.iterdir())) == 1 and provider.calls == 0
        assert not list(counts.glob("*attempt*.json"))
        passed.append("readiness-records-one-bound-provider-without-provider-tool-execution")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNIFIED_PRODUCER_ROOT", str(mount_root))
        asyncio.run(mount_controls())

    async def context_controls():
        import hashlib
        from amplifier_core import HookRegistry, HookResult, ToolResult
        from amplifier_core.message_models import TextBlock, ToolCall
        from amplifier_module_context_simple import SimpleContextManager
        from amplifier_module_loop_streaming import StreamingOrchestrator

        long_output, short_output = [case["output"] for case in fixture._producer_cases()]
        serialized = ToolResult(success=True, output=long_output).get_serialized_output()
        context = SimpleContextManager()
        message = {"role": "tool", "name": "regular_echo",
                   "tool_call_id": "context-control", "content": serialized}
        await context.add_message(message)
        projected = (await context.get_messages())[0]["content"]
        assert message["content"] == serialized  # Admission did not mutate the caller.
        assert len(serialized.encode()) == 150041 > context.max_tool_result_bytes
        assert len(projected.encode()) == context.max_tool_result_bytes == 131072
        assert "[tool-result truncated at ingress:" in projected
        with pytest.raises(json.JSONDecodeError):
            json.loads(projected)  # The previous provider oracle rejects this valid context.
        passed.append("real-context-ingress-rejects-legacy-full-json-oracle")

        output_only_projected = projected
        assert hashlib.sha256(output_only_projected.encode()).hexdigest() == (
            "95acd5cd80a87c5e863c6f556c517d6670ed44ccf8c9759cb5c5c00a9f6d66ee")
        full_result = ToolResult(success=True, output=long_output).model_dump()
        seen = []

        async def continuing(event, data):
            seen.append((event, data["result"]))
            return HookResult(action="continue")

        hooks = HookRegistry()
        hooks.register("tool:post", continuing)
        post = await hooks.emit("tool:post", {"result": full_result})
        assert len(seen) == 1 and seen[0] == ("tool:post", full_result)
        assert post.action == "continue" and post.data["result"] == full_result
        assert post.data["result"] is not full_result  # Streaming's `is not` branch.
        serialized = json.dumps(post.data["result"])
        assert await fixture._producer_post_hook_serialization(long_output) == serialized
        assert len(serialized.encode()) == 150085
        assert hashlib.sha256(serialized.encode()).hexdigest() == (
            "e786c966f2385075845ca6469a63d1f1b92191ff49103681c7291e1b2cc55742")
        passed.append("native-continue-hook-roundtrip-preserves-full-values-not-identity")

        calls = []
        class Echo:
            async def execute(self, arguments):
                calls.append(arguments)
                return ToolResult(success=True, output=long_output)

        # Exercise the actual pinned streaming tool/post/context path, not a
        # copied branch. This focused control does not claim capture persistence.
        context = SimpleContextManager()
        tool_call = ToolCall(id="native-envelope-control", name="regular_echo",
                             arguments={"slot": 1})
        result = await StreamingOrchestrator({})._execute_tool_with_result(
            tool_call, {"regular_echo": Echo()}, context, hooks)
        assert result == {"success": True, "error": None} and calls == [{"slot": 1}]
        messages = await context.get_messages()
        assert len(messages) == 1 and messages[0]["tool_call_id"] == tool_call.id
        assert len(seen) == 2 and seen[1] == ("tool:post", full_result)
        assert seen[1][1]["output"] == long_output  # Full hook value precedes clipping.
        projected = messages[0]["content"]
        assert len(projected.encode()) == 131072
        assert hashlib.sha256(projected.encode()).hexdigest() == (
            "618a8d223510196ab140fd2c5007d40bbc1082076388c4daf9bdbe5f67d30842")
        passed.append("native-streaming-post-hook-context-reproduces-retained-actual-hash")

        # The old output-only oracle is not an alternate accepted encoding.
        with pytest.raises(AssertionError):
            await fixture._producer_context_result(output_only_projected, long_output)
        passed.append("native-envelope-oracle-rejects-output-only-context")
        observed = await fixture._producer_context_result(projected, long_output)
        assert observed["exactContext"] and observed["contextClipped"]
        assert observed["serializedBytes"] == 150085 and observed["contextBytes"] == 131072
        assert await fixture._producer_context_result([TextBlock(text=projected)], long_output) == observed
        with pytest.raises(AssertionError):
            await fixture._producer_context_result(projected[:-1], long_output)
        small = await fixture._producer_post_hook_serialization(short_output)
        assert json.loads(small) == ToolResult(success=True, output=short_output).model_dump()
        assert not (await fixture._producer_context_result(small, short_output))["contextClipped"]
        passed.append("provider-context-exact-clipped-and-small-values-with-corruption-refusal")

        # The same full canonical comparator/field reader must reject the
        # otherwise-valid clipped provider text, including through lazy chunks.
        sid, node_id = "canonical-control-session", "canonical-control-node"
        reference = {"sessionId": sid, "id": node_id, "part": "nodes", "field": "output"}
        row = {"id": node_id, "outputDetail": reference}
        expected = fixture._producer_result_encodings(long_output)
        for full in (True, False):
            wire = (json.dumps(ToolResult(success=True, output=long_output).model_dump(mode="json"),
                               ensure_ascii=False) if full else projected)

            async def read_chunk(client, query):
                assert {key: query[key] for key in reference} == reference
                assert query["limit"] == 4096
                start = query["offset"]
                value = wire[start:start + query["limit"]]
                end = start + len(value)
                return {"value": value, "nextOffset": end if end < len(wire) else None}

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_get", read_chunk)
                field = await fixture._producer_field(None, sid, row, "output", expected)
            assert field["reference"] is reference and field["chunks"] > 1
            assert field["exact"] is full
            if full:
                assert field["value"]["output"] == long_output
        passed.append("canonical-lazy-full-unicode-accepted-clipped-context-rejected")

    asyncio.run(context_controls())

    async def completion_controls():
        from types import SimpleNamespace
        from amplifier_core.message_models import TextBlock

        captured, called = [], []
        request = object()
        argument = object()
        returned = object()
        primary_error = AssertionError(secret)
        provider = fixture.RegularProducerProvider()
        should_raise = False

        async def original(actual_request, observe, **kwargs):
            called.append((actual_request, kwargs))
            observe("prior-tool-message", lambda: {"priorFound": False, "exactMatchCount": 0})
            if should_raise:
                raise primary_error
            return returned

        # Exercise the actual complete wrapper, not a copied observer call.
        provider._complete = original
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("UNIFIED_PRODUCER_ROOT", str(root))
            patch.setattr(fixture, "_producer_record",
                          lambda directory, name, row: captured.append((name, row)))
            assert await provider.complete(request, marker=argument) is returned
            should_raise = True
            with pytest.raises(AssertionError) as caught:
                await provider.complete(request, marker=argument)
            assert caught.value is primary_error
            tb = caught.value.__traceback__
            while tb is not None and tb.tb_frame.f_code is not original.__code__:
                tb = tb.tb_next
            assert tb is not None
        assert len(called) == 2 and all(r is request and kw["marker"] is argument for r, kw in called)
        assert [row["status"] for _, row in captured] == ["returned", "raised"]
        assert all(row["stage"] == "prior-tool-message" for _, row in captured)
        assert all(fixture._producer_valid_completion_record(row, name) for name, row in captured)
        assert secret not in json.dumps(captured)
        passed.append("completion-wrapper-once-same-arguments-result-and-exception")

        for failing in (False, True):
            should_raise = failing
            before = len(called)
            with pytest.MonkeyPatch.context() as patch:
                patch.setenv("UNIFIED_PRODUCER_ROOT", str(root))
                patch.setattr(fixture, "_producer_record", broken_writer)
                if failing:
                    with pytest.raises(AssertionError) as caught:
                        await provider.complete(request, marker=argument)
                    assert caught.value is primary_error
                else:
                    assert await provider.complete(request, marker=argument) is returned
            assert len(called) == before + 1
        passed.append("completion-writer-failure-preserves-return-and-primary")

        context_rows = []
        async def mismatched(observe):
            return await fixture._producer_context_result(
                [TextBlock(text=secret)], fixture._producer_cases()[1]["output"], observe)

        with pytest.raises(AssertionError):
            await fixture._producer_completion_observer(
                2, mismatched, lambda name, row: context_rows.append((name, row)))
        row = context_rows[0][1]
        assert row["stage"] == "context-comparison" and row["status"] == "raised"
        assert row["details"]["actual"]["type"] == "list"
        assert row["details"]["blocks"] == {"count": 1, "capped": False, "types": ["TextBlock"]}
        assert row["details"]["normalized"]["bytes"] == len(secret.encode())
        assert not row["details"]["actualEqualsExpected"]
        assert row["details"]["expectedEqualsSerialized"] is True
        assert row["details"]["oracleLimitBytes"] == 131072
        assert row["exception"]["exceptionClass"] == "AssertionError"
        assert any(f["location"] == "fixture" for f in row["exception"]["frames"])
        assert secret not in json.dumps(row)
        passed.append("completion-context-failure-stage-and-mismatch-without-content")

        def unavailable_measurement():
            raise OSError(secret)

        async def bad_measurement(observe):
            observe("capture-destinations", unavailable_measurement)
            return returned

        bounded = []
        writer = lambda name, row: bounded.append((name, row))
        assert await fixture._producer_completion_observer(1, bad_measurement, writer) is returned
        assert bounded[0][1]["incomplete"] and bounded[0][1]["details"] == {}
        assert await fixture._producer_completion_observer(4, bad_measurement, writer) is returned
        assert bounded[-1] == ("completion-overflow", {"status": "overflow"})
        blocks = fixture._producer_completion_blocks([{"type": secret}] * 100)
        assert blocks == {"count": 16, "capped": True, "types": ["dict-other"] * 16}
        assert fixture._producer_completion_text("x" * 1_048_577) == {
            "type": "string", "bytes": None, "sha256": None, "capped": True}
        shape = fixture._producer_prior_shape(SimpleNamespace(messages=[
            SimpleNamespace(role="tool", tool_call_id=secret)] * 1025), [secret])
        assert shape == {"messagesType": "list", "messageCount": 1024,
                         "messagesCapped": True, "expectedIdPresent": True, "exactMatchCount": 1024}
        assert secret not in json.dumps((bounded, blocks, shape))
        passed.append("completion-measurement-failure-and-observation-bounds")

        diagnostic_root = root / "completion-files"
        directory = diagnostic_root / "schema-diagnostics"
        directory.mkdir(parents=True)
        (diagnostic_root / "counts").mkdir()
        (diagnostic_root / "counts/provider-attempt-2.json").write_text("{}")
        assert fixture._producer_completion_diagnostics(diagnostic_root)["gaps"] == ["missing-completion-2"]
        path = directory / "completion-2.json"
        path.write_text(json.dumps(row))
        assert fixture._producer_completion_diagnostics(diagnostic_root) == {"rows": [row], "gaps": []}
        for bad in (
            {**row, "stage": secret}, {**row, "details": {"raw": secret}},
            {**row, "exception": {**row["exception"], "frames": [{"location": secret, "line": 2}]}},
            {**row, "details": {"normalized": {**row["details"]["normalized"], "sha256": secret}}},
        ):
            path.write_text(json.dumps(bad))
            result = fixture._producer_completion_diagnostics(diagnostic_root)
            assert result == {"rows": [], "gaps": ["unreadable-completion-2"]}
            assert secret not in json.dumps(result)
        path.write_text("x" * 8193)
        assert fixture._producer_completion_diagnostics(diagnostic_root)["gaps"] == ["unreadable-completion-2"]
        passed.append("completion-missing-corrupt-oversized-records-stay-gaps")

        for failing_save in (False, True):
            report = {"status": "failed", "primaryFailure": {"phase": "producer-completion"},
                      "startupDiagnostics": []}
            primary = report["primaryFailure"]
            cleanup_calls = []

            async def cleanup():
                cleanup_calls.append(True)
                return returned

            def broken_collect(root):
                raise OSError(secret)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixture, "_producer_schema_diagnostics", lambda root: {"rows": [], "gaps": []})
                patch.setattr(fixture, "_producer_completion_diagnostics", broken_collect)
                async def attempt():
                    try:
                        raise primary_error
                    finally:
                        assert await fixture._producer_cleanup_with_diagnostics(
                            root, report, broken_writer if failing_save else lambda: None, cleanup) is returned

                with pytest.raises(AssertionError) as caught:
                    await attempt()
                assert caught.value is primary_error
            assert cleanup_calls == [True] and report["primaryFailure"] is primary
            assert report["diagnosticIncomplete"] and report["secondaryFailures"]
            assert report["diagnosticFailures"][0]["phase"] == "completion-collection"
            assert secret not in json.dumps(report)
        passed.append("completion-collection-and-save-failure-cannot-prevent-cleanup")

    asyncio.run(completion_controls())

    def capture_controls():
        from types import SimpleNamespace
        from amplifier_web.diagnostics import Diagnostics

        state = None
        reads = []
        unregistered = []

        def capability(name):
            reads.append(name)
            assert name == "context_intelligence._hook_state"
            return state

        coordinator = SimpleNamespace(get_capability=capability)
        absent = fixture._producer_capture_registration(coordinator)
        assert absent == {"source": "worker-hook-state", "statePresent": False,
                          "registrationsValid": False, "registrationCount": None, "enabled": False}
        state = {"unregister_fns": []}
        disabled = fixture._producer_capture_registration(coordinator)
        assert disabled["statePresent"] and disabled["registrationsValid"]
        assert disabled["registrationCount"] == 0 and disabled["enabled"] is False
        for bad in ({"unregister_fns": secret}, {"unregister_fns": [secret]},
                    {"unregister_fns": [lambda: None] * 4097}):
            state = bad
            observed = fixture._producer_capture_registration(coordinator)
            assert observed["enabled"] is False and not observed["registrationsValid"]
            assert observed["registrationCount"] is None and secret not in json.dumps(observed)
        passed.append("capture-registration-absent-disabled-invalid-fail-closed")

        # Hook mount creates an empty list; its later on_session_ready callback
        # adds registrations. Read current state, never cache the mount-time false.
        registrations = []
        state = {"unregister_fns": registrations}
        assert fixture._producer_capture_registration(coordinator)["enabled"] is False
        registrations.append(lambda: unregistered.append(True))
        enabled = fixture._producer_capture_registration(coordinator)
        assert enabled["enabled"] is True and enabled["registrationCount"] == 1
        assert unregistered == []  # Inspecting state never calls unregister.
        state = None  # Actual hook cleanup clears the capability.
        assert fixture._producer_capture_registration(coordinator)["enabled"] is False
        assert len(reads) == 8
        passed.append("capture-registration-late-ready-and-cleanup-not-cached")

        calls = [{"ordinal": n, "captureState": dict(enabled)} for n in (1, 2, 3)]
        evidence = fixture._producer_capture_evidence(calls)
        assert evidence["enabled"] is True
        assert evidence["source"] == "worker-hook-state-per-completion"
        for bad_calls in (
            calls[:2],
            [calls[0], calls[0], calls[2]],
            [calls[0], {"ordinal": 2, "captureState": disabled}, calls[2]],
            [{"ordinal": n, "captureResolverPresent": True,
              "runtimeReport": {"contextIntelligence": {"enabled": True}}} for n in (1, 2, 3)],
        ):
            assert fixture._producer_capture_evidence(bad_calls)["enabled"] is False
        passed.append("capture-enabled-requires-three-real-state-receipts-not-public-flag")

        # Characterize, do not fix, the product's presence-only suppression.
        # The test-only change must not inject a public flag and alter this path.
        for report, expected_records in (
            ({}, 1),
            ({"contextIntelligence": {"enabled": True}}, 0),
            ({"contextIntelligence": {"enabled": False}}, 0),
        ):
            records = []
            diagnostic = SimpleNamespace(record=lambda *args, **kwargs: records.append((args, kwargs)))
            session = {"id": "capture-report-control", "workspace": str(root), "runtimeReport": report}
            before = json.dumps(session)
            Diagnostics._runtime_event(diagnostic, "execution.event", {
                "kind": "tool", "phase": "completed", "label": "synthetic-tool",
                "toolCallId": "synthetic-call"}, session)
            assert len(records) == expected_records and json.dumps(session) == before
        passed.append("capture-public-presence-suppression-current-contract-characterized")

    capture_controls()

    def reader_evidence_controls():
        import sys

        owned = root.resolve()
        python = str(Path(sys.executable).absolute())
        module = "amplifier_web.shell_query_worker"
        environment = {
            "PYTHONPATH": os.pathsep.join((str(owned / "bootstrap"),
                                          str(source.parent), str(fixture.ROOT))),
            "UNIFIED_PRODUCER_ROOT": str(owned),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PRIVATE_CONTROL": secret,
        }
        command = [python, "-m", module,
                   "--spool", str(owned / "app/projection-spool-control" / ("1" * 32)),
                   "--cache-home", str(owned / "app"), "--memory-limit", "805306368"]
        original = json.dumps([command, environment])
        evidence = fixture._producer_spawn_evidence(
            owned, python, command, str(fixture.ROOT), environment)
        assert evidence["argvShape"][2] == module
        assert evidence["argc"] == 9 and secret not in json.dumps(evidence)
        # Rendering one known literal grants no process authority. These are
        # pure classifier calls, not Popen attempts or simulated execution.
        for cold in (False, True):
            assert fixture._producer_spawn_spec(
                owned, python, command, str(fixture.ROOT), environment, cold=cold) is None
            for supported in ("amplifier_web.event_window_worker",
                              "amplifier_web.history_projection_worker"):
                reader = [python, "-m", supported, *command[3:]]
                assert fixture._producer_spawn_spec(
                    owned, python, reader, str(fixture.ROOT), environment,
                    cold=cold) == "history-reader"
        assert json.dumps([command, environment]) == original
        passed.append("catalog-module-evidence-does-not-grant-spawn-authority")

        for unknown in ("x" * len(module), module + ".other", secret):
            altered = [python, "-m", unknown, *command[3:]]
            evidence = fixture._producer_spawn_evidence(
                owned, python, altered, str(fixture.ROOT), environment)
            assert evidence["argvShape"][2] == {
                "redacted": True, "type": "str", "length": len(unknown)}
            assert unknown not in json.dumps(evidence)
            for cold in (False, True):
                assert fixture._producer_spawn_spec(
                    owned, python, altered, str(fixture.ROOT), environment, cold=cold) is None
        passed.append("unknown-and-lookalike-reader-modules-remain-redacted-and-refused")

    reader_evidence_controls()
    return {"status": "passed", "cases": passed,
            "scope": "Authored helper controls only; actual worker evidence is separate"}


def _check_regular_work_details_real_producer_and_service_reopen(tmp_path):
    """An observation check, not a claim that missing-work product repair passed."""
    import signal

    root = Path(__file__).resolve().parents[1]
    scratch = tmp_path / "regular-producer"
    evidence_path = Path(os.environ.get("UNIFIED_PRODUCER_EVIDENCE",
                                       str(tmp_path / "regular-producer-evidence.json"))).resolve()
    assert not evidence_path.is_relative_to(scratch.resolve()), "Evidence must survive outside scratch"
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    controls = _check_worker_schema_diagnostic_contract(tmp_path)

    def failure(phase, message):
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
        wrapper_failure = {"phase": phase, "message": message}
        evidence["wrapperFailure"] = wrapper_failure
        evidence.setdefault("primaryFailure", wrapper_failure)
        evidence.update(status="failed", failure=evidence["primaryFailure"])
        evidence["diagnosticContractControls"] = controls
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    if not python:
        failure("wrapper-setup", "UNIFIED_RUNTIME_PYTHON is required; no dependency installation or skip")
        pytest.fail("Set UNIFIED_RUNTIME_PYTHON to the prepared pinned native runtime")
    env = dict(os.environ, UNIFIED_PRODUCER_EVIDENCE=str(evidence_path))
    try:
        process = subprocess.Popen(
            [python, str(root / "tests/fixtures/live_terminal_probe.py"), str(scratch), "--producer"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            start_new_session=True)
    except OSError:
        failure("wrapper-launch", "Producer process could not launch")
        pytest.fail("Producer launch failed; see sanitized evidence", pytrace=False)
    try:
        stdout, stderr = process.communicate(timeout=175)
    except subprocess.TimeoutExpired:
        # Terminate this fixture's own process group, including its worker/readers.
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=5)
        failure("wrapper-timeout", "180-second outer budget; owned process group killed")
        pytest.fail(f"Producer timeout; sanitized evidence: {evidence_path}", pytrace=False)
    if process.returncode and not evidence_path.exists():
        failure("wrapper-exit", f"Fixture exited {process.returncode} before saving a report")
    # Neither raw process stream is an artifact. StartupCapture is best-effort
    # private redaction, not permission to put its messages into CI/JUnit.
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["diagnosticContractControls"] = controls
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    assert process.returncode == 0, f"Producer exit {process.returncode}; sanitized evidence: {evidence_path}"
    assert evidence["status"] == "observed", evidence.get("failure")
    assert evidence["phase"] == "complete"
    assert evidence["providerSchemaPreflight"]["hostValidationUsed"] is True
    assert evidence["providerSchemaPreflight"]["providerAttempts"] == 0
    assert evidence["providerSchemaPreflight"]["toolAttempts"] == 0
    assert set(evidence["moduleSourcePreflight"]) == {"provider-regularproducer", "tool-regularproducer"}
    assert all(row == {"declaredPackageParent": True, "oldPackageDirectoryRejected": True,
                       "foundationActivated": True, "originMatches": True, "installDependencies": False}
               for row in evidence["moduleSourcePreflight"].values())
    assert evidence["workerSchemaDiagnostics"]["gaps"] == []
    assert evidence["providerCompletionDiagnostics"]["gaps"] == []
    assert [row["ordinal"] for row in evidence["providerCompletionDiagnostics"]["rows"]] == [1, 2, 3]
    assert all(row["status"] == "returned" and not row["incomplete"]
               for row in evidence["providerCompletionDiagnostics"]["rows"])
    assert not evidence.get("diagnosticIncomplete", False)
    assert any(row["status"] == "returned" for row in evidence["workerSchemaDiagnostics"]["rows"])
    worker_schema = [row for row in evidence["workerSchemaDiagnostics"]["rows"] if row["status"] == "returned"]
    assert all(row["ownedPackageOriginMatches"] and row["expectedParentInSysPath"]
               and row["fixtureHelperOriginMatches"] and row["schemaHelperOriginMatches"]
               for row in worker_schema)
    assert evidence["processGuardPreflight"]["status"] == "passed"
    assert evidence["processGuardPreflight"]["cases"][0]["actualPopenAndPosixSpawn"] is True
    assert evidence["noExtraColdExecution"] is True
    assert evidence["actualCaptureState"]["enabled"] is True
    assert evidence["actualCaptureState"]["source"] == "worker-hook-state-per-completion"
    assert len(evidence["actualCaptureState"]["perCompletion"]) == 3
    assert all(state["enabled"] is True and state["registrationCount"] > 0
               for state in evidence["actualCaptureState"]["perCompletion"])
    assert evidence["captureConfiguration"]["destinations"] == {}
    assert evidence["countsBeforeCold"] == evidence["countsAfterCold"]
    assert evidence["finalCounts"]["provider"] == 3
    assert evidence["finalCounts"]["tool"] == 2
    assert evidence["finalCounts"]["providerAttempts"] == 3
    assert evidence["finalCounts"]["toolAttempts"] == 2
    assert evidence["finalCounts"]["workerMounts"] == evidence["finalCounts"]["workerLaunches"] == 1
    assert not evidence["finalCounts"]["denials"] and not evidence["fieldIntegrityErrors"]
    assert evidence["finalCleanup"] == {"allServicesClosed": True, "remainingWorkers": 0, "errors": []}
    assert evidence["secondaryFailures"] == []
    # Do NOT assert missing/ambiguous counts are zero or positive. This admitted
    # experiment observes the installed producer, not an invented repair verdict.


# This fixed manual experiment needs additional pinned capture dependencies.
# It is not part of ordinary collection and cannot turn an ordinary host suite
# into a selected-but-skipped success.
if os.environ.get("UNIFIED_RUN_PRODUCER_CHECK") == "1":
    test_regular_work_details_real_producer_and_service_reopen = (
        _check_regular_work_details_real_producer_and_service_reopen)
