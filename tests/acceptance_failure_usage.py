"""Installed receiving-boundary qualification, not Worker/root/delegate proof.

Run with an isolated installed interpreter, outside the checkout, in an
unshare -n namespace with loopback up and an empty environment. The fixtures
use real SDKs and HTTP sockets; no account, inference or generation replay.
"""
import argparse
import asyncio
import errno
import hashlib
import importlib
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
import zipfile

from amplifier_core import HookResult, ModuleCoordinator
from amplifier_core.message_models import ChatRequest, Message
from amplifier_web.execution_events import CURRENT_CALL, CURRENT_PROVIDER, ExecutionEvents
from amplifier_web.browser_detail import project
from amplifier_web.execution import ingest


PRIVATE = "private-receiving-fixture-sentinel"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_openai_fixture(source):
    spec = importlib.util.spec_from_file_location("failure_usage_openai_fixture", source / "tests/test_transport_liveness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnthropicWire:
    def __init__(self, mode):
        self.mode = mode
        self.posts = self.accepted = 0
        self.tasks = set()
        self.release = asyncio.Event()
        self.errors = []

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self.release.set()
        self.server.close()
        await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        assert not self.tasks and not self.errors, self.errors

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            path = headers.split(b" ")[1].split(b"?")[0]
            length = next((int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            await reader.readexactly(length)
            if path == b"/v1/models":
                body = b'{"data":[],"has_more":false,"first_id":null,"last_id":null}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                await writer.drain()
                return
            assert path == b"/v1/messages", path
            self.posts += 1
            self.accepted += 1
            body = {"id": "msg_fixture", "type": "message", "role": "assistant",
                    "model": "claude-sonnet-5-5", "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": {"input_tokens": 125,
                    "cache_read_input_tokens": 10, "cache_creation_input_tokens": 3}}
            event = {"type": "message_start", "message": body}
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                         + f"event: message_start\ndata: {json.dumps(event)}\n\n".encode())
            await writer.drain()
            await self.release.wait()
            writer.transport.abort()
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            self.errors.append(type(exc).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)


def tracker_for(provider):
    public, observed = [], []
    tracker = ExecutionEvents("receiving-root", public.append)
    tracker.turn_id = "receiving-turn"
    coordinator = ModuleCoordinator()
    activity = asyncio.Event()

    async def callback(event, data):
        identity = CURRENT_CALL.get()
        assert identity in tracker.nodes
        assert CURRENT_PROVIDER.get() == id(provider)
        observed.append({"event": event, "call": identity,
                         "status": data.get("status"), "usage": data.get("usage")})
        tracker.hook("receiving-root", event, data)
        if event == "llm:progress" and data.get("observation") == "response_activity":
            activity.set()
        return HookResult()

    for event in ("llm:progress", "llm:response", "llm:stream_aborted"):
        coordinator.hooks.register(event, callback)
    provider.coordinator = coordinator
    return tracker, public, observed, activity


async def receiving(provider, request, wire, *, cancel=False, ready=None):
    tracker, public, observed, activity = tracker_for(provider)
    primary = []
    original = provider.complete

    async def invoke():
        try:
            return await original(request)
        except (Exception, asyncio.CancelledError) as exc:
            primary.append(exc)
            raise

    task = asyncio.create_task(tracker.provider_call("receiving-root", provider, request, invoke))
    try:
        if cancel:
            await asyncio.wait_for((ready or activity).wait(), 3)
            task.cancel("receiving-stop")
        elif isinstance(wire, AnthropicWire):
            await asyncio.wait_for(activity.wait(), 3)
            wire.release.set()
        try:
            await asyncio.wait_for(task, 5)
            raise AssertionError("expected primary failure")
        except (Exception, asyncio.CancelledError) as exc:
            assert primary and exc is primary[0], (type(exc).__name__, primary)
            if cancel:
                assert isinstance(exc, asyncio.CancelledError) and str(exc) == "receiving-stop"
                assert task.cancelling() == 1
            receipt = dict(exc.usage)
        row = next(row for row in tracker.nodes.values() if row["kind"] == "llm")
        terminal = [e for e in observed if e["event"] == "llm:response"]
        assert len(terminal) == 1 and terminal[0]["call"] == row["id"]
        assert terminal[0]["usage"] == receipt
        assert row["phase"] == ("cancelled" if cancel else "error") and row["endedAt"] is not None
        assert all(e["call"] == row["id"] for e in observed)
        session = {"id": "receiving-root"}
        for event in public:
            ingest(session, event["event"])
        projected = project(session)["execution"]["nodes"][0]
        assert projected["usage"] == row["usage"]
        assert PRIVATE not in json.dumps(public)
        assert not any(key in row["usage"] for key in (
            "attempts", "cost_known_subtotal_usd", "messages", "cache_read_input_tokens"))
        assert CURRENT_CALL.get() is None and CURRENT_PROVIDER.get() is None
        return {"phase": row["phase"], "usage": row["usage"], "hook_receipt": receipt,
                "public_node": projected, "posts": wire.posts, "accepted": wire.accepted,
                "native_hook_type": str(type(provider.coordinator.hooks)),
                "primary_preserved": True, "cancellation_count": task.cancelling()}
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def actual_checks(provider_source):
    cases = {}
    live = load_openai_fixture(provider_source)
    for label, inputs, outputs in (("openai-partial-failure", 125, None),
                                   ("openai-zero-failure", 0, 0),
                                   ("openai-complete-estimate", 125, 7)):
        failed = live.response("failed")
        failed["service_tier"] = "default"
        failed["error"] = {"code": "server_error", "message": PRIVATE}
        failed["usage"].update(input_tokens=inputs, output_tokens=outputs,
                               total_tokens=None if outputs is None else inputs + outputs)
        async with live.Loopback("failed_usage", streaming=True, failed_response=failed) as wire:
            provider, transport, _ = live.provider_for(wire, raw=False)
            try:
                result = await receiving(provider, live.request(), wire)
                assert wire.posts == wire.accepted == transport.dispatches == 1
                assert result["usage"]["inputTokens"] == inputs
                if outputs is None:
                    assert not {"outputTokens", "totalTokens", "costUsd"}.intersection(result["usage"])
                else:
                    assert result["usage"]["outputTokens"] == outputs
                    assert result["usage"]["costType"] == "estimated"
                cases[label] = result
            finally:
                await provider.close()
        assert not wire.tasks
    initial = live.response("incomplete")
    initial["service_tier"] = "default"
    async with live.Loopback("continuation_pending", initial_response=initial) as wire:
        provider, transport, _ = live.provider_for(wire, raw=False)
        try:
            result = await receiving(provider, live.request(), wire, cancel=True,
                                     ready=wire.pending_continuation)
            assert wire.posts == wire.accepted == transport.dispatches == 2
            assert result["hook_receipt"]["attempts"][0]["input_tokens"] == 10
            assert result["hook_receipt"]["attempts"][0]["output_tokens"] == 5
            assert result["usage"] == {"costType": "unavailable"}
            cases["openai-unfinished-continuation"] = result
        finally:
            await provider.close()
    import anthropic
    from anthropic import _base_client
    from amplifier_module_provider_anthropic import AnthropicProvider
    transport = getattr(_base_client, "httpx2", None) or _base_client.httpx
    for cancel in (False, True):
        async with AnthropicWire("cancel" if cancel else "reset") as wire:
            provider = AnthropicProvider("offline-placeholder", config={
                "default_model": "claude-sonnet-5-5", "use_streaming": True,
                "max_retries": 0, "max_concurrent_requests": 0, "raw": False})
            provider._client = anthropic.AsyncAnthropic(
                api_key="offline-placeholder", base_url=f"http://127.0.0.1:{wire.port}",
                max_retries=0, http_client=transport.AsyncClient(trust_env=False))
            try:
                request = ChatRequest(messages=[Message(role="user", content=PRIVATE)])
                result = await receiving(provider, request, wire, cancel=cancel)
                assert wire.posts == wire.accepted == 1
                assert result["usage"]["inputTokens"] == 125
                assert result["usage"]["cacheReadTokens"] == 10
                assert result["usage"]["cacheWriteTokens"] == 3
                assert not {"outputTokens", "totalTokens", "costUsd"}.intersection(result["usage"])
                cases["anthropic-partial-" + wire.mode] = result
            finally:
                await provider.close()
        assert not wire.tasks
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    return cases


def preflight(root, provider_evidence):
    assert sys.flags.isolated and sys.flags.dont_write_bytecode and sys.version_info[:2] == (3, 13)
    assert not (Path.cwd() / "amplifier_web").exists()
    assert socket.if_nameindex() == [(1, "lo")]
    routes = subprocess.check_output(["ip", "route", "show"]).decode()
    assert not routes.strip()
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1)
        raise AssertionError("egress allowed")
    except OSError as exc:
        assert exc.errno == errno.ENETUNREACH
    assert not any("proxy" in key.lower() for key in os.environ)
    site = Path(sysconfig.get_path("purelib"))
    from packaging.requirements import Requirement
    pins = {}
    for line in (root / "qualified-requirements.txt").read_text().splitlines():
        req = Requirement(line)
        dist = metadata.distribution(req.name)
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        assert not direct.get("dir_info", {}).get("editable")
        if req.name in {"amplifier-module-provider-openai", "amplifier-module-provider-anthropic"}:
            continue  # exact wheel byte checks below supersede prior provider pins
        if req.url:
            assert direct["vcs_info"]["commit_id"] == req.url.split("@")[-1].split("#")[0]
        else:
            assert dist.version in req.specifier
        pins[req.name] = dist.version
    assert len(pins) == 100
    checks = {}
    unified = next((root / "wheels").glob("amplifier_unified-*.whl"))
    for name, wheel, source in (
        ("unified", unified, root / "source"),
        ("openai", next((provider_evidence / "openai/wheels").glob("*.whl")), provider_evidence / "openai/source"),
        ("anthropic", next((provider_evidence / "anthropic/wheels").glob("*.whl")), provider_evidence / "anthropic/source")):
        count = 0
        with zipfile.ZipFile(wheel) as archive:
            for filename in archive.namelist():
                if filename.endswith("/") or ".dist-info/" in filename:
                    continue
                content = archive.read(filename)
                assert (site / filename).read_bytes() == content, filename
                if (source / filename).is_file():
                    assert (source / filename).read_bytes() == content, filename
                count += 1
        checks[name] = {"wheel_sha256": sha(wheel), "files_exact": count}
    for name, commit in (("openai", "f3035fd97ec973a7d7fa08fe1bfe3bf7cbeb5102"),
                         ("anthropic", "dc6f316115c1b897051f84984051b3d39fa562fd")):
        identity = json.loads((provider_evidence / name / "wheel-identity.json").read_text())
        assert identity["commit"] == commit and identity["wheel_sha256"] == checks[name]["wheel_sha256"]
        checks[name]["commit"] = commit
    origins = {name: importlib.import_module(name).__file__ for name in (
        "amplifier_web.execution_events", "amplifier_core", "amplifier_foundation",
        "amplifier_module_loop_live", "amplifier_module_provider_openai",
        "amplifier_module_provider_anthropic", "openai", "anthropic")}
    assert all(Path(path).is_relative_to(site) for path in origins.values())
    assert metadata.version("amplifier-core") == "2.0.1"
    assert list(site.glob("amplifier_core/*.so"))
    assert metadata.version("openai") == "3.24.0" and metadata.version("anthropic") == "1.11.0"
    return {"pins_checked": pins, "wheels": checks, "origins": origins,
            "namespace_interfaces": socket.if_nameindex(), "routes": routes,
            "external_errno": errno.ENETUNREACH}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--provider-evidence", type=Path, required=True)
    args = parser.parse_args()
    result = {"status": "running", "boundary": "Installed Unified/Core native hooks; synthetic root identities. Not Worker/delegate acceptance."}
    target = args.work_dir / "results/acceptance.json"
    assert not target.exists()
    try:
        result["identity"] = preflight(args.work_dir, args.provider_evidence)
        result["cases"] = asyncio.run(actual_checks(args.provider_evidence / "openai/source"))
        result["status"] = "PASS"
    except BaseException as exc:
        result.update(status="FAIL", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        target.write_text(json.dumps(result, indent=2))
        print(json.dumps(result))


if __name__ == "__main__":
    main()