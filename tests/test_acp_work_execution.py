"""Selected, full Work -> normal native Worker/Core -> real stdio ACP.

Opt in with UNIFIED_ACP_WORK_SOURCES from scripts/prepare_kiss502_work.py.
Relocate this file plus the named fixture/import closure for installed-wheel
qualification; never place amplifier_web source beside the installed tests.
This does not launch a DTU, use a real model/account or invoke any desktop tool.
"""
import asyncio
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

from fixtures.acp_work_sources import (
    assert_composition, bind_skill_sources, read_sources, source_settings,
    WORK_AGENTS, WORK_HOOKS, WORK_TOOLS,
)


class NativePeer:
    """Small real stdio client, independent of other test fixture imports."""
    def __init__(self, process):
        self.process, self.serial, self.updates = process, 0, []

    async def call(self, method, **params):
        self.serial += 1
        identity = self.serial
        self.process.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": identity, "method": method, "params": params,
        }) + "\n").encode())
        await self.process.stdin.drain()
        async with asyncio.timeout(75):
            while True:
                line = await self.process.stdout.readline()
                assert line, (await self.process.stderr.read()).decode()
                row = json.loads(line)
                if row.get("id") == identity:
                    return row
                self.updates.append(row)

    async def eof(self):
        self.process.stdin.close()
        await asyncio.wait_for(self.process.wait(), 5)
        assert self.process.returncode == 0, (await self.process.stderr.read()).decode()


@pytest.fixture
def work_sources():
    path = os.environ.get("UNIFIED_ACP_WORK_SOURCES")
    if not path:
        pytest.skip("Opt-in actual Work ACP lane: prepare exact UNIFIED_ACP_WORK_SOURCES first")
    return read_sources(path)


async def isolated_work(tmp_path, monkeypatch, receipt):
    # The no-device disposition is a real headless Linux mount, not an injected
    # select_backend exception. Never run this fixture on a user's desktop.
    assert sys.platform == "linux", "Requires a no-device Linux test runtime"
    for name in tuple(os.environ):
        if (name.startswith(("AMPLIFIER_", "DISPLAY", "WAYLAND_", "XAUTHORITY",
                             "DBUS_SESSION_BUS_ADDRESS"))
                or name in {"PYTHONPATH", "SSH_AUTH_SOCK"}):
            monkeypatch.delenv(name, raising=False)
    for key, folder in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
                        ("AMPLIFIER_WEB_HOME", "app"),
                        ("AMPLIFIER_SESSION_STATE_HOME", "ownership")):
        directory = tmp_path / folder
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(key, str(directory))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("AMPLIFIER_RUNTIME_IMMUTABLE", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Deliberate fixture input collisions exercise normal workspace/user order.
    for base in (workspace / ".amplifier", workspace / ".agents",
                 tmp_path / "home/.amplifier", tmp_path / "home/.agents"):
        sentinel = base / "skills/acp-work-precedence/SKILL.md"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text(
            "---\nname: acp-work-precedence\ndescription: Offline precedence sentinel\n---\n"
            "This is inert fixture data.\n")
    # Only the provider is substituted. All other settings are source bindings.
    settings = source_settings(receipt)
    provider_source = Path(__file__).parent / "fixtures/acp_work_provider.py"
    provider = tmp_path / "provider/amplifier_module_provider_acp_work_fixture"
    provider.mkdir(parents=True)
    (provider / "__init__.py").write_text(
        "from importlib.util import spec_from_file_location, module_from_spec\n"
        f"_spec=spec_from_file_location('acp_work_fixture', {str(provider_source.resolve())!r})\n"
        "_module=module_from_spec(_spec)\n_spec.loader.exec_module(_module)\n"
        "Provider=_module.Provider\nmount=_module.mount\n")
    audit = tmp_path / "provider.jsonl"
    settings["config"] = {"providers": [{
        "module": "provider-acp-work-fixture", "source": str(provider.parent),
        "config": {"audit": str(audit), "default_model": "scripted"},
    }]}
    from amplifier_web.host.config import HostConfig
    from amplifier_web.host.session import load_root_bundle
    config = HostConfig(tmp_path / "app", workspace, settings,
                        tmp_path / "registry", tmp_path / "shared")
    _, bundle, _ = await load_root_bundle(config, "work")
    plan = assert_composition(bundle, config)
    from amplifier_web.host.components import declarations
    assert {row["module"] for row in declarations(plan)} <= {
        *settings["sources"]["modules"], "provider-acp-work-fixture"}
    skills, bindings = bind_skill_sources(plan, receipt)
    settings["overrides"] = {"tool-skills": {"config": {"skills": skills}}}
    (tmp_path / "skill-bindings.json").write_text(json.dumps(bindings))
    (tmp_path / "shared/settings.yaml").write_text(json.dumps(settings))
    return workspace, settings, audit


async def test_actual_work_source_composition(tmp_path, monkeypatch, work_sources):
    from amplifier_web.host.config import HostConfig
    from amplifier_web.host.session import load_root_bundle
    workspace, settings, _ = await isolated_work(tmp_path, monkeypatch, work_sources)
    config = HostConfig(tmp_path / "app", workspace, settings,
                        tmp_path / "registry", tmp_path / "shared")
    _, bundle, chosen = await load_root_bundle(config, "work")
    bundle.resolve_pending_context()
    assert chosen == settings["sources"]["bundles"]["work"]
    plan = assert_composition(bundle, config)
    assert [row["module"] for row in plan["providers"]] == ["provider-acp-work-fixture"]
    assert "@work:context/system.md" in bundle.instruction


async def test_actual_work_stdio_acp_effect_and_native_reconnect(
        tmp_path, monkeypatch, aiohttp_client, work_sources):
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app
    from amplifier_web.session_files import project_slug
    from amplifier_web.session_identity import native_id

    artifacts = os.environ.get("AMPLIFIER_TEST_ARTIFACTS")
    workspace, settings, audit = await isolated_work(tmp_path, monkeypatch, work_sources)
    runtime = RuntimeManager(
        command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
        startup_timeout=75)
    app = await create_app(
        tmp_path / "app", workspace=workspace, runtime=runtime,
        preload_providers=False, voice=False,
        background_updates=False, background_work=False)
    service = app["service"]
    runtime.app_bridge, runtime.home = service.app_bridge, tmp_path / "app"
    client = await aiohttp_client(app)
    credential = tmp_path / "owner-token"
    credential.write_text(app["control_token"])
    credential.chmod(0o600)
    processes, events = [], []
    original = service._execution_observers.original

    async def observe(kind, payload):
        result = await original(kind, payload)
        events.append((kind, dict(payload)))
        return result

    async def observe_batch(rows):
        result = await original.batch(rows)
        events.extend((kind, dict(payload)) for kind, payload in rows)
        return result

    service._execution_observers.original = type(original)(observe, observe_batch)
    relay = [
        sys.executable, "-I", "-B", "-m", "amplifier_web",
        "--data-dir", str(tmp_path / "relay"), "--workspace", str(workspace),
        "acp", "--bundle", "work", "--server", str(client.make_url("")).rstrip("/"),
        "--token-file", str(credential),
    ]

    async def launch():
        process = await asyncio.create_subprocess_exec(
            *relay, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        peer = NativePeer(process)
        assert (await peer.call("initialize", protocolVersion=1))["result"]["protocolVersion"] == 1
        return peer

    try:
        first = await launch()
        sid = (await first.call("session/new", cwd=str(workspace), mcpServers=[]))["result"]["sessionId"]
        for turn in (1, 2):
            peer = first if turn == 1 else await launch()
            if turn == 2:
                assert (await peer.call("session/resume", sessionId=sid, cwd=str(workspace),
                                       mcpServers=[]))["result"] == {}
            response = await peer.call(
                "session/prompt", sessionId=sid,
                prompt=[{"type": "text", "text": f"Actual Work input {turn}: 漢🙂"}])
            assert response.get("result", {}).get("stopReason") == "end_turn", response
            text = "".join(row["params"]["update"].get("content", {}).get("text", "")
                           for row in peer.updates if row.get("method") == "session/update")
            assert f"Native reply {turn}: 漢🙂" in text, peer.updates
            assert service._session(sid)["title"] == f"Native ACP effect {turn}"
            await peer.eof()
        rows = [json.loads(line) for line in audit.read_text().splitlines()]
        work = [row for row in rows if row["kind"] == "work"]
        assert len(work) == 2
        worker_pid = runtime.workers[sid]["process"].pid
        assert {row["pid"] for row in rows} == {worker_pid}
        assert worker_pid not in {os.getpid(), *(p.pid for p in processes)}
        for row in work:
            plan = row["plan"]
            assert row["contextType"] == "BoundaryContextManager"
            assert {r["module"] for r in plan["tools"]} == set(WORK_TOOLS)
            assert {r["module"] for r in plan["hooks"]} == set(WORK_HOOKS)
            assert set(plan["agents"]) == WORK_AGENTS
            assert plan["session"]["context"]["config"]["token_meter"] == "actual"
            loads = row["construction"]["loads"]
            assert all(load["mounted"] for load in loads)
            assert len(loads) == 23  # 15 tools + 5 hooks + loop/context + provider.
            assert "computer_use_unavailable" in row["tools"]
            assert not {"computer", "desktop"} & set(row["tools"])
            skills = row["skills"]
            assert skills["winner"] == str(workspace / ".amplifier/skills/acp-work-precedence/SKILL.md")
            assert skills["config"]["visibility"]["visibility_token_budget"] == 2500
            assert "imagegen" in skills["names"] and "coordinate-work" in skills["names"]
            assert skills["visibilityBlocks"] == 1
            assert skills["visibilityApproxTokens"] <= 2500
            expected = [plan["session"][key] for key in ("orchestrator", "context")]
            expected += [r for section in ("providers", "tools", "hooks") for r in plan[section]]
            assert [(load["module"], load["config"], load["source_hint"]) for load in loads] == [
                (r["module"], r.get("config", {}), r.get("source")) for r in expected]
            assert all(r["completed"] for r in row["construction"]["ready"])
            for name, observed in row["modules"].items():
                path = Path(importlib.import_module(name).__file__).resolve()
                assert observed["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
                # Core/Foundation/product import from the prepared environment;
                # explicitly selected community modules can import their exact
                # exported source through the normal activator instead.
                if name.startswith(("amplifier_module_",)):
                    module = name.split(".")[0].removeprefix("amplifier_module_").replace("_", "-")
                    source = Path(work_sources["modules"][module]["path"]).resolve()
                    assert (observed["path"] == str(path)
                            or Path(observed["path"]).is_relative_to(source)), observed
                else:
                    assert observed["path"] == str(path), observed
        budgets = [row for row in rows if row["kind"] == "budget"]
        dispatches = [row for row in rows if row["kind"] == "dispatch"]
        assert len(dispatches) == 6  # state -> rename -> final for each real turn.
        assert all(any(b["sha256"] == d["sha256"] and b["requireMeasurement"]
                       for b in budgets) for d in dispatches)
        commands = [json.loads(raw) for (raw,) in service.db.execute(
            "SELECT receipt FROM commands WHERE id IN ('native-acp-title-1','native-acp-title-2')")]
        assert len(commands) == 2 and all(row["accepted"] for row in commands)
        row = service._session(sid)
        assert native_id(row) == runtime.workers[sid]["runtime_id"] == sid
        delivered = Counter(payload["inputId"] for kind, payload in events
                            if kind == "runtime.status" and payload.get("event") == "input.delivered")
        terminal = Counter(identity for kind, payload in events
                           if kind == "runtime.generation" and payload.get("event") == "generation.finished"
                           for identity in payload.get("input_ids", []))
        assert len(delivered) == 2 and set(delivered.values()) == {1}
        assert terminal == delivered
        assert len([m for m in row["messages"] if m["role"] == "user"]) == 2
        # Every external source was explicitly prepared. A hidden runtime fetch,
        # even one which happened to resolve the same current main, is a failure.
        fetched = [str(p) for root in (tmp_path / "registry", tmp_path / "app")
                   for p in root.rglob(".amplifier_cache_meta.json")]
        assert not fetched, fetched
        evidence = {
            "status": "passed", "nativeSession": sid, "workerPid": worker_pid,
            "project": project_slug(workspace), "nativeInputs": dict(delivered),
            "nativeTerminals": dict(terminal), "effects": commands,
            "work": work, "budget": budgets, "dispatches": dispatches,
            "skillSourceBindings": json.loads((tmp_path / "skill-bindings.json").read_text()),
            "resources": {name: {k: v for k, v in r.items() if k != "files"}
                          for name, r in work_sources["resources"].items()},
            "modules": {name: {k: v for k, v in r.items() if k != "files"}
                        for name, r in work_sources["modules"].items()},
            "agentSources": {name: {k: v for k, v in r.items() if k != "files"}
                             for name, r in work_sources["agentSources"].items()},
            "limits": [
                "Scripted local provider and synthetic serialized-request counter, not vendor measurement",
                "Normal full selected Work and five app behaviors; no paid API/account/device",
                "Headless unavailable disposition observed; computer/desktop never invoked",
                "Two turns/resident native worker across ACP EOF/reconnect, not host restart/compaction",
                "Unicode text final output, not streaming/media/native desktop qualification",
                "Direct ACP; published Instance wrapper is covered separately by the minimal native lane",
            ],
        }
        (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        if artifacts:
            target = Path(artifacts) / "actual-work-acp"
            target.mkdir(parents=True, exist_ok=True)
            (target / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    finally:
        try:
            if artifacts:
                target = Path(artifacts) / "actual-work-acp"
                target.mkdir(parents=True, exist_ok=True)
                (target / "events.json").write_text(json.dumps(events, indent=2) + "\n")
                if audit.exists():
                    (target / "provider.jsonl").write_bytes(audit.read_bytes())
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
            await asyncio.gather(*(process.wait() for process in processes))
            await client.close()