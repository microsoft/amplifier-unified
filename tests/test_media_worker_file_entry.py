"""File-entry/real-pipe media regression; prepared-source integration is opt-in."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests/fixtures/normal_media_pipe_probe.py"


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE.parent))
    spec = importlib.util.spec_from_file_location("pipe_probe_unit", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipe_fixture_never_constructs_or_injects_worker_graph_provider_or_binding():
    tree = ast.parse(PROBE.read_text())
    calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))}
    assert not calls & {"Worker", "NamedMediaGraph", "select_from_declaration", "mint_owner",
                        "bind_media", "acknowledge_media", "create_new", "force_enabled",
                        "AsyncOpenAI", "OpenAIProvider"}
    assert "create_subprocess_exec" in calls and "host_result" in calls
    assert not any(isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Subscript)
                           and isinstance(target.value, ast.Attribute)
                           and target.value.attr == "modules" for target in node.targets)
                   for node in ast.walk(tree)), "No sys.modules canonicalization workaround"
    patches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "object"
               and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)]
    assert {node.args[1].value for node in patches} == {
        "connect", "connect_ex", "handle_async_request"}


def test_pipe_private_parent_is_checked_not_repaired(probe, tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(AssertionError, match="0700"):
        probe.private_parent(tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    tmp_path.chmod(0o700)
    probe.private_parent(tmp_path)
    (tmp_path / "occupied").touch()
    with pytest.raises(AssertionError, match="fresh empty"):
        probe.private_parent(tmp_path)


def test_pipe_socket_denial_records_both_connection_paths_without_network(probe, tmp_path):
    from contextlib import ExitStack
    import socket
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    report = tmp_path / "denials.json"
    with ExitStack() as stack:
        denied = probe.deny_socket_connections(stack, report)
        with socket.socket() as client:
            for operation in (client.connect, client.connect_ex):
                with pytest.raises(AssertionError, match="denied"):
                    operation(("192.0.2.1", 443))
        assert len(denied) == 2
        assert json.loads(report.read_text()) == denied
    assert socket.socket.connect is original_connect and socket.socket.connect_ex is original_connect_ex
    tree = ast.parse(PROBE.read_text())
    bootstrap = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bootstrap")
    calls = [node for node in ast.walk(bootstrap) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id in {"deny_socket_connections", "helpers"}]
    assert next(node.lineno for node in calls if node.func.id == "deny_socket_connections") < next(
        node.lineno for node in calls if node.func.id == "helpers")


def test_pipe_finite_context_prepare_readback_and_unknown_service(probe):
    from amplifier_web.preparation_context import OPERATIONS
    result = probe.host_result({"operation": "context.prepare", "args": {"_contextInputs": ["one"]}})
    assert set(result) == set(OPERATIONS)
    assert result["context.manifest"]["value"] == {"surfaces": [], "inputIds": ["one"]}
    assert result["coordination.current"]["value"]["sessionId"] == probe.SESSION
    assert result["message.reactions.context"] == {"available": False}
    assert probe.host_result({"operation": "capacity.admit",
                              "args": {"call": {"sessionId": probe.SESSION}}}) == {
                                  "allowed": True, "budgetRevision": 0}
    with pytest.raises(AssertionError):
        probe.host_result({"operation": "capacity.admit", "args": {"call": {"sessionId": "foreign"}}})
    with pytest.raises(AssertionError, match="Unexpected"):
        probe.host_result({"operation": "dispatch", "args": {"action": "anything"}})


async def test_pipe_until_rejects_eof_instead_of_inventing_retirement_ack(probe):
    class Writer:
        def write(self, value):
            pass

        async def drain(self):
            pass

    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_eof()
    stderr.feed_eof()
    from types import SimpleNamespace
    pipe = probe.Pipe(SimpleNamespace(stdout=stdout, stderr=stderr, stdin=Writer()), b"private")
    with pytest.raises(AssertionError, match="EOF"):
        await pipe.until(lambda event: event.get("result") == {"retired": True})
    await pipe.reader
    await pipe.stderr


async def test_pipe_reader_preserves_real_json_and_answers_bridge_through_stdin(probe):
    from types import SimpleNamespace
    from amplifier_web.runtime_protocol import encode_message

    class Writer:
        def __init__(self):
            self.frames = []

        def write(self, value):
            self.frames.append(json.loads(value))

        async def drain(self):
            pass

    stdout, stderr, writer = asyncio.StreamReader(), asyncio.StreamReader(), Writer()
    event = {"op": "bridge", "id": "prepare", "operation": "context.prepare",
             "args": {"_contextInputs": ["one"]}}
    stdout.feed_data(encode_message(event))
    stdout.feed_eof()
    stderr.feed_eof()
    pipe = probe.Pipe(SimpleNamespace(stdout=stdout, stderr=stderr, stdin=writer), b"private")
    await pipe.reader
    assert pipe.events == [event]
    assert writer.frames == [{"op": "bridge.result", "id": "prepare", "result": probe.host_result(event)}]
    await pipe.stderr


def test_actual_media_file_entry_json_pipes_count_send_idle_retire_ack_and_resume(tmp_path):
    if os.environ.get("NORMAL_MEDIA_FILE_ENTRY") != "1":
        pytest.skip("set NORMAL_MEDIA_FILE_ENTRY=1 with an already prepared source runtime/PYTHONPATH")
    if sys.platform != "linux":
        pytest.skip("Prospective canonical source custody is qualified on Linux only")
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    assert python, "Select an existing prepared interpreter; this test never installs"
    tmp_path.chmod(0o700)
    result = subprocess.run([python, str(PROBE), str(tmp_path)], cwd=ROOT, text=True,
                            capture_output=True, timeout=120,
                            env={**os.environ, "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    # Keep the consumed, pixel-free parent pipe receipts alongside both exact
    # child observers and stderr readbacks, after the fresh-root launch finished.
    (tmp_path / "pipe-result.json").write_text(result.stdout)
    assert evidence["mode"] == "source-only" and evidence["installed_package_parity"] is False
    assert evidence["real_file_entry"][1] == str(ROOT / "amplifier_web/runtime_worker.py")
    for name in ("actual_vendor_provider_and_sdk", "offline_transport_not_inference",
                 "two_turn_idle_custody", "fresh_process_resume_without_replay",
                 "canonical_t2_ack", "canonical_and_protocol_no_pixels"):
        assert evidence[name] is True
    assert len(evidence["children"]) == len(evidence["receipts"]) == 2
    assert evidence["children"][0]["pid"] != evidence["children"][1]["pid"]
    for child, receipt, turns in zip(evidence["children"], evidence["receipts"], (2, 1)):
        assert child["final"] and not child["failures"] and not child["socket_denials"]
        assert child["canonical_cleanup_joined"] and child["private_requests_cleared"]
        assert child["file_bootstrapped_app_without_outer_root"]
        assert {"httpx", "httpx2"} <= set(child["transport_packages"])
        assert all(row["wire_http_package"] == "httpx2" for row in child["snapshots"])
        assert sum(row["phase"] == "send" for row in child["records"]) == turns
        assert sum(row["phase"] == "count" for row in child["records"]) >= turns
        assert child["source"]["catalog"]["recipe"] == "unified-openai-static-sol-local-capture-v1"
        assert not child["source"]["catalog"]["mismatches"]
        assert len(child["typed_receipts"]) == len(child["records"])
        for typed in child["typed_receipts"]:
            assert typed["receipt_type"] == "VersionedCheckpointReceipt"
            assert typed["result_type"] == "CommitAck" and typed["refs"]
            assert typed["prefix"]["session_id"] == "normal-media-file-entry"
        assert evidence["cas_scope"].startswith("successful exact-prefix path")
        assert receipt["reply_before_eof"] and receipt["exit_code"] == 0
        assert receipt["retirement_reply_index"] < receipt["stdout_eof_index"]
        assert "context.prepare" in receipt["bridge_operations"]