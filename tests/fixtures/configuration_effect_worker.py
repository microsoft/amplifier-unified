"""Pipe fixture: real Worker command/bridge and RuntimeControls in a subprocess.

Only mount/lease setup is synthetic. No provider call, module installation,
HTTP service, or native history is required. This does not qualify full mounting.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_web.runtime_worker import Worker, configure_protocol, publish
from amplifier_web.runtime_controls import RuntimeControls, override_path
from amplifier_web import runtime_worker

withheld = set()
def filtered_publish(value):
    if value.get("op") == "reply" and value.get("id") in withheld:
        return
    publish(value)
runtime_worker.publish = filtered_publish


def plan():
    return {"session": {"orchestrator": {"module": "loop-live"},
                       "context": {"module": "context-simple"}},
            "providers": [{"module": "provider-fixture"}],
            "tools": [{"module": "tool-fixture", "config": {"limit": 1}}]}


class FixtureWorker(Worker):
    async def acquire_for_mutation(self):
        pass  # No native shared history/writer in this transport fixture.

    def bind_activation(self):
        return None

    async def park(self, **kwargs):
        pass  # Keep the process available for deliberate duplicate delivery.

    async def _command_serial(self, data):
        if data["op"] == "start":
            sid = data["session"]["id"]
            capabilities = {}
            stored = override_path(sid)
            config = json.loads(stored.read_text()) if stored.exists() else plan()
            loop = SimpleNamespace(root_provider=None, jobs={})
            coordinator = SimpleNamespace(config=config, session_state={},
                get=lambda name: {"orchestrator": loop, "providers": {}}.get(name),
                get_capability=capabilities.get,
                register_capability=lambda key, value: capabilities.update({key: value}))
            self.session = SimpleNamespace(session_id=sid, coordinator=coordinator)
            self.runtime = SimpleNamespace(generation=None, queued_inputs=0)
            self.execution = asyncio.create_task(self.shutdown.wait())
            self.controls = RuntimeControls(self.session, self.runtime)
            self.activation_gate = SimpleNamespace(reset=lambda token: None)
            self.controls.state_path().parent.mkdir(parents=True, exist_ok=True)
            if not self.controls.state_path().exists():
                self.controls.state_path().write_text(json.dumps({"configurator": {"disabled": {"tools": ["tool-fixture"]}}}))
            publish({"type": "runtime.ready", "report": {},
                     "workerIdentity": {"pid": os.getpid(), "sessionId": sid}})
        elif data["op"] == "fixture.busy":
            self.runtime.queued_inputs = data["count"]
            publish({"op": "reply", "id": data["id"], "result": {"queued": data["count"]}})
        elif data["op"] == "fixture.partial":
            from amplifier_web import runtime_controls
            original = runtime_controls.write_private
            def partial(path, text):
                if path == self.controls.state_path():
                    raise OSError("Synthetic second-write failure")
                original(path, text)
            runtime_controls.write_private = partial
            publish({"op": "reply", "id": data["id"], "result": {"injected": True}})
        elif data["op"] == "fixture.lose_reply":
            self.lose_reply = True
            publish({"op": "reply", "id": data["id"], "result": {"injected": True}})
        elif data["op"] == "control" and data["operation"] == "configuration.providers":
            publish({"op": "reply", "id": data["id"], "result": {"providers": []}})
        else:
            if (data["op"] == "control" and data["operation"] == "configuration.apply"
                    and getattr(self, "lose_reply", False)):
                withheld.add(data["id"])
                self.lose_reply = False
            await super()._command_serial(data)


async def main():
    worker = FixtureWorker()
    try:
        # Independent read loop is necessary: native apply awaits the host bridge.
        while line := await asyncio.to_thread(sys.stdin.readline):
            data = json.loads(line)
            if data["op"] == "stop":
                worker.stop_revision += 1
                break
            task = asyncio.create_task(worker.command(data))
            worker.tasks.add(task)
            task.add_done_callback(worker.tasks.discard)
    finally:
        worker.shutdown.set()
        if worker.execution:
            await worker.execution
        for task in tuple(worker.tasks):
            task.cancel()
        await asyncio.gather(*tuple(worker.tasks), return_exceptions=True)
        if worker.controls:
            await worker.controls.close()


if __name__ == "__main__":
    configure_protocol()
    asyncio.run(main())
