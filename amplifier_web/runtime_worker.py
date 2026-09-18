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
except ImportError:  # Executed directly inside the isolated runtime.
    from runtime_protocol import MAX_MESSAGE_BYTES, encode_message

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
        self.parked_checkpoint_stamp = None
        self.parked_config_stamp = None
        self.config_inputs = ()
        self.command_lock = asyncio.Lock()
        self.start_config = None
        self.remounting = False

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
            return
        coordinator.register_capability("web.activity", True)
        if self.telemetry:
            registry = coordinator.get_capability("live.children")
            if registry and coordinator.session_id in registry.rows:
                self.telemetry.lifecycle({"type":"child.updated", **registry.rows[coordinator.session_id]})
            for provider in (coordinator.get("providers") or {}).values():
                self.telemetry.instrument_provider(coordinator.session_id, provider)
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
            if event == "provider:retry":
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
        for event in ("provider:request", "provider:retry", "tool:pre", "tool:post", "tool:error", "llm:request", "llm:response"):
            coordinator.hooks.register(event, activity, name="amplifier-web-activity-" + event)

    async def preparation_progress(self, directory):
        while True:
            if (directory / "live-mount-plan.json").exists():
                publish({"type": "runtime.progress", "phase": "session-mount",
                    "detail": "Loading the configured providers, tools, and worker agents."})
                return
            await asyncio.sleep(2)

    async def start(self, config):
        progress = None
        try:
            publish({"type": "runtime.progress", "phase": "bundle-preparation",
                "detail": "Loading your bundle and app behaviors; downloading or installing modules as needed."})
            # Module activators run uv pip separately from the host project.
            # Preserve an explicitly supplied user override if there is one.
            os.environ.setdefault("UV_OVERRIDE", str(Path(__file__).with_name("runtime_deps") / "compatibility.txt"))
            # The worker runs in a separate dependency environment; make the
            # installed application package available without importing its UI.
            package_root = str(Path(__file__).resolve().parent.parent)
            if package_root not in sys.path:
                sys.path.insert(0, package_root)
            from amplifier_web.host.config import app_home
            from amplifier_web.host.session import prepare_manager
            from amplifier_web.execution_events import ExecutionEvents
            from amplifier_web.runtime_controls import RuntimeControls
            from amplifier_module_loop_live.runtime import Runtime
            self.home = app_home()
            self.runtime = Runtime(session_id=config["id"], observer=self.observe, max_input_chars=200_000)
            self.telemetry = ExecutionEvents(config["id"], publish)
            workspace = Path(config.get("workspace") or config.get("workingDirectory") or os.getcwd()).expanduser().resolve(strict=True)
            self.workspace = workspace
            self.start_config = dict(config)
            # This import happens only in the runtime subprocess.  The outer
            # HTTP application intentionally stays independent of Foundation.
            if self.shared_store is None:
                from amplifier_foundation.session.shared_state import SharedSessionStore, file_stamp
                from amplifier_web.shared_state import ActivationGate, configuration_stamp
                self.shared_store = SharedSessionStore(workspace, config["id"])
                self.shared_store_stamp = file_stamp
                self.activation_gate = ActivationGate()
            if self.shared_handle is None:
                self.shared_handle = await asyncio.to_thread(
                    self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
            self.activation = self.activation_gate.activate()
            shared_snapshot = await asyncio.to_thread(self.shared_handle.read)
            # Always allow loading the saved transcript when one exists; the
            # adapter marks interrupted jobs as evidence, never replays them.
            report_directory = self.home / "runtime-reports" / config["id"]
            progress = asyncio.create_task(self.preparation_progress(report_directory))
            self.session, self.runtime, report = await prepare_manager(workspace,
                runtime=self.runtime, bundle=config.get("bundle") or None, ask=self.ask,
                resume=True, application_host="Amplifier Web", selection=config.get("selection") or None,
                report_dir=report_directory, shared_handle=self.shared_handle,
                shared_snapshot=shared_snapshot, write_guard=self.activation_gate.check_current)
            self.config_inputs = tuple(report.get("config_inputs", ()))
            from amplifier_web.attachments import encode
            self.session.coordinator.register_capability('live.attachments.encode',encode)
            self.controls = RuntimeControls(self.session, self.runtime, self.telemetry)
            # The common checkpoint owns shared-root state.  Local controls are
            # a native projection and must not override a CLI/web shared mount.
            if shared_snapshot is None:
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
            await install_app_access(self.session.coordinator, host.bridge)
            original_host = self.session.coordinator.get_capability("live.host")
            class ObservedHost:
                def __getattr__(self, name):
                    return getattr(original_host, name)
                async def prepare_execution(self, loop, coordinator, providers):
                    result = await original_host.prepare_execution(loop, coordinator, providers)
                    if coordinator:
                        host.install_activity(coordinator)
                        await install_app_access(coordinator, host.bridge)
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
            self.execution = asyncio.create_task(self.session.execute(""))
            self.execution.add_done_callback(self.executed)
            self.parked_checkpoint_stamp = self.shared_handle.stamp()
            self.parked_config_stamp = configuration_stamp(
                workspace, config["id"], self.home, self.shared_store_stamp,
                extra_paths=self.config_inputs)
            report["tools"] = list(self.session.coordinator.get("tools"))
            # Keep absolute module sources and full mount plans on disk. The UI
            # gets a compact capability report, not credentials or config blobs.
            public = {key: report.get(key) for key in ("bundle", "workspace", "session_id", "resumed", "providers",
                "tools", "agents", "provider_choices", "selection", "effective_selection", "steering", "capabilities", "fork_context_messages", "standalone", "settings_file")}
            publish({"type": "runtime.ready", "report": public})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            publish({"type": "runtime.error", "error": f"{type(exc).__name__}: {exc}"})
            self.shutdown.set()
        finally:
            if progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)

    async def park(self, *, activation=None):
        """Checkpoint then relinquish only a settled manager activation."""

        async with self.command_lock:
            if self.parked or self.shared_handle is None:
                return
            activation = activation or self.activation
            self.activation_gate.check(activation)
            if self.naming and self.naming.pending and not self.naming.pending.done():
                await self.naming.pending
            token = self.activation_gate.bind(activation)
            try:
                checkpoint = self.session.coordinator.get_capability("live.checkpoint")
                if checkpoint:
                    await checkpoint("completed")
                self.parked_checkpoint_stamp = self.shared_handle.stamp()
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

    async def acquire_for_mutation(self):
        """Acquire after a parked loop wakes, before it can accept an input."""

        if not self.parked:
            return
        handle = await asyncio.to_thread(
            self.shared_store.acquire, app="amplifier-unified", pid=os.getpid())
        checkpoint_stamp = handle.stamp()
        from amplifier_web.shared_state import configuration_stamp
        config_stamp = configuration_stamp(
            self.workspace, self.runtime.session_id, self.home, self.shared_store_stamp,
            extra_paths=self.config_inputs)
        if self.parked_checkpoint_stamp is not None and checkpoint_stamp is None:
            await asyncio.to_thread(handle.release)
            raise RuntimeError(
                "The shared session checkpoint disappeared while this runtime was "
                "parked. Its previous context will not be replaced with local history.")
        if checkpoint_stamp != self.parked_checkpoint_stamp or config_stamp != self.parked_config_stamp:
            self.shared_handle = handle
            self.parked = False
            await self.remount()
            return
        self.shared_handle = handle
        self.activation = self.activation_gate.activate()
        self.parked = False
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
        if self.remounting:
            return
        if not task.cancelled():
            error = task.exception()
            if error:
                publish({"type": "runtime.error", "error": f"{type(error).__name__}: {error}. Work was not replayed."})
        self.shutdown.set()

    async def command(self, data):
        """Serialize admission with parking and bind a per-work write token."""

        op = data.get("op")
        if op not in {"send", "control", "worker.steer", "worker.stop", "approval"}:
            await self._command_serial(data)
            return
        async with self.command_lock:
            await self.acquire_for_mutation()
            token = self.bind_activation()
            try:
                await self._command_serial(data)
            finally:
                self.activation_gate.reset(token)

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
            if op == "approval":
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
            elif not self.session or not self.execution:
                raise RuntimeError("Session is not ready")
            elif op == "send":
                from amplifier_module_loop_live.runtime import Input
                input_id = await self.runtime.submit(Input(
                    "user", data["text"], id=data["input_id"],
                    attachments=tuple(data.get("attachments", [])),
                    activation=self.activation))
                result = {"accepted": True, "inputId": input_id}
            elif op == "control":
                result = await self.controls.perform(data["operation"], data.get("arguments", {}))
            elif op in {"worker.steer", "worker.stop"}:
                children = self.session.coordinator.get_capability("live.children")
                wid = data["worker_id"]
                if children and wid in children.rows:
                    result = await children.control(wid, "steer" if op == "worker.steer" else "cancel", data.get("text", ""))
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
