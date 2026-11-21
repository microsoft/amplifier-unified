"""Actual connector -> real Unified host/Worker/Core, offline provider only.

Run only in the parent's prepared guest using its installed backend Python:
  KISS502_CONNECTORS=/artifacts/connectors python -m pytest -q \
    tests/fixtures/kiss502_connector_native.py --basetemp=/artifacts/connector-joined

Minecraft's old official SDK stays in its own prepared venv and subprocess.
Slack uses its unchanged published binary and unchanged fake-platform fixture.
No Mineflayer world, real Slack account, paid provider or social quality claim.
"""
import asyncio
from collections import Counter
import getpass
import hashlib
import importlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import sys
import uuid


async def minecraft_client(inputs, endpoint_file, result_file):
    """Ordinary configurable production Client, not a protocol reimplementation."""
    source = inputs / "sources/minecraft"
    inventory = json.loads((inputs / "minecraft-source.json").read_text())
    for name, expected in inventory["files"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
    import importlib.metadata as metadata
    dist = metadata.distribution("agent-client-protocol")
    direct = json.loads(dist.read_text("direct_url.json"))
    assert direct["vcs_info"]["commit_id"] == "9853d8b514d6340000bcbd01ec07808436db80cf"
    assert dist.version == "0.12.1"
    sys.path.insert(0, str(source))
    from acp_client import Client
    endpoint = json.loads(endpoint_file.read_text())
    events, replies = [], []
    sid = None
    for turn in (1, 2):
        client = Client(endpoint, lambda kind, **data: events.append((kind, data)))
        try:
            await client.start()
            actual = await client.open_session(sid)
            assert sid is None or actual == sid
            sid = actual
            result = await client.prompt(f"Native input {turn}: 漢🙂", timeout=75)
            assert result["stop_reason"] == "end_turn", result
            assert f"Native reply {turn}: 漢🙂" in result["final_text"], result
            assert result["unsettled_tools"] == [], result
            replies.append(result)
        finally:
            await client.close()
    assert sum(kind == "session_created" for kind, _ in events) == 1
    assert sum(kind == "session_resumed" for kind, _ in events) == 1
    result_file.write_text(json.dumps({"session": sid, "events": events, "replies": replies,
                                     "sdk": direct}, indent=2) + "\n")


async def slack_client(inputs, relay, workspace, home, env):
    source = inputs / "sources/slack"
    inventory = json.loads((inputs / "slack-source.json").read_text())
    for name, expected in inventory["files"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
    sys.path.insert(0, str(source / "tests"))
    from process_integration import SlackFixture, until, private_json, rows
    from aiohttp import web
    fixture = SlackFixture()
    app = web.Application()
    app.router.add_post("/api/{method}", fixture.api)
    app.router.add_get("/api/{method}", fixture.api)
    app.router.add_get("/socket/{name}", fixture.socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fixture.port = site._server.sockets[0].getsockname()[1]
    home.mkdir(mode=0o700)
    (home / "secrets").mkdir(mode=0o700)
    private_json(home / "secrets/slack.json",
                 {"app_token": "test-alpha-app", "bot_token": "test-alpha"})
    private_json(home / "adapter.json", {
        "schema": 1, "id": str(uuid.uuid4()), "label": "alpha", "revision": 1,
        "enabled": True, "setup_complete": True, "participation": "mentions",
        "acp": {"command": relay[0], "args": relay[1:], "cwd": str(workspace),
                "timeout_seconds": 75, "env": env},
        "slack": {"team_id": "Ttest", "app_id": "Aalpha", "bot_user_id": "Ualpha",
                  "bot_id": "Balpha", "channels": [], "allow_dms": True,
                  "loopback_test": True, "api_base": f"http://127.0.0.1:{fixture.port}/api/"},
        "attention": {"settle_ms": 10, "minimum_reply_interval_seconds": 0,
                      "max_replies_per_agent_per_hour": 100, "max_decisions_per_agent_per_hour": 100},
        "server": {"bind": "127.0.0.1", "port": 0, "owner": getpass.getuser(),
                   "pam_service": "acp-slack-adapter", "public_url": None, "embed_origins": []}})
    binary = inputs / "slack-package/acp-slack-adapter"
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == (
        "d837885756191f2f0a41daeabfa44e7c9d6b17d67dc92f7a86c8de32ea076f1e")
    process = None
    with (home / "service.log").open("xb") as log:
        try:
            process = await asyncio.create_subprocess_exec(
                str(binary), "serve", "--home", str(home), env=env, stdout=log, stderr=log)
            await until(lambda: "alpha" in fixture.sockets or process.returncode is not None,
                        "joined Slack socket", timeout=30)
            assert process.returncode is None, (home / "service.log").read_text()
            for turn in (1, 2):
                await fixture.send("Dnative", f"Native input {turn}: 漢🙂")
                await until(lambda: len(fixture.posts) == turn, "native Slack reply", timeout=90)
                assert f"Native reply {turn}: 漢🙂" in fixture.posts[-1]["markdown_text"]
                assert fixture.posts[-1]["channel"] == "Dnative"
                assert not fixture.posts[-1].get("thread_ts")
                await until(lambda: rows(home, "SELECT COUNT(*) FROM outbox WHERE status='sent'")[0][0] == turn,
                            "Slack confirmed outbox", timeout=10)
            sessions = rows(home, "SELECT session_id FROM session_bindings WHERE channel='Dnative'")
            assert len(sessions) == 1, sessions
            return {"session": sessions[0][0], "posts": fixture.posts,
                    "limits": ["No adapter restart in joined Slack lane; maintained fixtures cover restarts"]}
        finally:
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            await runner.cleanup()


if __name__ != "__main__":
    import pytest

    @pytest.mark.parametrize("connector", ["minecraft", "slack"])
    async def test_actual_connector_native_execution(tmp_path, monkeypatch, aiohttp_client, connector):
        inputs = Path(os.environ["KISS502_CONNECTORS"]).resolve()
        assert inputs.is_relative_to(Path("/artifacts"))
        assert os.environ.get("AMPLIFIER_TEST_CONTRACT"), "Parent's prepared guest only"
        # These are the same maintained native-runtime prerequisites/provider
        # used by test_acp_native_execution; no fallback provider graph is installed.
        probe_spec = spec_from_file_location(
            "connector_native_runtime", Path(__file__).with_name("peer_return_worker_probe.py"))
        probe = module_from_spec(probe_spec)
        probe_spec.loader.exec_module(probe)
        graph, paths = probe.qualification_runtime()
        from amplifier_web.bundles import SNAPSHOT_VERSION
        import amplifier_web
        assert "site-packages" in Path(amplifier_web.__file__).resolve().parts, (
            "Joined qualification requires the installed backend, not source shadowing")
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.server import create_app
        for key, name in (("HOME", "home"), ("AMPLIFIER_HOME", "shared"),
                          ("AMPLIFIER_WEB_HOME", "app"), ("AMPLIFIER_SESSION_STATE_HOME", "ownership")):
            directory = tmp_path / name
            directory.mkdir(mode=0o700)
            monkeypatch.setenv(key, str(directory))
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        monkeypatch.setenv("UV_OFFLINE", "1")
        monkeypatch.delenv("PYTHONPATH", raising=False)
        (tmp_path / "shared/settings.yaml").write_text("bundle:\n  app: []\n")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        provider_source = Path(__file__).with_name("acp_native_provider.py").resolve()
        provider = tmp_path / "provider/amplifier_module_provider_acp_native_fixture"
        provider.mkdir(parents=True)
        (provider / "__init__.py").write_text(
            "from importlib.util import spec_from_file_location,module_from_spec\n"
            f"s=spec_from_file_location('native_fixture',{str(provider_source)!r})\n"
            "m=module_from_spec(s)\ns.loader.exec_module(m)\nProvider=m.Provider\nmount=m.mount\n")
        audit = tmp_path / "provider.jsonl"
        bundle = tmp_path / "bundle.yaml"
        bundle.write_text(
            f"bundle:\n  name: connector-native-fixture\n  version: {SNAPSHOT_VERSION}\n"
            "session:\n  orchestrator:\n    module: loop-live\n"
            f"    source: {json.dumps(str(paths['amplifier_module_loop_live']))}\n"
            "    config:\n      use_streaming: false\n  context:\n    module: context-simple\n"
            f"    source: {json.dumps(str(paths['amplifier_module_context_simple']))}\n"
            "providers:\n  - module: provider-acp-native-fixture\n"
            f"    source: {json.dumps(str(provider.parent))}\n"
            f"    config:\n      audit: {json.dumps(str(audit))}\n")
        runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                                 startup_timeout=45)
        app = await create_app(tmp_path / "app", workspace=workspace, runtime=runtime,
                               preload_providers=False, voice=False, background_updates=False,
                               background_work=False)
        service = app["service"]
        runtime.app_bridge, runtime.home = service.app_bridge, tmp_path / "app"
        http = await aiohttp_client(app)
        credential = tmp_path / "owner-token"
        credential.write_text(app["control_token"])
        credential.chmod(0o600)
        relay = [sys.executable, "-I", "-B", "-m", "amplifier_web",
                 "--data-dir", str(tmp_path / "relay"), "--workspace", str(workspace),
                 "acp", "--bundle", str(bundle), "--server", str(http.make_url("")).rstrip("/"),
                 "--token-file", str(credential)]
        env = {key: os.environ[key] for key in
               ("PATH", "HOME", "AMPLIFIER_HOME", "AMPLIFIER_WEB_HOME",
                "AMPLIFIER_SESSION_STATE_HOME", "PYTHONDONTWRITEBYTECODE", "UV_OFFLINE")}
        events = []
        original = service._execution_observers.original

        async def observe(kind, payload):
            result = await original(kind, payload)
            events.append((kind, dict(payload)))
            return result

        async def batch(rows):
            result = await original.batch(rows)
            events.extend((kind, dict(payload)) for kind, payload in rows)
            return result

        service._execution_observers.original = type(original)(observe, batch)
        try:
            if connector == "minecraft":
                endpoint = tmp_path / "endpoint.json"
                endpoint.write_text(json.dumps({"command": relay, "cwd": str(workspace), "env": env}))
                result_file = tmp_path / "minecraft-client.json"
                with (tmp_path / "minecraft-client.log").open("xb") as log:
                    process = await asyncio.create_subprocess_exec(
                        str(inputs / "minecraft-venv/bin/python"), "-I", "-B", str(Path(__file__).resolve()),
                        str(inputs), str(endpoint), str(result_file), env=env, stdout=log, stderr=log)
                    try:
                        assert await asyncio.wait_for(process.wait(), 180) == 0, (
                            tmp_path / "minecraft-client.log").read_text()
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                connector_result = json.loads(result_file.read_text())
            else:
                connector_result = await slack_client(inputs, relay, workspace, tmp_path / "slack", env)
            sid = connector_result["session"]
            assert service._session(sid)["title"] == "Native ACP effect 2"
            rows = [json.loads(line) for line in audit.read_text().splitlines()]
            providers = [row for row in rows if row["kind"] == "provider"]
            assert [row["round"] for row in providers] == [1, 2]
            worker_pid = runtime.workers[sid]["process"].pid
            assert {row["pid"] for row in rows} == {worker_pid}
            for row in providers:
                for name, witness in row["modules"].items():
                    path = Path(importlib.import_module(name).__file__).resolve()
                    assert witness == {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            commands = [json.loads(raw) for (raw,) in service.db.execute(
                "SELECT receipt FROM commands WHERE id IN ('native-acp-title-1','native-acp-title-2')")]
            assert len(commands) == 2 and all(row["accepted"] for row in commands)
            delivered = Counter(payload["inputId"] for kind, payload in events
                                if kind == "runtime.status" and payload.get("event") == "input.delivered")
            terminals = Counter(identity for kind, payload in events
                                if kind == "runtime.generation" and payload.get("event") == "generation.finished"
                                for identity in payload.get("input_ids", []))
            assert len(delivered) == 2 and set(delivered.values()) == {1}
            assert terminals == delivered
            (tmp_path / "joined-evidence.json").write_text(json.dumps({
                "connector": connector, "runtime": graph, "provider": providers, "effects": commands,
                "nativeInputs": dict(delivered), "nativeTerminals": dict(terminals), "result": connector_result,
                "limits": ["Scripted provider, loop-live/context-simple, not full Work",
                           "Direct Unified backend only; Instance wrapper qualified separately",
                           "Fake platform; no paid model, live account or world"]}, indent=2) + "\n")
        finally:
            await http.close()


if __name__ == "__main__":
    asyncio.run(minecraft_client(*map(Path, sys.argv[1:])))