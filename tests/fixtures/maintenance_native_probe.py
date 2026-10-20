"""Actual app/worker/Core/loop/delegate pause acceptance with offline effects.

Run only in an owned isolated app root. Providers and one counted tool are
deterministic; admission, checkpoints, worker ownership and restart are product.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[2]
INSTALLED_PACKAGE = os.environ.get('MAINTENANCE_PROBE_INSTALLED') == '1'
if not INSTALLED_PACKAGE:
    sys.path.insert(0, str(SOURCE))

PROVIDER = '''
import asyncio, json, os, uuid
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse
__amplifier_module_type__ = "provider"
class FixtureProvider:
    name = "maintenance-fixture"
    def __init__(self, coordinator=None, config=None):
        self.coordinator, self.config = coordinator, config or {}
    def get_info(self):
        return ProviderInfo(id=self.name, display_name="Offline maintenance fixture",
            capabilities=["tools"], defaults={"model":"offline-maintenance"})
    def get_config_schema(self): return {"fields":[]}
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        root = Path(self.config["queue"])
        identity = uuid.uuid4().hex
        pending = root / (identity + ".request.json")
        staging = pending.with_suffix(".tmp")
        staging.write_text(json.dumps({"id":identity, "sessionId":self.coordinator.session_id,
            "pid":os.getpid(), "request":request.model_dump(mode="json")}))
        staging.replace(pending)
        reply = root / (identity + ".reply.json")
        async with asyncio.timeout(90):
            while not reply.exists():
                await asyncio.sleep(.02)
        return ChatResponse.model_validate(json.loads(reply.read_text()))
async def mount(coordinator, config=None):
    await coordinator.mount("providers", FixtureProvider(coordinator, config), name="maintenance-fixture")
'''

TOOL = '''
import asyncio, json, os
from pathlib import Path
from amplifier_core import ToolResult
__amplifier_module_type__ = "tool"
class Effect:
    name = "maintenance_effect"
    description = "Append exactly one synthetic effect."
    input_schema = {"type":"object", "properties":{"hold":{"type":"boolean"}, "approval":{"type":"boolean"}}}
    def __init__(self, coordinator, config):
        self.coordinator, self.config = coordinator, config or {}
    async def execute(self, arguments):
        if arguments.get("approval"):
            decision = await self.coordinator.approval_system.request_approval(
                "Allow the counted synthetic effect?", ["allow", "deny"])
            if decision != "allow":
                return ToolResult(success=False, output="Synthetic effect denied; nothing was performed.")
        if arguments.get("hold"):
            Path(self.config["effects"] + ".started").write_text("waiting")
            while not Path(self.config["effects"] + ".release").exists():
                await asyncio.sleep(.02)
        with Path(self.config["effects"]).open("a") as out:
            out.write(json.dumps({"sessionId":self.coordinator.session_id, "pid":os.getpid()})+"\\n")
            out.flush()
            os.fsync(out.fileno())
        return ToolResult(success=True, output="Counted maintenance fixture effect")
async def mount(coordinator, config=None):
    await coordinator.mount("tools", Effect(coordinator, config), name="maintenance_effect")
'''


async def until(predicate, seconds=30):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.02)


def stamp(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


async def run(root, mode="restart"):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace, shared, home, queue = [root / name for name in ("workspace", "shared", "app", "queue")]
    for path in (workspace, shared, home, queue):
        path.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(shared), AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1")
    if INSTALLED_PACKAGE:
        os.environ.pop('PYTHONPATH', None)
    else:
        os.environ['PYTHONPATH'] = str(SOURCE)
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_ACCESS_TOKEN")) or key in {"OPENAI_KEY", "ANTHROPIC_AUTH_TOKEN"}:
            os.environ.pop(key, None)
    import yaml
    from aiohttp.test_utils import TestClient, TestServer
    from amplifier_web.auth import SESSION_COOKIE, new_session
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app

    modules = ("loop-live", "context-simple", "tool-delegate")
    paths = {name: str(Path(importlib.import_module("amplifier_module_" + name.replace("-", "_")).__file__).parent)
             for name in modules}
    for kind, source in (("provider", PROVIDER), ("tool", TOOL)):
        package = root / kind / ("amplifier_module_" + kind + "_maintenance_fixture")
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source)
        paths[kind + "-maintenance-fixture"] = str(package.parent)
    effects = root / "effects.jsonl"
    bundle = root / "bundle.yaml"
    bundle.write_text(yaml.safe_dump({
        "bundle": {"name": "maintenance-native-fixture", "version": SNAPSHOT_VERSION},
        "session": {"orchestrator": {"module": "loop-live", "config": {
            "use_streaming": False, "min_delay_between_calls_ms": 0, "background_delegate": False}},
            "context": {"module": "context-simple", "source": paths["context-simple"]}},
        "providers": [{"module": "provider-maintenance-fixture", "source": paths["provider-maintenance-fixture"],
                       "config": {"queue": str(queue), "default_model": "offline-maintenance"}}],
        "tools": [{"module": "tool-maintenance-fixture", "source": paths["tool-maintenance-fixture"],
                   "config": {"effects": str(effects)}},
                  {"module": "tool-delegate", "source": paths["tool-delegate"],
                   "config": {"features": {"self_delegation": {"enabled": True}}}}],
        "agents": {"worker": {"instruction": "Perform the assigned synthetic effect."}},
    }))
    (shared / "settings.yaml").write_text(yaml.safe_dump({
        "bundle": {"active": str(bundle), "app": []},
        "web_bundles": {"excluded": list(APPENDED_DEFAULTS)},
        "sources": {"modules": paths},
    }))
    report = {"paths": paths, "versions": {name: importlib.metadata.version(name) for name in
        ("amplifier-core", "amplifier-foundation", "amplifier-module-loop-live",
         "amplifier-module-loop-streaming")}, "processes": [], "stages": [], "fallbackSignals": []}
    report['installedPackage'] = INSTALLED_PACKAGE
    report['applicationPath'] = str(Path(importlib.import_module('amplifier_web').__file__).resolve())
    if INSTALLED_PACKAGE:
        assert '/site-packages/amplifier_web/' in report['applicationPath'], report
        assert all('/site-packages/' in paths[name] for name in ('loop-live', 'tool-delegate')), report
    client = app = runtime = None
    processes, worker_rows = [], []
    runtime_events = []
    seen = set()

    async def launch():
        runtime = RuntimeManager(command=[sys.executable, "-u", str(Path(__file__).resolve()),
                                          "--worker", str(root)],
                                 startup_timeout=45, retention={"prewarm_on_select": False})
        web = await create_app(home, workspace=workspace, runtime=runtime,
                              voice=False, background_updates=False, preload_providers=False)
        app = web["service"]
        original_event = app.on_runtime_event
        async def observed_event(kind, value):
            runtime_events.append({"type": kind, "data": value})
            with (root / "runtime-events.jsonl").open("a") as out:
                out.write(json.dumps({"type": kind, "data": value}) + "\n")
            await original_event(kind, value)
        app.on_runtime_event = observed_event
        runtime.app_bridge, runtime.home = app.app_bridge, home
        client = TestClient(TestServer(web))
        await client.start_server()
        headers = {"Cookie": SESSION_COOKIE + "=" + new_session(web["session_secret"]),
                   "X-Amplifier-State-Transport": "delta-v1"}
        return client, app, runtime, headers

    async def action(name, args=None, *, status=200):
        response = await client.post("/api/actions", headers=headers,
            json={"action": name, "args": args or {}, "id": uuid.uuid4().hex})
        data = await response.json()
        assert response.status == status, (response.status, data)
        if status == 200:
            assert data.get("accepted"), data
        return data

    async def request(role, native_id):
        def find():
            for path in sorted(queue.glob("*.request.json")):
                if path.name in seen:
                    continue
                row = json.loads(path.read_text())
                if (row["sessionId"] == native_id) == (role == "root"):
                    seen.add(path.name)
                    return row
                if role == "child" and mode != "completed-job":
                    raise AssertionError("The original child did not resume; the parent requested another call.")
        async with asyncio.timeout(40):
            while True:
                if row := find():
                    return row
                await asyncio.sleep(.02)

    def reply(row, text="", calls=None):
        target = queue / (row["id"] + ".reply.json")
        staging = target.with_suffix(".tmp")
        staging.write_text(json.dumps({
            "content": [{"type": "text", "text": text}], "tool_calls": calls}))
        staging.replace(target)

    def remember():
        for row in runtime.workers.values():
            process = row["process"]
            if process not in processes:
                processes.append(process)
                worker_rows.append(row)
                report["processes"].append({"pid": process.pid, "group": os.getpgid(process.pid),
                                             "startTicks": stamp(process.pid)})

    try:
        client, app, runtime, headers = await launch()
        await action("session.create", {"title": "Native maintenance", "bundle": str(bundle)})
        session = app._session()
        sid = session["id"]
        await action("session.naming", {"id": sid, "automatic": False})
        await runtime.start(session, app.on_runtime_event)
        remember()
        native_id = runtime.workers[sid]["runtime_id"]
        await action("conversation.send", {"sessionId": sid, "text": "One original request"})
        root_request = await request("root", native_id)
        if mode == 'approval':
            reply(root_request, calls=[{'id': 'approval-effect', 'name': 'maintenance_effect',
                                       'arguments': {'approval': True}}])
            await until(lambda: bool(app._session(sid).get('approvals')))
            approval = app._session(sid)['approvals'][0]['id']
            await action('maintenance.pause')
            await until(lambda: app.maintenance.value['held'])
            assert not app.maintenance.snapshot()['readyForRestart']
            await action('approval.respond', {'sessionId': sid, 'id': approval, 'decision': 'allow'}, status=409)
            assert not effects.exists()
            await action('approval.respond', {'sessionId': sid, 'id': approval, 'decision': 'deny'})
            await until(lambda: app.maintenance.snapshot()['readyForRestart'])
            paused = app.maintenance.snapshot()
            assert not effects.exists()
            await client.close()
            client = None
            client, app, runtime, headers = await launch()
            assert app.maintenance.value['held'] and not runtime.workers
            await action('maintenance.resume', {'epoch': paused['epoch']})
            resumed_request = await request('root', native_id)
            remember()
            assert 'Synthetic effect denied' in str(resumed_request['request']['messages'])
            reply(resumed_request, 'Denied effect retained without replay')
            await until(lambda: any(row.get('text') == 'Denied effect retained without replay'
                for row in app._session(sid)['messages']))
            assert not effects.exists()
            assert [(row['id'], row['status']) for row in app._session(sid)['approvals']] == [(approval, 'deny')]
            assert sum(row['type'] == 'approval.requested' for row in runtime_events) == 1
            report['stages'].append('approval-waits-without-auto-answer-denial-settles-and-survives-restart')
            report['passed'] = True
            return
        if mode in {"emergency", "held-tool"}:
            if mode == "held-tool":
                reply(root_request, calls=[{"id": "held-effect", "name": "maintenance_effect",
                                           "arguments": {"hold": True}}])
                await until(lambda: Path(str(effects) + ".started").exists())
                await action("maintenance.pause")
                await until(lambda: app.maintenance.value["held"])
                epoch = app.maintenance.value["epoch"]
                await action("maintenance.resume", {"epoch": epoch}, status=409)
                await asyncio.sleep(16)
                assert not app.maintenance.snapshot()["readyForRestart"]
                assert not effects.exists() and all(process.returncode is None for process in processes)
                report["stages"].append("running-tool-waits-without-timeout-escalation")
            assert not app.state.get("updates", {}).get("staged")
            preview = (await action("maintenance.stopPreview"))["result"]
            await action("maintenance.stopNow", {key: preview[key] for key in
                ("hostInstanceId", "targetDigest")} | {"confirm": True})
            await until(lambda: app.maintenance.snapshot()["canResume"])
            assert app.maintenance.snapshot()["roots"][0]["phase"] == "stopped"
            assert all(process.returncode is not None for process in processes)
            assert not effects.exists()
            health = await client.get("/api/health", headers=headers)
            assert health.status == 200 and (await health.json())["ok"]
            epoch = app.maintenance.value["epoch"]
            await action("maintenance.resume", {"epoch": epoch})
            await until(lambda: not app.maintenance.value["held"])
            assert not runtime.workers and not effects.exists()
            report["stages"].append("confirmed-emergency-stop-without-update-app-remains-inspectable")
            report["passed"] = True
            return
        reply(root_request, calls=[{"id": "original-delegate", "name": "delegate",
                                  "arguments": {"agent": "worker", "instruction": "One original child request",
                                                **({"async": True} if mode == "completed-job" else {})}}])
        child_request = await request("child", native_id)
        if mode == "completed-job":
            waiting_root = await request("root", native_id)
            reply(child_request, calls=[{"id": "original-effect", "name": "maintenance_effect", "arguments": {}}])
            finishing_child = await request("child", native_id)
            assert len(effects.read_text().splitlines()) == 1
            await action("maintenance.pause")
            await until(lambda: any(json.loads(path.read_text()).get("held")
                for path in (home / "sessions").glob("*/maintenance/control.json")))
            reply(waiting_root, "Delegate is working")
            reply(finishing_child, "Original child result retained")
            await until(lambda: app.maintenance.snapshot()["readyForRestart"])
            paused = app.maintenance.snapshot()
            assert paused["roots"][0]["proof"]["pendingResults"]
            assert paused["roots"][0]["proof"]["jobOrigins"][0]["status"] == "returned"
            report["paused"] = paused
            await client.close()
            client = None
            assert all(process.returncode is not None for process in processes)
            client, app, runtime, headers = await launch()
            assert app.maintenance.value["held"] and not runtime.workers
            await action("maintenance.resume", {"epoch": paused["epoch"]})
            await until(lambda: runtime.workers)
            remember()
            result_request = await request("root", native_id)
            assert "Original child result retained" in str(result_request["request"]["messages"])
            assert "original-delegate" in str(result_request["request"]["messages"])
            assert len(effects.read_text().splitlines()) == 1
            reply(result_request, "Original result received")
            await until(lambda: any(row.get("text") == "Original result received"
                for row in app._session(sid)["messages"]))
            await until(lambda: not app.maintenance.value["held"])
            assert len(effects.read_text().splitlines()) == 1
            report["stages"].append("completed-delegate-result-survives-host-restart-once")
            report["passed"] = True
            return
        report["stages"].append("child-model-in-flight")
        await action("maintenance.pause")
        await until(lambda: any(path.exists() and json.loads(path.read_text()).get("held")
            for path in (home / "sessions").glob("*/maintenance/control.json")))
        await action("conversation.send", {"sessionId": sid, "text": "MUST NOT START"}, status=409)
        reply(child_request, calls=[{"id": "original-effect", "name": "maintenance_effect", "arguments": {}}])
        await until(lambda: app.maintenance.snapshot()["readyForRestart"])
        paused = app.maintenance.snapshot()
        assert paused["roots"][0]["phase"] == "suspended", paused
        assert app._session(sid)["status"] == "paused"
        assert app._session(sid)["execution"]["turns"][-1]["phase"] == "paused"
        assert not effects.exists()
        report["paused"] = paused
        from amplifier_web.update_blockers import blockers
        await until(lambda: not blockers(app), seconds=5)
        report["updateBlockersAfterPause"] = blockers(app)
        report["stages"].append("root-and-child-checkpointed")
        await client.close()
        client = None
        assert all(process.returncode is not None for process in processes)
        report["stages"].append("original-worker-exited")
        client, app, runtime, headers = await launch()
        assert app.maintenance.value["held"]
        assert not runtime.workers and not effects.exists()
        assert app._session(sid)["status"] == "paused"
        assert not app._session(sid).get("error")
        resumed_event_start = len(runtime_events)
        await action("conversation.send", {"sessionId": sid, "text": "STILL HELD"}, status=409)
        await action("maintenance.resume", {"epoch": paused["epoch"]})
        await until(lambda: runtime.workers)
        remember()
        next_child = await request("child", native_id)
        assert len(effects.read_text().splitlines()) == 1
        assert next_child["sessionId"] == child_request["sessionId"]
        assert str(next_child["request"]["messages"]).count("One original child request") == 1
        reply(next_child, "Original child completed")
        next_root = await request("root", native_id)
        assert "original-delegate" in str(next_root["request"]["messages"])
        reply(next_root, "Original root completed")
        await until(lambda: not app.maintenance.value["held"])
        generation = paused["roots"][0]["proof"]["generationId"]
        await until(lambda: any(row["type"] == "runtime.generation"
            and row["data"].get("event") == "generation.finished"
            and row["data"].get("generation_id") == generation
            for row in runtime_events[resumed_event_start:]))
        assert any(row.get("role") == "assistant" and row.get("text") == "Original root completed"
                   for row in app._session(sid)["messages"])
        assert len(effects.read_text().splitlines()) == 1
        report["resumed"] = app.maintenance.snapshot()
        assert report["resumed"]["roots"][0]["phase"] == "resumed", report["resumed"]
        assert not app._session(sid).get("error")
        assert app._session(sid)["collaborationGeneration"]["completed"]
        await action("maintenance.resume", {"epoch": paused["epoch"]}, status=409)
        report["stages"].append("same-child-root-resumed-once-after-restart")
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = repr(exc)
        if app is not None:
            report["maintenanceAtFailure"] = app.maintenance.snapshot()
            report["sessionsAtFailure"] = [{key: value for key, value in row.items() if key in
                {"id", "status", "runtimeStatus", "failure", "execution", "collaborationGeneration"}}
                for row in app.state.get("sessions", [])]
        raise
    finally:
        if runtime is not None:
            remember()
        if client is not None:
            try:
                await asyncio.wait_for(client.close(), 20)
            except Exception as exc:
                report["cleanupError"] = repr(exc)
        # Strictly these subprocess handles, after their original start identity
        # and process group are verified. Never any service or whole-DTU signal.
        for process, identity in zip(processes, report["processes"]):
            if process.returncode is None:
                assert stamp(process.pid) == identity["startTicks"]
                assert os.getpgid(process.pid) == process.pid == identity["group"]
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
                report["fallbackSignals"].append({"pid": process.pid, "signal": "SIGKILL"})
        report["workerStderr"] = [{"pid": row["process"].pid, "stderr": row["stderr"],
                                   "returncode": row["process"].returncode} for row in worker_rows]
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if report.get("passed"):
            assert not report["fallbackSignals"] and not report.get("cleanupError"), report


async def observed_worker(root):
    from amplifier_web.host.execution_pause import ExecutionPause
    from amplifier_web.runtime_worker import Worker, configure_protocol
    original = ExecutionPause.install
    async def observed(self, coordinator, **kwargs):
        # Private diagnostics only. No config, checkpoint or admission mutation.
        with (root / "native-mounts.jsonl").open("a") as out:
            out.write(json.dumps({"pid": os.getpid(), "sessionId": coordinator.session_id,
                                  "restore": kwargs.get("restore"),
                                  "config": coordinator.config}) + "\n")
        return await original(self, coordinator, **kwargs)
    ExecutionPause.install = observed
    configure_protocol()
    await Worker().run()


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        asyncio.run(observed_worker(Path(sys.argv[2])))
    else:
        asyncio.run(run(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "restart"))
