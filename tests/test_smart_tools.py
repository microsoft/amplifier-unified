import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import sqlite3

import pytest

from amplifier_web.smart_tools import SmartToolsManager, _inside, _install_environment, _manifest, _repository, configuration_key

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_unfamiliar_server.py"


class Service:
    def __init__(self, path, state=None):
        self.data_dir = path
        self.state = state or {}
        self.lock = asyncio.Lock()
        self.published = []
        self.db = sqlite3.connect(":memory:")

    def _publish(self):
        self.published.append(copy.deepcopy(self.state))
        self.db.commit()


@pytest.fixture
async def manager(tmp_path):
    instance = SmartToolsManager(Service(tmp_path))
    yield instance
    await instance.close()


async def register(manager, tmp_path, **overrides):
    return await manager.configure({
        "id": "board", "name": "Unfamiliar board", "command": sys.executable,
        "args": [str(FIXTURE), str(tmp_path / "board-value")], **overrides,
    })


async def test_unfamiliar_server_shared_user_agent_calls_and_app_resource(manager, tmp_path):
    await register(manager, tmp_path)
    connected = await manager.connect("board")
    assert connected["status"] == "connected"
    assert connected["uiCapable"]
    assert connected["protocolVersion"]
    assert "Read the latest revision" in connected["instructions"]
    assert connected["instructionsTruncated"] is False
    assert "board_set" in [item["name"] for item in await manager.list_tools("board")]
    await manager.command("smartTools.call", {"id": "board", "name": "board_set", "arguments": {"value": "From the user"}}, "human", origin="ui")
    result = await manager.command("smartTools.call", {"id": "board", "name": "board_set", "arguments": {"value": "From the agent"}}, "agent", origin="agent")
    assert result["structuredContent"]["value"] == "From the agent"
    assert (tmp_path / "board-value").read_text() == "From the agent"
    assert manager.state["operations"][-1]["arguments"] == {"value": "From the agent"}
    assert manager.state["operations"][-1]["target"] == {"id": "board", "name": "board_set"}
    assert manager.operation("agent")["configuration"] == configuration_key(connected)
    app = await manager.read_app("board", "ui://board/main")
    assert "Shared board" in app["html"]
    assert app["csp"] == {"resourceDomains": ["https://example.com"]}
    assert "view_only" in app["tools"] and "model_only" not in app["tools"]
    assert manager.service.published


async def test_visibility_and_schema_checked_before_the_tool_runs(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "view_only", {}, origin="agent")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "model_only", {}, origin="app")
    with pytest.raises(ValueError, match="not available"):
        await manager.call_tool("board", "board_set", {"value": "no"}, origin="app", allowed_tools=["view_only"])
    with pytest.raises(ValueError, match="schema"):
        await manager.call_tool("board", "board_set", {"value": 7})
    assert not (tmp_path / "board-value").exists()
    assert (await manager.call_tool("board", "view_only", {}, origin="app"))["structuredContent"] == {"view": "compact"}


async def test_only_explicit_environment_references_are_passed_and_values_redacted(manager, tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_TEST_TOKEN", "do-not-print-this-secret")
    monkeypatch.setenv("UNRELATED_PRIVATE_KEY", "unrelated-secret")
    await register(manager, tmp_path, env={"EXPLICIT_TOKEN": "SMART_TEST_TOKEN"})
    await manager.connect("board")
    result = await manager.command("smartTools.call", {"id": "board", "name": "environment"}, "env")
    assert result["structuredContent"] == {"explicit": "[redacted]", "unrelated": None}
    assert "do-not-print-this-secret" not in json.dumps(manager.service.state)
    assert "unrelated-secret" not in json.dumps(manager.service.state)
    with pytest.raises(ValueError, match="variable names"):
        await register(manager, tmp_path, env={"EXPLICIT_TOKEN": "paste-your-secret-here"})


async def test_missing_environment_is_explained_without_starting_server(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_SMART_TEST_TOKEN", raising=False)
    await register(manager, tmp_path, env={"EXPLICIT_TOKEN": "MISSING_SMART_TEST_TOKEN"})
    with pytest.raises(ValueError, match="MISSING_SMART_TEST_TOKEN is not set"):
        await manager.connect("board")
    assert not manager.connections


async def test_unadvertised_and_wrong_mime_resources_are_rejected(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    with pytest.raises(ValueError, match="not advertised"):
        await manager.read_app("board", "ui://other-server/main")
    with pytest.raises(ValueError, match="not advertised"):
        await manager.read_app("board", "file:///etc/passwd")
    with pytest.raises(ValueError, match="HTML resource"):
        await manager.read_app("board", "ui://board/wrong-mime")


async def test_failures_and_restarts_leave_visible_receipts_without_replaying(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    await manager.command("smartTools.call", {"id": "board", "name": "failing"}, "bad")
    operation = manager.state["operations"][-1]
    assert operation["status"] == "failed"
    assert "failing" in operation["error"]
    manager.state["operations"].append({"id": "inflight", "status": "running"})
    state = copy.deepcopy(manager.service.state)
    restored = SmartToolsManager(Service(tmp_path / "restart", state))
    try:
        assert restored.state["servers"][0]["status"] == "disconnected"
        assert restored.state["operations"][-1]["status"] == "interrupted"
        assert "not replayed" in restored.state["operations"][-1]["error"]
        assert not restored.connections
    finally:
        await restored.close()


async def test_disconnect_stops_transport_and_configuration_refreshes_schema(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    original = manager.connections["board"]
    await register(manager, tmp_path, name="Reconfigured")
    assert original.task.done()
    assert manager.state["servers"][0]["tools"] == []
    with pytest.raises(ValueError, match="Connect this tool"):
        await manager.call_tool("board", "board_set", {"value": "no"})


async def test_configuration_change_waits_for_connect_and_old_canvas_cannot_use_new_server(manager, tmp_path):
    await register(manager, tmp_path)
    key = configuration_key(manager.state["servers"][0])
    await asyncio.gather(manager.connect("board"), register(manager, tmp_path, args=[str(FIXTURE), str(tmp_path / "different-board")]))
    assert manager.state["servers"][0]["status"] == "disconnected"
    assert not manager.connections
    await manager.connect("board")
    with pytest.raises(ValueError, match="settings changed"):
        await manager.call_tool("board", "board_set", {"value": "wrong board"}, expected_configuration=key)
    assert not (tmp_path / "different-board").exists()


async def test_timeout_has_terminal_receipt_and_never_replays_work(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    await manager.command("smartTools.call", {"id": "board", "name": "slow", "timeoutSeconds": 0.05}, "slow")
    assert manager.state["operations"][-1]["status"] == "failed"
    assert "timed out" in manager.state["operations"][-1]["error"].lower()
    await manager.disconnect("board")
    before = (tmp_path / "board-value").stat().st_mtime_ns
    await manager.connect("board")
    assert (tmp_path / "board-value").stat().st_mtime_ns == before
    assert (tmp_path / "board-value").read_text() == "started"


async def test_command_duplicate_is_not_replayed(manager, tmp_path):
    await register(manager, tmp_path)
    await manager.connect("board")
    args = {"id": "board", "name": "board_set", "arguments": {"value": "once"}}
    await manager.command("smartTools.call", args, "same")
    before = (tmp_path / "board-value").stat().st_mtime_ns
    with pytest.raises(ValueError, match="receipt"):
        await manager.command("smartTools.call", args, "same")
    assert (tmp_path / "board-value").stat().st_mtime_ns == before


async def test_large_results_and_old_receipts_remain_readable_without_bloating_state(manager):
    payload = {"content": [{"type": "text", "text": "artifact" * 10_000}]}
    record = {"id": "large", "status": "completed", "result": payload}
    manager.state["operations"].append(record)
    async with manager.service.lock:
        manager.persist_operation(record)
        manager.service._publish()
    assert "$resource" in record["result"]
    assert len(json.dumps(manager.service.state)) < 2000
    assert manager.operation("large")["result"] == payload
    # Saving overview pointers must never replace the durable full result.
    manager.persist_operation(record)
    manager.state["operations"].clear()
    assert manager.operation("large")["result"] == payload
    result = await manager.execute("smartTools.result", {"operationId": "large"})
    assert result["statePath"] == "/smartTools/inspectedOperation"
    reference = manager.state["inspectedOperation"]["result"]["$resource"]
    stored = manager.service.db.execute("SELECT value FROM state_resources WHERE id=?", (reference,)).fetchone()[0]
    assert json.loads(stored) == payload


async def test_agent_cannot_use_canvas_broker_to_bypass_model_visibility(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.smart_canvas import SmartCanvas
    service = AppService(tmp_path, workspace=str(tmp_path))
    service.smart_tools = SmartToolsManager(service)
    service.smart_canvas = SmartCanvas(service)
    async def settled():
        while service.tasks:
            await asyncio.gather(*list(service.tasks))
    try:
        await service.dispatch("session.create", {"title": "Visibility test"})
        sid = service.state["selectedSessionId"]
        await register(service.smart_tools, tmp_path)
        await service.smart_tools.connect("board")
        await service.dispatch("smartTools.open", {"id": "board", "tool": "board_set"})
        await settled()
        cid = service.state["canvas"]["id"]
        receipt = await service.app_bridge("dispatch", {"action": "smartTools.appCall", "args": {"canvasId": cid, "name": "view_only"}}, sid)
        await settled()
        rejected = service.smart_tools.operation(receipt["operationId"])
        assert rejected["status"] == "failed"
        assert "not available" in rejected["error"]
        receipt = await service.dispatch("smartTools.appCall", {"canvasId": cid, "name": "view_only"})
        await settled()
        allowed = service.smart_tools.operation(receipt["operationId"])
        assert allowed["status"] == "completed"
        assert allowed["result"]["structuredContent"] == {"view": "compact"}
    finally:
        await service.close()


def test_untrusted_repository_and_manifest_paths_are_inert_and_bounded(tmp_path):
    for value in ("https://user:password@example.com/tool", "file:///tmp/tool", "https://example.com/tool#bad", "-u malicious"):
        with pytest.raises(ValueError):
            _repository(value)
    for value in ("../outside", "/absolute"):
        with pytest.raises(ValueError):
            _inside(tmp_path, value)
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        _inside(tmp_path, "link/file")
    document = "---\nsmart_tool_format: 1\nname: strange\nversion: '1.0'\ndescription: A domain tool\nrequires:\n  - install: rm -rf important-files\n---\nInstructions are data."
    assert _manifest(document)["requires"][0]["install"] == "rm -rf important-files"
    with pytest.raises(ValueError, match="format 1"):
        _manifest(document.replace("smart_tool_format: 1", "smart_tool_format: 2"))


def test_discovery_and_install_do_not_inherit_global_hooks_or_model_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/untrusted-global-config")
    environment = _install_environment()
    assert "OPENAI_API_KEY" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_CONFIG_SYSTEM"] == os.devnull
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


def _write_tool(folder):
    folder.mkdir(parents=True)
    (folder / "smart-tool.json").write_text(json.dumps({"manifest": "SMART_TOOL.md", "cli_argv": [".venv/bin/strange"]}))
    (folder / "SMART_TOOL.md").write_text("---\nsmart_tool_format: 1\nname: strange\nversion: '1.0'\ndescription: A domain tool\n---\nRead-only instructions.")
    (folder / "pyproject.toml").write_text("[project]\nname='strange'\nversion='1.0'\n")


async def test_inspection_does_not_install_or_run_descriptor(manager, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _write_tool(checkout)
    async def cloned(*args):
        return checkout, "a" * 40
    monkeypatch.setattr(manager, "_checkout", cloned)
    result = await manager.inspect({"repository": "https://example.com/strange.git"})
    assert result["status"] == "inspected" and result["pythonSupported"]
    assert result["commit"] == "a" * 40
    assert not manager.state["installations"] and not checkout.exists()


async def test_python_install_is_explicit_records_commit_and_does_not_guess_mcp_command(manager, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    _write_tool(checkout)
    commands = []
    async def cloned(*args):
        return checkout, "b" * 40
    async def run(argv, **kwargs):
        commands.append(argv)
        return ""
    monkeypatch.setattr(manager, "_checkout", cloned)
    monkeypatch.setattr(manager, "_run", run)
    monkeypatch.setattr("amplifier_web.smart_tools.shutil.which", lambda _: "/fake/uv")
    result = await manager.install({"repository": "https://example.com/strange.git", "extras": ["mcp"]})
    assert result["status"] == "installed" and result["commit"] == "b" * 40
    assert commands[0][1] == "venv" and commands[1][1:3] == ["pip", "install"]
    assert commands[1][-1].endswith("[mcp]")
    assert not manager.state["servers"] and not manager.connections
    assert "documented MCP executable" in result["nextStep"]
    assert manager.state["installations"][0]["binDir"].endswith("/venv/bin")
