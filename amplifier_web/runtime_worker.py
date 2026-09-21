"""Private JSON-lines worker. Runs only inside the configured runtime environment."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid

try:
    from .runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from .message_delivery import contains_input
except ImportError:  # Executed directly inside the isolated runtime.
    from runtime_protocol import MAX_MESSAGE_BYTES, encode_message
    from message_delivery import contains_input

# Reserve a dedicated protocol descriptor before module imports. CLI displays and
# provider logs are routed to stderr, never mistaken for model/app events.
_PROTOCOL = None


def configure_protocol():
    global _PROTOCOL
    _PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def publish(data):
    _PROTOCOL.write(encode_message(data).decode("utf-8"))


class Worker:
    def __init__(self):
        self.session = self.runtime = self.execution = None
        self.approvals = {}
        self.operation_ids = set()
        self.operation_controls = 0
        self.bridges = {}
        self.start_task = None
        self.tasks = set()
        self.shutdown = asyncio.Event()
        self.telemetry = None
        self.controls = None
        self.naming = None
        self.workspace = None
        self.home = None
        self.shared_store = None
        self.shared_handle = None
        self.activation_gate = None
        self.activation = None
        self.parked = False
        self.parked_history_stamp = None
        self.parked_config_stamp = None
        self.config_inputs = ()
        self.command_lock = asyncio.Lock()
        self.start_config = None
        self.remounting = False
        self.context_bindings = {}
        self.context_inputs = []
        if __package__:
            from .ownership import WorkerOwnership
        else:
            from ownership import WorkerOwnership
        self.ownership = WorkerOwnership(self, lambda data: publish(data))

    async def ask(self, prompt, options):
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        options = options or ["allow", "deny"]
        self.approvals[identity] = (future, options)
        publish({"type": "approval.requested", "id": identity, "prompt": prompt, "options": options})
        try:
            decision = await future
            publish({"type": "approval.resolved", "id": identity, "decision": decision})
            return decision
        except asyncio.CancelledError:
            publish({"type": "approval.resolved", "id": identity, "decision": "expired"})
            raise
        finally:
            self.approvals.pop(identity, None)

    async def bridge(self, operation, args):
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.bridges[identity] = future
        publish({"op": "bridge", "id": identity, "operation": operation, "args": args})
        try:
            return await asyncio.wait_for(future, 60)
        finally:
            self.bridges.pop(identity, None)

    def observe(self, event):
        """Publish lifecycle metadata, never provider reasoning or tool inputs."""
        event = dict(event)
        if self.controls:
            from amplifier_web.scheduled_input import finish as finish_scheduled_input
            finish_scheduled_input(self.controls, event)
        if event.get('type') == 'generation.started':
            self.context_inputs = []
        if event.get('type') in {'input.delivered', 'steering.applied'} and event.get('input_id'):
            if event['input_id'] not in self.context_inputs:
                self.context_inputs = (self.context_inputs + [event['input_id']])[-8:]
        if self.naming:self.naming.observe(event)
        if self.telemetry:
            self.telemetry.lifecycle(event)
        loop = self.session.coordinator.get("orchestrator") if self.session else None
        if event.get("type", "").startswith("job.") and loop:
            job = loop.jobs.get(event.get("job_id"), {})
            arguments = (job.get("tool_call") or {}).get("arguments", {})
            if isinstance(arguments, dict) and isinstance(arguments.get("agent"), str):
                event["agent"] = arguments["agent"]
        publish(event)
        if event.get("type") == "generation.finished" and loop:
            count = sum(not job["task"].done() for job in loop.jobs.values())
            if count:
                publish({"type": "runtime.activity", "phase": "waiting-workers", "activeWorkers": count,
                    "detail": f"Waiting for {count} delegated task{'s' if count != 1 else ''} to report back."})
        elif event.get("type") == "input.delivered":
            publish({"type": "runtime.activity", "phase": "model", "detail": "Preparing a model response."})

    def install_activity(self, coordinator):
        from amplifier_core import HookResult
        if coordinator.get_capability("web.activity"):
            # Live provider edits may mount a new immutable instance after the
            # hooks were installed. Preserve its admission wrapper as well.
            if self.telemetry:
                providers = coordinator.get("providers") or {}
                for name, provider in list(providers.items()):
                    providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider)
            return
        coordinator.register_capability("web.activity", True)
        async def observe_operation(event):
            identity = (coordinator.session_id, event.get("operationId"))
            if event.get("phase") == "started":
                self.operation_ids.add(identity)
            try:
                from amplifier_web.computation import KERNEL_TRANSPORT
                if event.get("source") == "tool-bash" and KERNEL_TRANSPORT.get():
                    return {"capturedBy": "computation"}
                return await self.bridge("operations.observe", {
                    "runtimeSessionId": coordinator.session_id, "event": event})
            finally:
                if event.get("phase") == "finished":
                    self.operation_ids.discard(identity)
                    task = asyncio.create_task(self.park())
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        coordinator.register_capability("operations.observe", observe_operation)
        async def admit_questions(question_ids):
            return await self.bridge("questions.admit", {"questionIds": question_ids})
        coordinator.register_capability("questions.admit", admit_questions)
        def public_stream():
            from amplifier_web.execution_events import CALL_PURPOSE
            return not CALL_PURPOSE.get()
        coordinator.register_capability("live.public_stream", public_stream)
        if self.telemetry:
            coordinator.register_capability('web.provider_observe', lambda provider: self.telemetry.instrument_provider(coordinator.session_id,provider))
            coordinator.register_capability('web.provider_call', lambda provider, request, invoke, **kwargs: self.telemetry.provider_call(coordinator.session_id, provider, request, invoke, **kwargs))
            registry = coordinator.get_capability("live.children")
            if registry and coordinator.session_id in registry.rows:
                self.telemetry.lifecycle({"type":"child.updated", **registry.rows[coordinator.session_id]})
            providers = coordinator.get("providers") or {}
            for name, provider in list(providers.items()):
                providers[name] = self.telemetry.instrument_provider(coordinator.session_id, provider)
        async def activity(event, data):
            identity = coordinator.session_id
            if data.get("session_id", identity) != identity:
                return HookResult()
            if self.telemetry:
                self.telemetry.hook(identity, event, data)
            from amplifier_web.execution_events import CALL_PURPOSE
            if CALL_PURPOSE.get() or event.startswith("llm:"):
                # Naming calls retain their measured usage, but do not own the
                # conversation's busy indicator or a delegated worker's state.
                return HookResult()
            phase = "model"
            retry = {}
            detail = "Waiting for the configured model to respond."
            if event == "context:compaction_started":
                phase, detail = "compacting", "Making room in the conversation. You can keep sending updates."
            elif event == "context:compaction_finished":
                detail = "Conversation context prepared; continuing work." if data.get("outcome") == "completed" else "Context preparation " + str(data.get("outcome", "ended")) + "."
            elif event == "provider:retry":
                phase = "retrying"
                attempt, maximum = data.get("attempt"), data.get("max_retries")
                suffix = f" ({attempt} of {maximum})" if isinstance(attempt, int) and isinstance(maximum, int) else ""
                if suffix:
                    retry = {"retryAttempt": attempt, "retryMax": maximum}
                detail = "The model request was interrupted; retrying" + suffix + "."
            elif event.startswith("tool:"):
                phase = "tools"
                tool = data.get("tool_name")
                tool = tool[:100] if isinstance(tool, str) else "tool"
                detail = ("Running " if event == "tool:pre" else "Finished " if event == "tool:post" else "An error occurred in ") + tool + "."
            if identity == self.runtime.session_id:
                publish({"type": "runtime.activity", "phase": phase, "detail": detail, **retry})
            else:
                registry = coordinator.get_capability("live.children")
                row = registry.rows.get(identity, {}) if registry else {}
                publish({"type": "worker.activity", "workerId": identity, "phase": phase,
                    "detail": detail, "name": row.get("agent", "Worker"), "callId": row.get("callId"), "time": time.time(), **retry})
            return HookResult()
        for event in ("provider:request", "provider:retry", "tool:pre", "tool:post", "tool:error", "llm:request", "llm:response", "context:compaction_started", "context:compaction_finished"):
            coordinator.hooks.register(event, activity, name="amplifier-web-activity-" + event)

    async def preparation_progress(self, directory):
        while True:
            if (directory / "live-mount-plan.json").exists():
                publish({"type": "runtime.progress", "phase": "session-mount",
                    "detail": "Loading the configured providers, tools, and worker agents."})
                return
            await asyncio.sleep(2)

    async def start(self, config, *, raise_errors=False, recover_bundle=True, resolved_root=None):
        progress = None
        try:
            publish({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Loading your bundle and app behaviors; downloading or installing modules as needed."})
            # Module activators run uv pip separately from the host project.
            # Preserve an explicitly supplied user override if there is one.
            os.environ.setdefault("UV_OVERRIDE", str(Path(__file__).with_name("runtime_deps") / "compatibility.txt"))
            # Load only app code, never the outer host's site-packages metadata.
            if __package__:
                from .runtime_bootstrap import bootstrap_app_package
            else:
                from runtime_bootstrap import bootstrap_app_package
            bootstrap_app_package()
            from amplifier_web.host.config import app_home
            from amplifier_web.host.session import prepare_manager
            from amplifier_web.execution_events import ExecutionEvents
            from amplifier_web.runtime_controls import RuntimeControls
            from amplifier_web.shared_state import ActivationGate, configuration_stamp
            from amplifier_module_loop_live.runtime import Runtime
            self.home = app_home()
            from amplifier_web.runtime_qualification import active_install_overrides
            install_overrides = active_install_overrides(self.home, os.environ.get("UV_OVERRIDE"))
            if install_overrides is not None:
                os.environ["UV_OVERRIDE"] = str(install_overrides)
            self.runtime = Runtime(session_id=config["id"], observer=self.observe, max_input_chars=200_000)
            self.telemetry = ExecutionEvents(config["id"], publish)
            workspace = Path(config.get("workspace") or config.get("workingDirectory") or os.getcwd()).expanduser().resolve(strict=True)
            self.workspace = workspace
            self.start_config = dict(config)
            # Execution locking lives in the isolated runtime. The outer host
            # uses the same Foundation native-history reader for navigation.
            if self.shared_store is None:
                from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
                self.shared_store = SharedSessionStore(workspace, config["id"])
                self.shared_store_stamp = file_stamp
                self.activation_gate = ActivationGate()
            if self.shared_handle is None:
                self.shared_handle = await asyncio.to_thread(
                    self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
            if recover_bundle:
                from amplifier_web.bundle_selection import BundleTransaction
                BundleTransaction(self.home, workspace, config["id"]).restore()
            from amplifier_web.history_revision import recover_pending
            recover_pending(self.home, workspace, config['id'])
            self.activation = self.activation_gate.activate()
            self.runtime.capture_activation = self.activation_gate.current
            # Always allow loading the saved transcript when one exists; the
            # adapter marks interrupted jobs as evidence, never replays them.
            report_directory = self.home / "runtime-reports" / config["id"]
            progress = asyncio.create_task(self.preparation_progress(report_directory))
            self.session, self.runtime, report = await prepare_manager(workspace,
                runtime=self.runtime, bundle=config.get("bundle") or None, ask=self.ask,
                resume=True, application_host="Amplifier Web", selection=config.get("selection") or None,
                report_dir=report_directory, shared_handle=self.shared_handle,
                shared_handle_getter=lambda: self.shared_handle,
                write_guard=self.activation_gate.check_current, resolved_root=resolved_root,
                execution_workspace=config.get("workingDirectory"), install_overrides=install_overrides)
            self.config_inputs = tuple(report.get("config_inputs", ()))
            from amplifier_web.attachments import encode
            self.session.coordinator.register_capability('live.attachments.encode',encode)
            self.controls = RuntimeControls(self.session, self.runtime, self.telemetry)
            self.controls.capacity.admit = lambda row: self.bridge("capacity.admit", {"call": row})
            self.telemetry.admission_guard = self.controls.capacity.guard
            # Preserve app controls on native mounts. A legacy common snapshot
            # retains its previous restoration policy during one-time recovery.
            if report.get("history_source") != "legacy-checkpoint":
                await self.controls.restore()
            self.controls.persist()
            if config.get("forkContext") and not report.get("resumed"):
                # Fork conversational context without tool receipts or runtime
                # ownership. Source operations must never be replayed.
                from types import SimpleNamespace
                messages = [{"role": row["role"], "content": encode(SimpleNamespace(
                    text=row.get("text", ""), attachments=row["attachments"]))
                    if row["role"] == "user" and row.get("attachments") else row.get("text", "")}
                    for row in config.get("messages", []) if row.get("role") in {"user", "assistant"} and (row.get("text") or row.get("attachments"))]
                if messages:
                    await self.session.coordinator.get("context").set_messages(messages)
                report["fork_context_messages"] = len(messages)
            host = self
            from amplifier_web.app_guidance import install_app_access
            def surface_bridge(coordinator):
                # Workers inherit the originating inputs at assignment, never
                # another client's later input or another coordinator's receipts.
                assigned = None if coordinator is host.session.coordinator else list(host.context_inputs)
                watched = set()
                async def bridge(operation, args):
                    ids = host.context_inputs if assigned is None else assigned
                    if operation.startswith('context.'):
                        bindings = [host.context_bindings[i] for i in ids if i in host.context_bindings]
                        if assigned is not None:
                            if operation == 'context.read':
                                watched.add(args.get('surfaceId'))
                            bindings = [{**b, 'targets': [t for t in b.get('targets', []) if t['surfaceId'] in watched]} for b in bindings]
                            if not bindings:
                                bindings = [{'clientId': 'detached-worker', 'targets': []}]
                        args = {**args, '_contextInputs': ids,
                                '_contextBindings': bindings}
                    return await host.bridge(operation, args)
                return bridge
            await install_app_access(self.session.coordinator, surface_bridge(self.session.coordinator))
            original_host = self.session.coordinator.get_capability("live.host")
            class ObservedHost:
                def __getattr__(self, name):
                    return getattr(original_host, name)
                async def prepare_execution(self, loop, coordinator, providers):
                    result = await original_host.prepare_execution(loop, coordinator, providers)
                    if coordinator:
                        host.install_activity(coordinator)
                        await install_app_access(coordinator, surface_bridge(coordinator))
                        from amplifier_web.host.session import SelectedProvider
                        transform = coordinator.get_capability('web.provider_transform')
                        if isinstance(getattr(loop,'root_provider',None), SelectedProvider):
                            loop.root_provider.execution_adapter = transform
                        runtime, selected, scope = result
                        return runtime, {name: transform(value) for name, value in selected.items()}, scope
                    return result
            # loop-live propagates this host through its existing ContextVar to
            # delegated sessions. Observe their public lifecycle without changing
            # spawning, provider routing, approvals, or execution ownership.
            self.session.coordinator.register_capability("live.host", ObservedHost())
            self.session.coordinator.register_capability("web.activity.install", self.install_activity)
            self.session.coordinator.register_capability("live.activation", self.activation_gate)
            self.session.coordinator.register_capability("live.park", self.park)
            from amplifier_web.host.naming import LiveSessionNaming
            completed=[identity for event in config.get('generations',[]) if event.get('event')=='generation.finished' for identity in event.get('input_ids',[]) if identity in {t['id'] for t in config.get('execution',{}).get('turns',[])}]
            self.naming=LiveSessionNaming(self.session.coordinator,self.home,publish,completed)
            await self.ownership.register()
            self.execution = asyncio.create_task(self.session.execute(""))
            self.execution.add_done_callback(self.executed)
            self.parked_history_stamp = self.history_stamp()
            self.parked_config_stamp = configuration_stamp(
                workspace, config["id"], self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
            report["tools"] = list(self.session.coordinator.get("tools"))
            # Keep absolute module sources and full mount plans on disk. The UI
            # gets a compact capability report, not credentials or config blobs.
            public = {key: report.get(key) for key in ("bundle", "root_bundle", "workspace", "session_id", "resumed", "providers",
                "tools", "agents", "provider_choices", "selection", "effective_selection", "steering", "capabilities", "fork_context_messages", "standalone", "settings_file")}
            publish({"type": "runtime.ready", "report": public})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if raise_errors:
                raise
            error = {"type": "runtime.error", "error": f"{type(exc).__name__}: {exc}"}
            from amplifier_web.module_failures import ConfiguredModuleError
            if isinstance(exc, ConfiguredModuleError):
                error.update(code="module_load_failed", moduleFailures=exc.failures)
            if type(exc).__name__ == "SessionBusyError":
                error.update(code="session_busy", owner=getattr(exc, "owner", None))
            publish(error)
            self.shutdown.set()
        finally:
            if progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)

    async def park(self, *, activation=None):
        """Checkpoint then relinquish only a settled manager activation."""

        # Naming may itself await an approval/bridge response. Do not hold the
        # command lock while waiting for it; admission is rechecked below.
        if self.naming and self.naming.pending and not self.naming.pending.done():
            await self.naming.pending
        async with self.command_lock:
            if self.ownership.yielding or self.parked or self.shared_handle is None:
                return
            loop = self.session.coordinator.get("orchestrator")
            if (self.approvals or self.bridges or self.operation_ids or self.operation_controls or self.runtime.queued_inputs
                    or not self.runtime.inbox.empty() or self.runtime.generation
                    or (loop and (loop.pending or loop._active_jobs()))):
                return
            activation = activation or self.activation
            self.activation_gate.check(activation)
            token = self.activation_gate.bind(activation)
            try:
                checkpoint = self.session.coordinator.get_capability("live.checkpoint")
                if checkpoint:
                    await checkpoint("completed")
                self.parked_history_stamp = self.history_stamp()
                from amplifier_web.shared_state import configuration_stamp
                self.parked_config_stamp = configuration_stamp(
                    self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                    extra_paths=self.config_inputs)
                self.activation_gate.release(activation)
                await asyncio.to_thread(self.shared_handle.release)
                self.shared_handle = None
                self.parked = True
            finally:
                self.activation_gate.reset(token)
        publish({"type": "runtime.parked", "session_id": self.runtime.session_id})

    def history_stamp(self):
        """Cheap native invalidation; never parse events or consult checkpoints."""
        from amplifier_web.session_files import sessions_dir
        directory = sessions_dir(self.workspace) / self.runtime.session_id
        return tuple(self.shared_store_stamp(directory / name) for name in
                     ("transcript.jsonl", "transcript.jsonl.backup", "metadata.json", "metadata.json.backup"))

    async def acquire_for_mutation(self):
        """Acquire after a parked loop wakes, before it can accept an input."""

        if not self.parked:
            return
        handle = await asyncio.to_thread(
            self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
        try:
            history_stamp = self.history_stamp()
            from amplifier_web.shared_state import configuration_stamp
            config_stamp = configuration_stamp(
                self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
        except BaseException:
            await asyncio.to_thread(handle.release)
            raise
        if (self.parked_history_stamp and any(self.parked_history_stamp[:2])
                and not any(history_stamp[:2])):
            await asyncio.to_thread(handle.release)
            raise RuntimeError(
                "The native session transcript and its backup disappeared while this runtime was "
                "parked. Its previous context will not be replaced with legacy history.")
        if history_stamp != self.parked_history_stamp or config_stamp != self.parked_config_stamp:
            self.shared_handle = handle
            self.parked = False
            await self.remount()
            return
        self.shared_handle = handle
        self.activation = self.activation_gate.activate()
        self.parked = False
        await self.ownership.register()
        publish({"type": "runtime.reactivated", "session_id": self.runtime.session_id,
                 "reused": True})

    async def remount(self):
        """Replace stale mounted state after lock+stamp validation."""

        self.remounting = True
        try:
            if self.execution and not self.execution.done():
                self.execution.cancel()
                await asyncio.gather(self.execution, return_exceptions=True)
            if self.controls:
                await self.controls.close()
            if self.session:
                await self.session.cleanup()
            self.session = self.controls = self.naming = self.execution = None
            await self.start(self.start_config)
        finally:
            self.remounting = False

    def bind_activation(self):
        """Bind the current capability to a command task, never to a global writer."""

        if self.parked or self.shared_handle is None:
            raise RuntimeError("The session is parked and has no active shared writer.")
        return self.activation_gate.bind(self.activation)

    def executed(self, task):
        if self.remounting or self.ownership.yielding:
            return
        if not task.cancelled():
            error = task.exception()
            if error:
                publish({"type": "runtime.error", "error": f"{type(error).__name__}: {error}. Work was not replayed."})
        self.shutdown.set()

    async def command(self, data):
        """Serialize admission with parking and bind a per-work write token."""

        op = data.get("op")
        if op == "park":
            await self.park()
            publish({"op": "reply", "id": data.get("id"), "result": {"parked": self.parked}})
            return
        if op == "retire":
            async with self.command_lock:
                loop = self.session.coordinator.get("orchestrator") if self.session else None
                settled = bool(self.parked and not self.ownership.yielding
                    and not self.approvals and not self.bridges and not self.operation_ids and not self.operation_controls and not self.remounting
                    and not self.runtime.queued_inputs and self.runtime.inbox.empty()
                    and not self.runtime.generation
                    and not (self.naming and self.naming.pending and not self.naming.pending.done())
                    and not (loop and (loop.pending or loop._active_jobs())))
                publish({"op": "reply", "id": data.get("id"), "result": {"retired": settled}})
                if settled:
                    self.shutdown.set()
            return
        if op not in {"send", "retry", "resume", "control", "worker.steer", "worker.stop", "worker.message", "approval"}:
            await self._command_serial(data)
            return
        try:
            async with self.command_lock:
                if self.ownership.yielding:
                    from amplifier_foundation.session import SessionBusyError
                    raise SessionBusyError(self.shared_handle.owner if self.shared_handle else None)
                await self.acquire_for_mutation()
                token = self.bind_activation()
                detached_cancel = op == "control" and (data.get("operation", "").startswith(("operations.", "kernels.")))
                if detached_cancel:
                    self.operation_controls += 1
                else:
                    try:
                        await self._command_serial(data)
                    finally:
                        self.activation_gate.reset(token)
            if detached_cancel:
                try:
                    await self._command_serial(data)
                finally:
                    self.operation_controls -= 1
                    self.activation_gate.reset(token)
            if op in {"control", "retry"}:
                # A control-only action or duplicate retry does not wake the inbox.
                # It must therefore schedule its own settled release.
                await self.park(activation=self.activation)
        except Exception as exc:
            reply = {"op": "reply", "id": data.get("id"),
                     "error": f"{type(exc).__name__}: {exc}"}
            if type(exc).__name__ == "SessionBusyError":
                reply["code"] = "session_busy"
                reply["owner"] = getattr(exc, "owner", None)
            publish(reply)

    async def _command_serial(self, data):
        identity = data.get("id")
        try:
            op = data.get("op")
            if op == "bridge.result":
                future = self.bridges.get(identity)
                if future and not future.done():
                    if data.get("error"):
                        future.set_exception(RuntimeError(data["error"]))
                    else:
                        future.set_result(data.get("result"))
                return
            if op == "resume":
                if not self.session or not self.execution or self.execution.done():
                    raise RuntimeError('The session has not finished loading successfully.')
                result = {"accepted": True}
            elif op == "approval":
                future, options = self.approvals[data["approval_id"]]
                if future.done() or data["decision"] not in options:
                    raise ValueError("Approval expired or decision is not offered")
                future.set_result(data["decision"])
                result = {"accepted": True}
            elif op == "start":
                if self.start_task:
                    raise RuntimeError("Already started")
                self.start_task = asyncio.create_task(self.start(data["session"]))
                return
            elif op == "stop":
                if self.runtime and not self.runtime.closed:
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input("stop"))
                else:
                    self.shutdown.set()
                return
            elif op == "dependencies":
                if __package__:
                    from .artifact_runtime import discover
                else:
                    from artifact_runtime import discover
                result = await discover('worker')
            elif not self.session or not self.execution:
                raise RuntimeError("Session is not ready")
            elif op in {"delivery", "retry"} and (
                data['input_id'] in self.runtime.accepted or
                contains_input(
                    await self.session.coordinator.get('context').get_messages(), data['input_id'])
            ):
                result = {"accepted": True, "delivery": "accepted", "duplicate": True}
            elif op == "delivery":
                result = {"delivery": "unknown"}
            elif op in {"send", "retry"}:
                from amplifier_module_loop_live.runtime import Input
                self.context_bindings[data['input_id']] = data.get('context_binding', {'clientId': None, 'targets': []})
                self.context_bindings = dict(list(self.context_bindings.items())[-64:])
                input_id = await self.runtime.submit(Input(
                    "user", data["text"], id=data["input_id"],
                    attachments=tuple(data.get("attachments", [])),
                    activation=self.activation))
                result = {"accepted": True, "inputId": input_id}
            elif op == "control":
                arguments = data.get("arguments", {})
                if data["operation"] == "schedule.submit":
                    from amplifier_web.scheduled_input import admit
                    result = await admit(self.controls, self.runtime, arguments, self.activation)
                elif data["operation"] == "session.naming":
                    self.controls.require_idle()
                    if not self.naming:
                        raise ValueError('Automatic naming is unavailable for this conversation.')
                    result = await self.naming.suggest()
                elif data["operation"] == "bundle.preview":
                    from amplifier_web.bundle_selection import preview
                    self.controls.require_idle()
                    self.bundle_preview = {**await asyncio.wait_for(preview(self.controls, self.workspace, arguments['bundle']), 150),
                                           'previewId': str(uuid.uuid4())}
                    result = self.bundle_preview
                elif data["operation"] == "history.edit":
                    if self.approvals or self.bridges or not self.runtime.inbox.empty():
                        raise ValueError('Finish pending interactions before editing history.')
                    from amplifier_web.history_revision import rewind
                    result = await rewind(self.controls, arguments)
                    publish({'type': 'history.revised', **result})
                    from amplifier_module_loop_live.runtime import Input
                    await self.runtime.submit(Input('user', arguments['text'], id=arguments['operationId'],
                        attachments=tuple(arguments.get('attachments', [])), activation=self.activation))
                elif data["operation"] == "bundle.switch":
                    from amplifier_web.bundle_selection import switch
                    result = await switch(self, arguments)
                else:
                    result = await self.controls.perform(data["operation"], arguments)
            elif op in {"worker.steer", "worker.stop", "worker.message"}:
                children = self.session.coordinator.get_capability("live.children")
                wid = data["worker_id"]
                if children and wid in children.rows:
                    result = await children.control(wid, "steer" if op == "worker.steer" else "message" if op == "worker.message" else "cancel", data.get("text", ""), input_id=data.get("input_id"))
                elif op == "worker.stop":
                    from amplifier_module_loop_live.runtime import Input
                    loop = self.session.coordinator.get("orchestrator")
                    job = loop.jobs.get(wid)
                    if job is None and children and wid in children.rows:
                        call = children.rows[wid].get("callId")
                        wid = next((k for k,v in loop.jobs.items() if v.get("call_id") == call), None)
                        job = loop.jobs.get(wid)
                    if not job or job["task"].done():
                        raise ValueError("Worker is no longer active")
                    await self.runtime.submit(Input("cancel_job", target=wid, activation=self.activation))
                    result = {"accepted": True, "completed": False}
                else:
                    raise ValueError("This finite worker cannot receive messages; ask the main session to revise its work")
            else:
                raise ValueError("Unknown runtime operation")
            if identity:
                publish({"op": "reply", "id": identity, "result": result})
        except Exception as exc:
            publish({"op": "reply", "id": identity, "error": f"{type(exc).__name__}: {exc}"})

    async def read(self):
        reader = asyncio.StreamReader(limit=MAX_MESSAGE_BYTES)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
        try:
            while line := await reader.readline():
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                except ValueError:
                    continue
                task = asyncio.create_task(self.command(data))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            publish({'type':'runtime.error','error':f'Amplifier worker communication failed ({type(exc).__name__}). A message could not be read; work was not replayed.'})
        finally:
            transport.close()
            self.shutdown.set()

    async def run(self):
        read = asyncio.create_task(self.read())
        try:
            await self.shutdown.wait()
        finally:
            if self.ownership.registration:
                await self.ownership.registration.close()
            for task in [read, self.start_task, self.execution, *self.tasks]:
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in [read, self.start_task, self.execution, *self.tasks] if t), return_exceptions=True)
            if self.controls:
                await self.controls.close()
            if self.session:
                await self.session.cleanup()
            if self.shared_handle is not None:
                try:
                    await asyncio.to_thread(self.shared_handle.release)
                finally:
                    self.shared_handle = None


if __name__ == "__main__":
    configure_protocol()
    asyncio.run(Worker().run())
