"""Offline migration acceptance: saved choices must reach a completed native turn.

Reuse the maintained provider fixture; real host/worker/Core/Foundation own
admission, replacement, persistence and native history. Only dependency transport
is replaced with an already installed local graph. Generation qualification and
connection recording identity have separate maintained regression checks.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from next_message_native_probe import PROVIDER, until


async def run(root, scenario):
    root.mkdir(parents=True, exist_ok=False)
    home, shared, workspace = (root / name for name in ("app", "shared", "workspace"))
    shared.mkdir()
    workspace.mkdir()
    os.environ.update(
        AMPLIFIER_WEB_HOME=str(home), AMPLIFIER_HOME=str(shared),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1",
    )
    for key in list(os.environ):
        if key.endswith(("_API_KEY", "_ACCESS_TOKEN")):
            os.environ.pop(key, None)
    import yaml
    import amplifier_module_context_simple
    import amplifier_module_loop_live
    from amplifier_web import draft_defaults
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.draft_defaults_probe import query
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService

    provider = root / "provider" / "amplifier_module_provider_fixture"
    provider.mkdir(parents=True)
    (provider / "__init__.py").write_text(PROVIDER)
    sys.path.insert(0, str(provider.parent))
    os.environ["PYTHONPATH"] = str(SOURCE) + os.pathsep + str(provider.parent)
    calls = root / "calls.jsonl"
    modules = {
        "context-simple": str(Path(amplifier_module_context_simple.__file__).parent),
        "loop-live": str(Path(amplifier_module_loop_live.__file__).parent),
        "provider-fixture": str(provider.parent),
    }
    bundles = []
    for label in ("a", "b"):
        path = root / f"bundle-{label}.yaml"
        path.write_text(yaml.safe_dump({
            "bundle": {"name": "matrix-" + label, "version": SNAPSHOT_VERSION},
            "session": {
                "orchestrator": {"module": "loop-live", "config": {"use_streaming": False}},
                "context": {"module": "context-simple", "source": modules["context-simple"]},
            },
            "providers": [{"module": "provider-fixture", "source": str(provider.parent),
                           "config": {"root": label, "calls": str(calls),
                                      "default_model": "first"}}],
        }))
        bundles.append(str(path))
    (shared / "settings.yaml").write_text(yaml.safe_dump({
        "bundle": {"active": bundles[0], "app": []},
        "web_bundles": {"excluded": list(APPENDED_DEFAULTS)},
        "sources": {"modules": modules},
    }))
    async def resolver(home, workspace, bundle=None, app_bundle=None, **kwargs):
        return await query({
            "home": str(home), "workspace": workspace, "bundle": bundle,
            "sessionId": kwargs.get("session_id"), "catalog": kwargs.get("catalog", False),
            "replacement": kwargs.get("replacement"),
            "desiredBundle": kwargs.get("desired_bundle", False),
        })
    worker_command = [sys.executable, "-u", str(SOURCE / "amplifier_web/runtime_worker.py")]
    if scenario == "production-probe":
        # Keep the production resolver, subprocess, environment and candidate
        # qualification entry. Only select the DTU's already installed graph;
        # there is no immutable ecosystem generation in this offline fixture.
        RuntimeManager._command = lambda self, *a, **kw: list(self.command or worker_command)
    else:
        draft_defaults.resolve_defaults = resolver
    processes = []
    spawn = asyncio.create_subprocess_exec
    async def observed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process
    asyncio.create_subprocess_exec = observed_spawn
    def create():
        runtime = RuntimeManager(
            command=worker_command,
            startup_timeout=45, retention={"prewarm_on_select": False},
        )
        app = AppService(home, workspace=workspace, runtime=runtime)
        app.management = Management(app)
        runtime.app_bridge, runtime.home = app.app_bridge, home
        return app, runtime
    app, runtime = create()
    report = {"scenario": scenario, "passed": False, "versions": {
        name: importlib.metadata.version(name)
        for name in ("amplifier-core", "amplifier-foundation", "amplifier-module-loop-live")
    }}
    try:
        if scenario == "native-restart":
            store = SessionStore.for_app(home, workspace)
            store.save("matrix-native", [{"role": "user", "content": "Retain native history"}],
                       {"working_dir": str(workspace), "bundle": bundles[0]})
            await app.history.refresh(force=True)
            target = app._admit_session(app.directory.resolve("matrix-native"))
            assert target["historyManaged"]
        else:
            await app.dispatch("session.create", {"bundle": bundles[0]}, command_id="create")
            target = app._session()
        sid = target["id"]
        await app.dispatch("session.naming", {"id": sid, "automatic": False})
        if scenario == "cold-empty":
            target["selection"] = {}
            app._publish_changes(sessions={sid})
        elif scenario == "cold-none":
            target["selection"] = None
            app._publish_changes(sessions={sid})
        elif scenario == "cold-missing":
            target.pop("selection", None)
            app._publish_changes(sessions={sid})
        async def send(identity):
            receipt = await app.dispatch("conversation.send", {
                "sessionId": sid, "text": identity,
                "expectedConfigurationRevision": target.get("nextMessageRevision", 0),
            }, command_id=identity, include_state=False)
            assert receipt["accepted"], receipt
            await until(lambda: target.get("collaborationGeneration", {}).get("terminal"))
            await until(lambda: runtime.workers.get(sid, {}).get("parked"))
            turn = next(row for row in target["execution"]["turns"] if row["id"] == identity)
            return copy.deepcopy(turn)
        warm = scenario.startswith("warm") or scenario == "model-only"
        if warm:
            assert (await send("baseline"))["phase"] == "completed"
        if scenario in {"warm-pin", "warm-reset", "model-only"}:
            await app.dispatch("conversation.configure", {
                "sessionId": sid, "expectedRevision": 0,
                "selection": {"instance": "fixture", "model": "second", "effort": "high"},
            }, command_id="pin", include_state=False)
            assert (await send("pinned"))["phase"] == "completed"
        before = copy.deepcopy(target.get("selection"))
        revision = target.get("nextMessageRevision", 0)
        args = {"sessionId": sid, "expectedRevision": revision, "bundle": bundles[1]}
        # An unpinned provider owns its own default. loop-live intentionally
        # sends None; this synthetic provider does not rewrite request.model.
        expected_model, expected_effort, expected_root = None, None, "b"
        if scenario == "warm-pin":
            expected_model, expected_effort = "second", "high"
        if scenario == "warm-reset":
            args["resetModel"] = True
        if scenario == "model-only":
            args.pop("bundle")
            args["selection"] = {"instance": "fixture", "model": "first", "effort": "low"}
            expected_model, expected_effort, expected_root = "first", "low", "a"
        if scenario == "peer-archived-cross":
            other = root / "other"
            other.mkdir()
            await app.dispatch("session.create", {"workspace": str(other), "bundle": bundles[0]},
                               command_id="operator-create", include_state=False)
            operator = app._session()
            operator["collaborationGeneration"] = {
                "id": "operator-generation", "inputIds": ["human-settings"], "terminal": False,
            }
            access = (await app.dispatch("access.get", {"sessionId": operator["id"]}))["result"]
            await app.dispatch("access.set", {
                "sessionId": operator["id"], "scope": "session",
                "mode": "full", "expectedRevision": access["revision"],
            })
            target["archived"] = True
            app._publish()
            assert app.state["selectedSessionId"] != sid
        selected = app.state["selectedSessionId"]
        starts, prior_calls = len(processes), calls.read_bytes() if calls.exists() else b""
        if scenario == "peer-archived-cross":
            receipt = await app.app_bridge("dispatch", {
                "action": "conversation.configure", "args": args, "id": "migration",
                "_runtimeSessionId": operator["id"], "_generationId": "operator-generation",
                "_inputBindings": [{"inputId": "human-settings"}],
            }, operator["id"])
        else:
            receipt = await app.dispatch("conversation.configure", args, command_id="migration",
                                         include_state=False)
        assert receipt["result"]["mounted"] is False
        assert len(processes) == starts
        assert (calls.read_bytes() if calls.exists() else b"") == prior_calls
        assert app.state["selectedSessionId"] == selected
        if scenario not in {"warm-reset", "model-only"}:
            assert target.get("selection") == before
        if scenario in {"native-restart", "cold-restart"}:
            await app.close()
            app, runtime = create()
            view = (await app.dispatch("conversation.configuration", {"sessionId": sid},
                                      include_state=False))["result"]
            assert view["nextMessageRevision"] == revision + 1
            assert view["pendingBundleSelection"]["bundle"] == bundles[1]
            target = app._session(sid)
        turn = await send("migration-input")
        rows = [json.loads(line) for line in calls.read_text().splitlines()]
        report.update(turn=turn, calls=rows, savedWithoutStarting=True,
                      appliedRevision=target.get("appliedConfigurationRevision"),
                      pending=bool(target.get("pendingBundleSelection")),
                      archived=target.get("archived", False))
        assert rows[-1]["root"] == expected_root and rows[-1]["model"] == expected_model, rows
        assert rows[-1]["effort"] == expected_effort, rows
        assert len(rows) == len(prior_calls.splitlines()) + 1, rows
        assert target["appliedConfigurationRevision"] == revision + 1
        assert not target.get("pendingBundleSelection")
        # A correct response/tuple is insufficient if retirement falsely
        # interrupts the new turn or leaves a Stop banner.
        assert turn["phase"] == "completed", turn
        assert target.get("status") not in {"stopped", "interrupted", "error"}, target.get("error")
        native = SessionStore.for_app(home, workspace).load(
            runtime.workers[sid]["runtime_id"])[0]
        ids = [(m.get("metadata") or {}).get("amplifier_input", {}).get("id") for m in native]
        assert ids.count("migration-input") == 1, ids
        if warm:
            assert ids.count("baseline") == 1, ids
        if scenario in {"warm-pin", "warm-reset", "model-only"}:
            assert ids.count("pinned") == 1, ids
        if scenario == "native-restart":
            assert any(m.get("content") == "Retain native history" for m in native)
        if scenario == "peer-archived-cross":
            assert target["archived"] is True
        report.update(passed=True, inputExactlyOnce=True)
    except BaseException as exc:
        report.update(error=type(exc).__name__ + ": " + str(exc),
                      pendingChoice=target.get("pendingBundleSelection"),
                      placement={key: target.get(key) for key in
                                 ("workspace", "workingDirectory", "executionRevision")})
        raise
    finally:
        await app.close()
        asyncio.create_subprocess_exec = spawn
        report["cleanupConfirmed"] = all(p.returncode is not None for p in processes)
        (root / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        assert report["cleanupConfirmed"]


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))