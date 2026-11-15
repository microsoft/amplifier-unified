"""Finite offline host diagnostics with a clean error and a private PNG cause.

Real Core/Foundation construction, Children admission/adoption/checkpoint/cleanup,
and Worker's pre-Core observer and executed callback are exercised. Only the
selected module/provider fixture and filesystem locators are substituted.
No SDK, network, global logging filter,
exception rewriting, installed-wheel, Worker.start or universal privacy claim.

Children and Worker.executed currently emit no Python exception log. We observe
actual Core logger entry calls AND its actual LogRecords, not a fabricated host
log or merely a handler's formatted output. The original chain stays available
in memory for classification; chain=True would expose it if passed to a logger.
Core 2.0.2 flattens the outer execute error into RuntimeError. That is not evidence
of original error/cancellation identity surviving Core, nor the privacy mechanism
being tested: the production pre-Core observer sees the original private chain.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import closing
import copy
from dataclasses import dataclass
import hashlib
import io
import logging
import os
from pathlib import Path
import socket
import sys
import traceback

import pytest
from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator
from amplifier_core import _session_exec
from amplifier_core.llm_errors import LLMError
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_foundation.session.media import SessionMediaStore
from amplifier_module_context_simple import SimpleContextManager
from amplifier_module_loop_live.runtime import Runtime

from amplifier_web import runtime_worker, worker_diagnostics
from amplifier_web.host import children as host_children, media as host_media
from amplifier_web.host.media_graph import NamedMediaGraph
from amplifier_web.host.storage import SessionStore
from test_media_child_routes import (
    ChildRouteLoader, ChildRouteLoop, ChildRouteProvider, ProspectiveChildFactory,
    _assert_legacy_fences, _assert_qualified,
)

NAMESPACE = host_media.NAMESPACE


@pytest.fixture(autouse=True)
def isolated_shared_session_files(tmp_path, monkeypatch):
    """Override conftest's environment-writing fixture with locator-only isolation."""
    from amplifier_web import native_provider, runtime_controls
    before = {key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"}
    monkeypatch.setattr(runtime_controls, "app_home", lambda: tmp_path / "app")
    monkeypatch.setattr(native_provider, "app_home", lambda: tmp_path / "app")
    attempts = []

    def refuse_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("This source fixture must never open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_network)
    yield
    assert not attempts, "Unexpected network attempt"
    after = {key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"}
    assert bool(after == before), "The diagnostic proof changed process environment"


def _diagnostic_text(value):
    """Inspect logger inputs, including chains, without printing private values."""
    if isinstance(value, BaseException):
        return "".join(traceback.format_exception(value, chain=True))
    if isinstance(value, dict):
        return "".join(_diagnostic_text(item) for pair in value.items() for item in pair)
    if isinstance(value, (list, tuple)):
        return "".join(_diagnostic_text(item) for item in value)
    return str(value)


def _assert_no_private(value, sentinel):
    __tracebackhide__ = True
    if sentinel in _diagnostic_text(value):
        pytest.fail("Private PNG reached a diagnostic boundary", pytrace=False)


class LoggerEntryCapture:
    """Read-only spy on ONE exact logger instance; delegate unchanged, no filter."""
    def __init__(self, monkeypatch, logger):
        self.calls = []
        self.call_text = []
        self.records = []
        self.logger, self.previous_level = logger, logger.level
        original_log = logger._log
        original_record = logger.makeRecord

        def entry(level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
            # Match Logger._log's normalization while the caller's exception
            # context is still live, including truthy non-tuple exc_info.
            resolved = exc_info
            if exc_info:
                if isinstance(exc_info, BaseException):
                    resolved = (type(exc_info), exc_info, exc_info.__traceback__)
                elif not isinstance(exc_info, tuple):
                    resolved = sys.exc_info()
            actual_call = (level, msg, args, resolved, extra, stack_info)
            self.calls.append(actual_call)
            # Snapshot AT entry, before handlers, filters or later mutation.
            self.call_text.append(_diagnostic_text(actual_call))
            return original_log(level, msg, args, exc_info=exc_info, extra=extra,
                                stack_info=stack_info, stacklevel=stacklevel)

        def record(*args, **kwargs):
            actual = original_record(*args, **kwargs)
            self.records.append(actual)
            return actual

        logger.setLevel(logging.DEBUG)
        monkeypatch.setattr(logger, "_log", entry)
        monkeypatch.setattr(logger, "makeRecord", record)

    def assert_clean(self, sentinel):
        _assert_no_private(self.call_text, sentinel)
        _assert_no_private(self.calls, sentinel)
        for record in self.records:
            _assert_no_private(record.__dict__, sentinel)
            _assert_no_private(logging.Formatter().format(record), sentinel)

    def close(self):
        self.logger.setLevel(self.previous_level)


class DiagnosticProvider(ChildRouteProvider):
    """Finite provider boundary: actual private count/send, then translated error."""
    async def complete(self, request, **kwargs):
        assert await super().complete(request, **kwargs) == "private image inspected"
        self.private_cause = RuntimeError(base64.b64encode(self.pixels).decode())
        self.translated = LLMError("Private image request failed", provider="fixture",
                                  model="fixture", status_code=400, retryable=False)
        try:
            raise self.private_cause
        except RuntimeError:
            raise self.translated from self.private_cause

    async def observe_cleanup(self):
        await super().observe_cleanup()
        self.cleanup_visits += 1


@dataclass
class DiagnosticPrepared(PreparedBundle):
    state: object = None

    async def create_session(self, *, before_initialize=None, **kwargs):
        async def initialize(session):
            coordinator = session.coordinator
            factory = self.state["factory"]
            binding = factory.latest(session.session_id) if factory and kwargs.get("parent_id") else None
            if binding is not None:
                binding.check()
                _assert_legacy_fences(binding.directory)
            provider = DiagnosticProvider(coordinator, self.state["pixels"], binding)
            provider.cleanup_visits = 0
            coordinator.register_cleanup(provider.observe_cleanup)
            loader = ChildRouteLoader(provider, SimpleContextManager(), sentinel=self.state["sentinel"])
            loader.observed = []
            coordinator.loader = loader
            assert self.state["selection"].mint_owner(
                loader, kind="diagnostic-loader", construction=coordinator)
            self.state["sessions"].append(session)
            self.state["providers"].append(provider)
            if before_initialize is not None:
                await before_initialize(session)
        session = await super().create_session(before_initialize=initialize, **kwargs)
        if kwargs.get("parent_id"):
            # Actual Worker observer, installed before Core's execute call. No
            # catch/log/classification implementation is replaced by the test.
            worker = self.state["worker"]
            worker.session = session
            worker.runtime = Runtime(session.session_id)
            worker.capture_manager_failures()
        return session


def diagnostic_selection(plan):
    from amplifier_web.host import prompt_events
    from amplifier_web import provider_recording
    import test_host_media_hooks
    import test_media_child_routes
    paths = [Path(__file__), *[Path(module.__file__) for module in (
        test_host_media_hooks, test_media_child_routes, host_children, host_media,
        prompt_events, provider_recording)]]
    return NamedMediaGraph("finite-child-private-error-diagnostics",
        plans=[plan, host_children.child_plan(plan, {})],
        sources={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        dependencies=(HookResult, ChildRouteLoop),
        owner_rules=(("diagnostic-loader", ChildRouteLoader,
                      ("sentinel", "provider", "context", "tool")),
                     ("coordinator", ModuleCoordinator, ()),
                     ("media-source-policy", host_media.SourcePolicy, ("coordinator",))),
        environments={"NamedFixtureLoader.load.<locals>.mount": [
            {"module_id": name} for name in ("fixture-context", "fixture-provider", "fixture-hooks")]})


@pytest.mark.parametrize("prospective", [False, True], ids=["legacy", "prospective-t2"])
async def test_actual_child_error_chain_host_entry_diagnostics_and_cleanup(
        tmp_path, monkeypatch, prospective):
    from PIL import Image
    if prospective and sys.platform != "linux":
        pytest.skip("Prospective source lifetime is qualified on Linux only")
    factory = ProspectiveChildFactory() if prospective else None
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    sentinel = base64.b64encode(pixels).decode()
    plan = {"session": {"orchestrator": {"module": "fixture-loop"},
                        "context": {"module": "fixture-context"}},
            "providers": [{"module": "fixture-provider"}],
            "hooks": [{"module": "fixture-hooks"}], "agents": {"worker": {}}}
    selection = diagnostic_selection(plan)
    worker = runtime_worker.Worker()
    state = {"selection": selection, "pixels": pixels, "sentinel": sentinel,
             "factory": factory, "worker": worker, "providers": [], "sessions": []}
    prepared = DiagnosticPrepared(copy.deepcopy(plan), BundleModuleResolver({}),
        Bundle(name="diagnostic-child", session=plan["session"], agents=plan["agents"]), state=state)
    store = SessionStore(tmp_path / "sessions")

    async def initialize(session):
        host_media.bind_media(session, store, selection=selection)

    parent = await prepared.create_session(session_id="parent", session_cwd=tmp_path,
                                           before_initialize=initialize)
    task = None
    try:
        async def checkpoint():
            canonical = store.save("parent", await parent.coordinator.get("context").get_messages(), {})
            host_media.acknowledge_media(parent.coordinator, store, "parent", canonical)
        parent.coordinator.register_capability("session.durable_checkpoint", checkpoint)
        assert host_media.seal_hook_graph(parent.coordinator)
        _assert_qualified(parent.coordinator)
        cap = parent.coordinator.get_capability(NAMESPACE)
        origin = {"kind": "upload", "session_id": "parent", "id": "upload-1"}
        ref = (await cap.ingest(pixels, source_format="image-bytes", origin=origin, policy=None))["value"]
        await cap.publish([ref], origin=origin, disposition="retain")
        rows = [{"role": "tool", "name": "fixture", "tool_call_id": "call-1",
                 "content": "Inspect the inherited private image", "metadata": {NAMESPACE: {"refs": [ref]}}}]
        await parent.coordinator.get("context").set_messages(rows)
        await checkpoint()
        rows = await parent.coordinator.get("context").get_messages()
        registry = await host_children.install_children(parent, prepared, Runtime("parent"), store, None,
                                                        canonical_binding_factory=factory)
        # This is the actual logger used by Core's production orchestrator entry.
        # Children/Worker.executed have no logger; do not invent one for the test.
        assert _session_exec.logger is logging.getLogger("amplifier_core._session_exec")
        capture = LoggerEntryCapture(monkeypatch, _session_exec.logger)
        published = []
        monkeypatch.setattr(runtime_worker, "publish", published.append)  # In-memory protocol sink only.
        from amplifier_web import support_diagnostics
        classify = support_diagnostics.exception_chain
        classified = []

        def observe_classification(error):
            classified.append(error)
            return classify(error)  # Same production classifier, unmodified result.

        monkeypatch.setattr(support_diagnostics, "exception_chain", observe_classification)
        task = asyncio.create_task(registry.spawn("worker", "Inspect", parent,
            sub_session_id="diagnostic-child", parent_messages=rows))
        escaped = None
        try:
            await task
        except BaseException as error:
            escaped = error  # Keep the real chain, never render it as a failure report.
        provider = state["providers"][-1]
        assert hasattr(provider, "translated"), "The private provider failure was not exercised"
        original = provider.translated
        assert type(original) is LLMError
        assert original.__cause__ is provider.private_cause
        assert bool(sentinel in str(original.__cause__))
        assert bool(sentinel not in str(original))
        assert bool(sentinel in "".join(traceback.format_exception(original, chain=True)))
        assert (original.provider, original.model, original.status_code, original.retryable) == (
            "fixture", "fixture", 400, False)
        assert not task.cancelled() and task.exception() is escaped
        # Record, do not bypass, the current native Core flattening boundary.
        assert type(escaped) is RuntimeError and escaped is not original
        assert escaped.__cause__ is None
        observed = worker.execution_failure
        assert observed[:2] == (worker.runtime, worker.stop_revision)
        original_capture = observed[2]
        assert classified[0] is original and classified[1] is original
        assert classified[2] is provider.private_cause
        assert [row["errorType"] for row in original_capture["exceptionChain"]] == [
            "LLMError", "RuntimeError"]
        worker.executed(task)  # Actual host error/cancellation classification boundary.
        assert worker.shutdown.is_set()
        event, = published
        assert event["type"] == "runtime.error" and event["failureSource"] == "execution"
        assert event["errorType"] == "RuntimeError"  # Not misreported as native type preservation.
        assert event["executionFailure"] is original_capture
        facts = worker_diagnostics.validate_command_failure_facts(event["failureFacts"])
        assert facts is not None and len(facts["errors"]) == 1
        assert facts["errors"][0]["class"] == "RuntimeError"
        assert provider.translated is original and original.__cause__ is provider.private_cause
        assert len(provider.counted) == len(provider.sent) == 1
        assert provider.counted[0] is provider.sent[0] and provider.sent[0].messages == []
        assert provider.cleanup_visits == 1
        assert registry.rows["diagnostic-child"]["status"] == "error"
        assert not registry.admissions and set(registry.sessions) == {"parent"}
        child = state["sessions"][-1]
        assert type(child) is AmplifierSession
        media = child.coordinator.get_capability("web.media.store")
        with closing(SessionMediaStore(media.root, media.session_id)):
            pass  # A new physical acquisition, not merely a cleared cleanup flag.
        if factory:
            binding = factory.latest("diagnostic-child")
            assert provider.cleanup_checked and len(provider.count_receipts) == 1
            with pytest.raises(factory.conflict_type, match="closed"):
                binding.check()
            reopened = factory.binding_type.resume(binding.directory, session_id=binding.session_id,
                                                    expected=binding.expected)
            try:
                saved = reopened.load_messages()
                assert saved and binding.last_receipt.verify()["operation_id"] == reopened.expected.operation_id
            finally:
                await reopened.aclose()
        else:
            saved, metadata = store.load("diagnostic-child")
            assert metadata["status"] == registry.rows["diagnostic-child"]["status"]
        assert await parent.coordinator.get("context").get_messages() == rows
        # Actual entry calls/records exist; none is a fabricated exception log.
        assert len(capture.calls) == len(capture.records) == 2
        assert all(record.name == "amplifier_core._session_exec" for record in capture.records)
        capture.assert_clean(sentinel)
        _assert_no_private((published, registry.snapshot(), saved), sentinel)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await parent.cleanup()
        if "capture" in locals():
            try:
                capture.assert_clean(sentinel)
            finally:
                capture.close()
