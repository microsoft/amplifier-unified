"""Bounded installed Windows candidate check, NOT public activation acceptance.

Uses the maintained worker_startup_probe's local-module/settings pattern. Only
the provider is scripted; Core, Foundation, loop, context, capture, filesystem,
Worker, RuntimeManager, HTTPS and auth handlers are real. No rendered browser,
Scheduled Task, installer, default bundle or real provider is qualified here.
Run only in the dedicated GitHub Windows job. Never upload its private scratch.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
import ctypes
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import tomllib
import types
import uuid
import zipfile


MODULES = {
    "loop-live": "amplifier_module_loop_live",
    "context-simple": "amplifier_module_context_simple",
    "hook-context-intelligence": "amplifier_module_hook_context_intelligence",
    "tool-filesystem": "amplifier_module_tool_filesystem",
}
IMPORTS = {
    "amplifier_web": "amplifier-unified",
    "amplifier_web.runtime": "amplifier-unified",
    "amplifier_web.runtime_worker": "amplifier-unified",
    "amplifier_web.host.session": "amplifier-unified",
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_streaming": "amplifier-module-loop-streaming",
    **{value: value.replace("_", "-") for value in MODULES.values()},
}
STAGES = {"private_fixture_creation", "installed_host", "fixture", "credentials_tls",
          "grant_cookie_csrf", "first_worker_turn", "output_snapshot", "stop_tree",
          "host_restart_resume", "private_runtime_project_api", "native_prerequisites",
          "controls", "first", "resume", "complete"}
CHECKS = STAGES | {"privateTlsGrantCookieCsrfReplay", "inheritedAccessLookup",
          "realWorkerToolUtf8History", "hostRestartResumeWithoutReplay",
          "outputSnapshotReadExactBytes", "stopDescendantTreeAndIo",
          "privateRuntimeProjectApi", "maintainedNativePrerequisites",
          "twoFreshHostsAndInstalledWorkerGraphs", "unexpectedPythonNetworkAttempts"}
# Exact maintained native cells, not a file-wide/full-suite gate. Keep explicit
# parameter IDs so a missing forced-cleanup branch cannot hide behind one pass.
NATIVE_CONTROLS = {
    "prepare_private_refuse_unsafe": "test_runtime_updates.py::test_native_windows_prepare_project_private_before_payload_and_refuse_unsafe",
    "auth_converge": "test_windows_auth.py::test_native_credentials_converge_and_existing_read",
    "auth_empty": "test_windows_auth.py::test_native_empty_existing_refused_without_replacement",
    "auth_foreign_acl": "test_windows_auth.py::test_native_foreign_read_acl_never_repaired",
    "auth_alias": "test_windows_auth.py::test_native_case_alias_refused",
    "auth_junction": "test_windows_auth.py::test_native_junction_not_resolved_before_refusal",
    "auth_wrong_leaf_no_disclosure": "test_browser_entry.py::test_native_unrelated_listener_and_proxy_never_receive_bearer[pyloop-wrong-leaf]",
    "auth_redirect_proxy_no_disclosure": "test_browser_entry.py::test_native_unrelated_listener_and_proxy_never_receive_bearer[pyloop-redirect]",
    "runtime_identity_pipes": "test_runtime.py::test_native_windows_runtime_worker_identity_pipes_and_job_cleanup[False]",
    "runtime_forced_cleanup": "test_runtime.py::test_native_windows_runtime_worker_identity_pipes_and_job_cleanup[True]",
    "runtime_cancel_identity": "test_runtime.py::test_native_windows_runtime_cancel_before_identity_reaps_job",
    "optional_full_app_reload": "test_optional_controller_availability.py::test_native_default_selection_full_app_chat_save_reload",
    "read_workspace_utf8": "test_windows_read_files.py::test_native_inherited_workspace_utf8_spaces_and_no_private_acl_requirement",
    "read_sharing_release": "test_windows_read_files.py::test_native_held_handles_block_data_write_leaf_and_parent_rename_then_release",
    "read_hardlink_limit": "test_windows_read_files.py::test_native_missing_directory_hardlink_and_limit_refuse_without_mutation",
    "read_junction": "test_windows_read_files.py::test_native_junctions_refuse_before_resolve_even_when_target_is_inside",
    "resource_hash_corruption": "test_windows_read_files.py::test_native_resource_hash_projection_sync_resolution_and_corruption",
    "resource_publish_overlap": "test_windows_read_files.py::test_resource_publication_of_distinct_blob_during_held_projection",
}
NATIVE_FILES = tuple(sorted({node.split("::")[0] for node in NATIVE_CONTROLS.values()}
                            | {"conftest.py", "test_service.py"}))
TLS_NEGATIVE_MODES = {
    "tests/" + NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"]: "wrong-leaf",
    "tests/" + NATIVE_CONTROLS["auth_redirect_proxy_no_disclosure"]: "redirect",
}
TLS_NEGATIVE_NODES = frozenset(TLS_NEGATIVE_MODES)
MARKER = "Windows trial café 日本語"
RESUMED = "Resumed café 日本語"
# Source-attested urllib3 2.x capability probe. We BLOCK its bind, never allow
# it. Verify the exact compiled caller, closed socket and HAS_IPV6=False.
IPV6_PROBE = '''
def _has_ipv6(host: str) -> bool:
    """Returns True if the system can bind an IPv6 address."""
    sock = None
    has_ipv6 = False
    if socket.has_ipv6:
        try:
            sock = socket.socket(socket.AF_INET6)
            sock.bind((host, 0))
            has_ipv6 = True
        except Exception:
            pass
    if sock:
        sock.close()
    return has_ipv6
'''
PROVIDER = r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
__amplifier_module_type__ = "provider"
class Provider:
    name = "fixture"
    def __init__(self, coordinator, config):
        self.coordinator, self.config = coordinator, config
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Offline fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        # Core's module-validator request is not a conversation or tool effect.
        if self.coordinator.session_id == "test-session" or not request.tools:
            return ChatResponse(content=[TextBlock(text="Offline fixture")])
        from amplifier_web.access_mode import read_mode
        assert read_mode(Path(os.environ["AMPLIFIER_WEB_HOME"]),
            Path(self.config["output"]).parent, self.coordinator.session_id) == "full"
        with Path(self.config["calls"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid":os.getpid(), "access":"full"}) + "\n")
        text = str(next(m.content for m in reversed(request.messages) if m.role == "user"))
        if "WAIT_FOR_STOP" in text:
            child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(180)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path(self.config["blocked"]).write_text(json.dumps({"pid":child.pid}), encoding="utf-8")
            await asyncio.Event().wait()
        if "RESUME_TRIAL" in text:
            assert any("Windows trial café 日本語" in str(m.content) for m in request.messages)
            return ChatResponse(content=[TextBlock(text="Resumed café 日本語")])
        if not any(m.role == "tool" and m.tool_call_id == "trial-write" for m in request.messages):
            return ChatResponse(content=[], tool_calls=[ToolCall(id="trial-write", name="write_file",
                arguments={"file_path":self.config["output"], "content":"Windows trial café 日本語\n"})])
        return ChatResponse(content=[TextBlock(text="Windows trial café 日本語")])
async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(coordinator, config or {}), name="fixture")
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def candidate(source):
    """Clean CI checkout identity, not the pull request's untested head."""
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], timeout=15).decode().strip()
    assert not git("status", "--porcelain")
    result = {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}")}
    assert all(re.fullmatch(r"[0-9a-f]{40,64}", value) for value in result.values())
    return result


def requirements(path):
    """Use the maintained exact dev module sources, not another pinned graph."""
    dev = tomllib.loads(path.read_text(encoding="utf-8"))["dependency-groups"]["dev"]
    selected = [value for value in dev if any(value.startswith(
        module.replace("_", "-") + " @") for module in MODULES.values())]
    assert len(selected) == len(MODULES)
    return selected


def installed(wheel):
    """Verify noneditable candidate bytes and import origins, never add checkout."""
    dist = metadata.distribution("amplifier-unified")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable")
    assert direct.get("url") == wheel.resolve().as_uri()
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith("/") or not name.startswith("amplifier_"):
                continue
            assert ".." not in Path(name).parts
            assert Path(dist.locate_file(name)).read_bytes() == archive.read(name)
            count += 1
    assert count > 100
    origins = {}
    for package, distribution in IMPORTS.items():
        module = importlib.import_module(package)
        path = Path(module.__file__).resolve()
        owner = metadata.distribution(distribution)
        expected = {Path(owner.locate_file(f)).resolve() for f in owner.files or ()}
        assert path in expected and path.is_relative_to(Path(sys.prefix).resolve())
        origins[package] = {"origin": "installed", "sha256": digest(path.read_bytes())}
    from packaging.version import Version
    assert Version(metadata.version("amplifier-core")) >= Version("2.0.2")
    from amplifier_foundation.session import prepare_session_directory
    assert callable(prepare_session_directory)
    graph = []
    for item in metadata.distributions():
        name, version = item.metadata["Name"], item.version
        assert re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
        assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", version)
        row = {"name": name, "version": version}
        provenance = json.loads(item.read_text("direct_url.json") or "{}")
        commit = provenance.get("vcs_info", {}).get("commit_id")
        if commit:
            assert re.fullmatch(r"[0-9a-f]{40,64}", commit)
            row["commit"] = commit
        assert not provenance.get("dir_info", {}).get("editable")
        graph.append(row)
    return {"wheelSha256": digest(wheel.read_bytes()), "verifiedMembers": count,
            "imports": origins, "graph": sorted(graph, key=lambda row: row["name"].lower())}


def validate_public(value):
    """Fail closed before upload, including nested child evidence and metadata."""
    def sha(value, lengths=(64,)):
        assert type(value) is str and len(value) in lengths and re.fullmatch("[0-9a-f]+", value)
    def number(value):
        assert type(value) is int and 0 <= value <= 10**9
    def installation(item):
        assert set(item) == {"wheelSha256", "verifiedMembers", "imports", "graph"}
        sha(item["wheelSha256"]); number(item["verifiedMembers"])
        assert set(item["imports"]) == set(IMPORTS)
        for row in item["imports"].values():
            assert set(row) == {"origin", "sha256"} and row["origin"] == "installed"
            sha(row["sha256"])
        for row in item["graph"]:
            assert {"name", "version"} <= set(row) <= {"name", "version", "commit"}
            assert re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", row["name"])
            assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", row["version"])
            if "commit" in row:
                sha(row["commit"], (40, 64))
    common = {"status", "stage", "checks", "failureKind", "errno", "winerror"}
    def checks(item):
        assert item["status"] in {"PASS", "FAIL"} and item["stage"] in STAGES
        for key, value in item["checks"].items():
            assert key in CHECKS
            if key == "unexpectedPythonNetworkAttempts":
                number(value)
            else:
                assert value in {"PASS", "FAIL"}
        if "failureKind" in item:
            assert item["failureKind"] in {"assertion", "permission", "missing", "timeout", "import", "other"}
        for key in {"errno", "winerror"} & item.keys():
            number(item[key])
    assert set(value) <= common | {"candidate", "qualification", "publicAcceptance", "runtimePreparation",
        "outerJobsSettled", "scratchRemoved", "python", "windowsBuild", "hosts", "workers", "nativeControls"}
    checks(value)
    assert set(value["candidate"]) == {"head", "tree"}
    for commit in value["candidate"].values():
        sha(commit, (40, 64))
    assert value["publicAcceptance"] is False
    assert value["qualification"] == "private-adapter-candidate-only"
    assert value["runtimePreparation"] == "separate-private-project-api-plus-preinstalled-worker"
    assert len(value["python"]) == 3
    for version in value["python"]:
        number(version)
    number(value["windowsBuild"])
    for key in {"outerJobsSettled", "scratchRemoved"} & value.keys():
        assert type(value[key]) is bool
    for item in value.get("hosts", []):
        assert set(item) <= common | {"installed", "innerJobsSettled", "publicGateClosed", "network"}
        checks(item)
        if "installed" in item:
            installation(item["installed"])
        for key in {"innerJobsSettled", "publicGateClosed"} & item.keys():
            assert type(item[key]) is bool
        if "network" in item:
            assert set(item["network"]) == {"blockedOptionalIpv6Probes", "unknownAttempts"}
            for count in item["network"].values():
                number(count)
    for item in value.get("workers", []):
        installation(item)
    if "nativeControls" in value:
        item = value["nativeControls"]
        assert set(item) <= common | {"installed", "selection", "testSources", "network"}
        checks(item)
        if "installed" in item:
            installation(item["installed"])
        if "testSources" in item:
            assert set(item["testSources"]) == set(NATIVE_FILES)
            for fingerprint in item["testSources"].values():
                sha(fingerprint)
        if "network" in item:
            assert set(item["network"]) == {
                "blockedOptionalIpv6Probes", "unknownAttempts", "infrastructureSocketpairBinds",
                "tlsNegativeReservations", "tlsNegativeListeners", "tlsNegativeConnects"}
            for count in item["network"].values():
                number(count)
        if "selection" in item:
            selection = item["selection"]
            assert set(selection) == {"status", "exitCode", "collectionExact", "cases"}
            assert selection["status"] in {"PASS", "FAIL"}
            assert type(selection["collectionExact"]) is bool
            number(selection["exitCode"])
            assert set(selection["cases"]) == set(NATIVE_CONTROLS)
            for case in selection["cases"].values():
                assert set(case) == {"status", "collected", "callsPassed", "skips", "failures"}
                assert case["status"] in {"PASS", "FAIL"}
                for key in ("collected", "callsPassed", "skips", "failures"):
                    number(case[key])
                if case["status"] == "PASS":
                    assert (case["collected"], case["callsPassed"], case["skips"], case["failures"]) == (1, 1, 0, 0)
            if selection["status"] == "PASS":
                assert selection["collectionExact"] and selection["exitCode"] == 0
                assert all(case["status"] == "PASS" for case in selection["cases"].values())
    return value


class NetworkGuard:
    """Deny and retain every unrecognized attempt, even if a library catches it.

    The only allowed network is this process's one reserved TLS listener and
    numeric connects to that exact port. Worker processes allow no network.
    This is Python-audit coverage, not an adversarial native-code sandbox.
    """
    def __init__(self, log):
        self.log, self.port, self.reserving = Path(log), None, False
        self.blocked = 0
        self.probe_socket = None
        self.probes = 0
        self.pair_depth = self.pair_binds = 0
        self.tls_scope = ContextVar("trial_tls_negative_case", default=None)
        self.tls_binding = None
        self.tls_reservations = self.tls_connects = 0
        self.tls_endpoints = []
        # Read metadata/source WITHOUT importing urllib3 or any app module.
        self.probe_path = Path(metadata.distribution("urllib3").locate_file(
            "urllib3/util/connection.py")).resolve()
        source = self.probe_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_has_ipv6")
        assert ast.dump(function) == ast.dump(ast.parse(IPV6_PROBE).body[0])
        compiled = compile(source, str(self.probe_path), "exec", dont_inherit=True)
        self.probe_code = next(c for c in compiled.co_consts if isinstance(c, types.CodeType)
                               and c.co_name == "_has_ipv6")

    def control_socketpair(self, event, args):
        # pytest-asyncio constructs fresh Windows event loops. Their synchronous
        # stdlib self-pipe is NOT app networking. Only our scoped socketpair
        # wrapper and the exact loaded stdlib fallback may create this one pair.
        if not self.pair_depth or event not in {"socket.bind", "socket.connect"}:
            return False
        frame = sys._getframe(1)
        for _ in range(8):
            if frame is None:
                return False
            if frame.f_code is socket._fallback_socketpair.__code__:
                if (frame.f_globals.get("__name__") != "socket"
                        or Path(frame.f_globals["__file__"]).resolve() != Path(socket.__file__).resolve()):
                    return False
                if event == "socket.bind":
                    valid = args[0] is frame.f_locals.get("lsock") and args[1] == ("127.0.0.1", 0)
                    if valid:
                        self.pair_binds += 1
                    return valid
                return (args[0] is frame.f_locals.get("csock")
                        and args[1] == frame.f_locals["lsock"].getsockname()[:2]
                        and args[1][0] == "127.0.0.1")
            frame = frame.f_back
        return False

    def control_tls_network(self, event, args):
        if event == "socket.bind" and self.tls_binding is not None:
            stream, address = self.tls_binding
            return args[0] is stream and args[1] == address
        scope = self.tls_scope.get()
        if event != "socket.connect" or scope is None:
            return False
        for endpoint in scope.endpoints:
            # Both the pinned listener and the proxy/redirect sink are reachable:
            # disclosure must fail the original test's oracle, not be hidden by
            # this guard. No different listener at the same port is admitted.
            if (endpoint.server.started and not endpoint.server.closed
                    and endpoint.stream.fileno() >= 0
                    and args[1] == endpoint.stream.getsockname()[:2]
                    and args[1][0] == "127.0.0.1"):
                self.tls_connects += 1
                return True
        return False

    def audit(self, event, args):
        if event not in {"socket.bind", "socket.connect", "socket.getaddrinfo", "socket.sendto",
                         "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
            return
        address = args[1] if event in {"socket.bind", "socket.connect"} else None
        if self.control_socketpair(event, args):
            return
        if self.control_tls_network(event, args):
            return
        if event == "socket.bind" and address == ("::1", 0):
            frame = sys._getframe(1)
            if (self.probes == 0 and frame.f_code == self.probe_code
                    and frame.f_globals.get("__name__") == "urllib3.util.connection"
                    and Path(frame.f_globals["__file__"]).resolve() == self.probe_path
                    and args[0].family == socket.AF_INET6 and frame.f_locals.get("host") == "::1"):
                self.probes += 1
                self.probe_socket = args[0]
                raise PermissionError("Blocked attested optional IPv6 capability probe")
        if event == "socket.bind" and self.reserving and address == ("127.0.0.1", 0):
            self.reserving = False
            return
        if event == "socket.connect" and isinstance(address, tuple) and self.port is not None:
            if address[:2] == ("127.0.0.1", self.port):
                return
        self.blocked += 1
        # Never retain host names, paths, arguments or exception prose.
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "blocked": True}) + "\n")
        raise PermissionError("Offline trial network boundary")

    def verify(self):
        if self.probes:
            assert self.probe_socket.fileno() == -1
            assert sys.modules["urllib3.util.connection"].HAS_IPV6 is False
        assert self.blocked == 0
        return {"blockedOptionalIpv6Probes": self.probes, "unknownAttempts": self.blocked}


@contextmanager
def control_socketpairs(guard):
    """Narrow interpreter self-pipe allowance; no DNS or arbitrary loopback."""
    original = socket.socketpair
    def pair(*args, **kwargs):
        assert not args and not kwargs  # asyncio uses the ordinary IPv4 default
        guard.pair_depth += 1
        left = right = None
        try:
            left, right = original()
            assert left.getsockname() == right.getpeername()
            assert right.getsockname() == left.getpeername()
            assert left.getsockname()[0] == right.getsockname()[0] == "127.0.0.1"
            return left, right
        except BaseException:
            for stream in (left, right):
                if stream is not None:
                    stream.close()
            raise
        finally:
            guard.pair_depth -= 1
    socket.socketpair = pair
    try:
        yield
    finally:
        socket.socketpair = original


@contextmanager
def native_tls_cases(guard, target, source_hash):
    """Transport-only observation for the two unchanged disclosure falsifiers.

    The exact copied test code owns the case; its nested catcher/redirect
    functions own the two TestServers. Admit each actual socket and its retained
    endpoint, not a global loopback window. Requests, TLS pinning, redirects,
    proxies and the test's bearer-disclosure assertions remain unchanged.
    """
    import pytest
    from aiohttp.test_utils import BaseTestServer, TestServer
    source = target / "tests/test_browser_entry.py"
    raw = source.read_bytes()
    assert digest(raw) == source_hash
    name = "test_native_unrelated_listener_and_proxy_never_receive_bearer"
    declaration = next(part for part in ast.parse(raw).body
                       if isinstance(part, ast.AsyncFunctionDef) and part.name == name)
    assert {part.name for part in declaration.body if isinstance(part, ast.AsyncFunctionDef)} == {"catcher", "redirect"}
    scopes = {}
    def scope_for(item):
        assert item.nodeid in TLS_NEGATIVE_NODES
        function = item.obj
        # pytest rewrites assertions, so raw compile(source) is not the loaded
        # code object. Bind to this exact selected module/function after rewrite,
        # with the independently staged source hash and exact parameter checked.
        assert (function.__name__ == name and function.__code__.co_name == name
                and Path(function.__code__.co_filename) == source
                and Path(function.__globals__["__file__"]) == source
                and sys.modules[function.__module__].__dict__ is function.__globals__
                and function.__globals__[name] is function and digest(source.read_bytes()) == source_hash
                and item.callspec.params["mode"] == TLS_NEGATIVE_MODES[item.nodeid])
        if item.nodeid not in scopes:
            handlers = {part for part in function.__code__.co_consts if isinstance(part, types.CodeType)
                        and part.co_name in {"catcher", "redirect"}}
            assert len(handlers) == 2
            scopes[item.nodeid] = types.SimpleNamespace(
                globals=function.__globals__, handlers=handlers, port=None, endpoints=[])
        return scopes[item.nodeid]

    def bound(stream, address):
        assert guard.tls_binding is None
        guard.tls_binding = (stream, address)
        try:
            stream.bind(address)
        finally:
            guard.tls_binding = None

    class Controls:
        def reserve(self, item):
            # Only these two selected cases request this fixture. Retain no
            # ambient unused-port allowance and never bind a nonnumeric host.
            scope = scope_for(item)
            assert scope.port is None
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
                bound(stream, ("127.0.0.1", 0))
                scope.port = stream.getsockname()[1]
            guard.tls_reservations += 1
            return scope.port

        @pytest.fixture
        def unused_tcp_port(self, request):
            return self.reserve(request.node)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            if item.nodeid not in TLS_NEGATIVE_NODES:
                yield
                return
            scope = scope_for(item)
            assert scope.port is not None and not scope.endpoints
            token = guard.tls_scope.set(scope)
            try:
                yield
            finally:
                guard.tls_scope.reset(token)

    original = BaseTestServer.start_server
    async def start(server, *args, **kwargs):
        scope = guard.tls_scope.get()
        if scope is None:
            return await original(server, *args, **kwargs)  # bind stays denied
        if server.started:
            assert any(endpoint.server is server for endpoint in scope.endpoints)
            return await original(server, *args, **kwargs)  # aiohttp_client cleanup registration
        assert type(server) is TestServer and server.host == "127.0.0.1" and not server.started
        assert len(scope.endpoints) < 2
        # The first server is the ephemeral HTTP sink; the second is the
        # explicitly reserved wrong-leaf or trusted-redirect HTTPS listener.
        expected_port = 0 if not scope.endpoints else scope.port
        assert (server.port or 0) == expected_port
        assert bool(kwargs.get("ssl")) == bool(scope.endpoints)
        routes = tuple(server.app.router.routes())
        assert routes and all(route.handler.__code__ in scope.handlers
            and route.handler.__globals__ is scope.globals for route in routes)
        original_factory = server.socket_factory
        streams = []
        def factory(host, port, family):
            assert not streams and host == "127.0.0.1" and port == expected_port and family == socket.AF_INET
            stream = socket.socket(family, socket.SOCK_STREAM)
            streams.append(stream)
            try:
                bound(stream, (host, port))
                return stream
            except BaseException:
                stream.close()
                raise
        server.socket_factory = factory
        try:
            await original(server, *args, **kwargs)
            assert len(streams) == 1 and streams[0].fileno() >= 0
            assert streams[0].getsockname()[:2] == (server.host, server.port)
            endpoint = types.SimpleNamespace(server=server, stream=streams[0])
            scope.endpoints.append(endpoint)
            guard.tls_endpoints.append(endpoint)
        except BaseException:
            await server.close()
            for stream in streams:
                stream.close()
            raise
        finally:
            server.socket_factory = original_factory
    BaseTestServer.start_server = start
    try:
        yield Controls()
    finally:
        BaseTestServer.start_server = original


def sanitize(root):
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    environment.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "home" / "roaming"), "LOCALAPPDATA": str(root / "home" / "local"),
        "AMPLIFIER_HOME": str(root / "shared"), "AMPLIFIER_WEB_HOME": str(root / "app"),
        "AMPLIFIER_UNIFIED_IMPORT_HOME": str(root / "legacy"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
        "AMPLIFIER_TERMINAL_HOME": str(root / "terminal"),
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "capture"),
        "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1", "PYTHONUTF8": "1",
        "WINDOWS_TRIAL_ROOT": str(root),
        "GITHUB_ACTIONS": "true",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    os.environ.clear()
    os.environ.update(environment)


def private_directory(path, root):
    from amplifier_web.windows_files import directory
    with directory(path, owned_root=root, create=True):
        pass


def canonical_temp(value):
    """Canonicalize ONLY the existing TEMP, checking both chains, never ACL repair."""
    original = Path(value).absolute()
    canonical = original.resolve(strict=True)
    for path in (original, canonical):
        for component in (*reversed(path.parents), path):
            info = component.lstat()
            assert stat.S_ISDIR(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)
    before, after = original.stat(), canonical.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    return canonical


def settled(owners):
    """Never equate swallowed RuntimeManager.close errors to cleanup proof."""
    return all(owner.result is not None and owner.result.active_processes == 0
               and owner.job.tree_empty and owner.job.io_finalized for owner in owners)


async def until(predicate):
    async with asyncio.timeout(60):
        while not predicate():
            await asyncio.sleep(.05)


def child_handle(pid):
    # Retain a read-only exact process handle, never signal an integer PID.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
    assert handle and kernel.WaitForSingleObject(handle, 0) == 258
    return kernel, handle


async def worker(wheel):
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    guard = NetworkGuard(root / ("network-worker-" + str(os.getpid()) + ".jsonl"))
    sys.addaudithook(guard.audit)
    from amplifier_web.runtime_worker import Worker, configure_protocol
    configure_protocol()
    # Record again AFTER the real mount, not only before Foundation source loading.
    owner = Worker()
    original_start = owner.start
    async def observed_start(*args, **kwargs):
        await original_start(*args, **kwargs)
        if owner.session is not None:
            evidence = installed(wheel)
            guard.verify()
            write_json(root / ("installed-worker-" + str(os.getpid()) + ".json"), evidence)
    owner.start = observed_start
    await owner.run()
    guard.verify()


async def journey(root, wheel, report, guard, phase):
    from aiohttp import web, ClientSession, TCPConnector, DummyCookieJar
    from amplifier_web import auth, browser_entry as entry, server, tls
    from amplifier_web.deployment import load_server_config
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.session_files import sessions_dir
    from amplifier_foundation.session.history import SessionHistoryStore

    home, workspace = root / "app", root / "workspace café 日本語"
    report["stage"] = "fixture"
    modules = {key: str(Path(importlib.import_module(value).__file__).parent)
               for key, value in MODULES.items()}
    settings = {"bundle": {"app": []}, "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
                "sources": {"modules": modules}}
    from amplifier_web.deployment import write_private
    write_private(root / "shared" / "settings.yaml", json.dumps(settings), owned_root=root / "shared")
    provider = root / "provider" / "amplifier_module_provider_fixture"
    if phase == "first":
        private_directory(provider, root)
        (provider / "__init__.py").write_text(PROVIDER, encoding="utf-8")
    output, blocked = workspace / "trial.txt", root / "blocked.json"
    bundle = root / "bundle.yaml"
    write_json(bundle, {
        "bundle": {"name": "windows-trial"},
        "session": {"orchestrator": {"module": "loop-live", "config": {
            "use_streaming": False, "min_delay_between_calls_ms": 0}},
            "context": {"module": "context-simple", "source": modules["context-simple"]}},
        "providers": [{"module": "provider-fixture", "source": str(provider.parent),
                       "config": {"output": str(output), "blocked": str(blocked),
                                  "calls": str(root / "provider-calls.jsonl")}}],
        "tools": [{"module": "tool-filesystem", "source": modules["tool-filesystem"],
                   "config": {"allowed_write_paths": [str(workspace)]}}],
    })
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker", str(wheel)]
    runtime = RuntimeManager(command=command, startup_timeout=90, retention={"prewarm_on_select": False})
    runtime.home = home
    runner = app = reservation = None
    original_reader = entry.read_owner_control_token
    owners = []
    try:
        if phase == "first":
            report["stage"] = "private_runtime_project_api"
            from amplifier_web.runtime_environment import prepare_project
            from amplifier_web.windows_files import directory
            content = b'[project]\nname="trial-preparation"\nversion="0.0.0"\n'
            project = prepare_project(home, content=content)
            with directory(project, owned_root=home) as pinned:
                assert pinned.read_private("pyproject.toml", limit=1024) == content
            assert prepare_project(home, content=content) == project
            report["checks"]["privateRuntimeProjectApi"] = "PASS"
        report["stage"] = "credentials_tls"
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        guard.reserving = True
        reservation.bind(("127.0.0.1", 0))
        guard.port = reservation.getsockname()[1]
        reservation.listen(32)
        reservation.setblocking(False)
        config = load_server_config(home, overrides={"port": guard.port, "bind": ["127.0.0.1"]})
        config = tls.setup_local_ca(home, config)
        app = await server.create_app(home, workspace=workspace, runtime=runtime, voice=False,
            background_updates=False, background_work=False, preload_providers=False, server_config=config)
        runtime.app_bridge = app["service"].app_bridge
        assert app["browser_entry"] is None
        try:
            original_reader(home, entry.current_owner_sid())
        except entry.BrowserEntryUnavailable:
            report["publicGateClosed"] = True
        else:
            raise AssertionError("Public gate changed; re-review qualification boundary")
        # Same explicit candidate seam as test_native_actual_app_tls_grant_cookie_and_replay.
        # This in-process fixture changes the reader selection, not the source
        # gate, stored settings or credential initialization implementation.
        entry.read_owner_control_token = entry._read_private_owner_control_token
        entry.initialize(app)
        assert app["browser_entry"] is not None
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.SockSite(runner, reservation, ssl_context=tls.ssl_context(home, config)).start()
        _, pin = entry._owner_probe_targets(home, config)
        origin = f"https://127.0.0.1:{guard.port}"
        async with ClientSession(connector=TCPConnector(ssl=pin), trust_env=False,
                                 cookie_jar=DummyCookieJar()) as client:
            report["stage"] = "grant_cookie_csrf"
            response = await client.get(origin + "/api/state")
            assert response.status == 401
            await response.read()
            url = await entry.owner_browser_url(home, config)
            assert "#ticket=" in url and app["control_token"] not in url
            ticket = url.split("#ticket=")[1]
            response = await client.get(origin + "/login")
            assert response.status == 200
            csrf = response.cookies[auth.CSRF_COOKIE].value
            await response.read()
            headers = {"Origin": origin, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers,
                data={"csrf": "wrong", "ticket": ticket}, allow_redirects=False)
            assert response.status == 403
            await response.read()
            data = {"csrf": csrf, "ticket": ticket}
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers, data=data, allow_redirects=False)
            assert response.status == 303
            cookie = response.cookies[auth.SESSION_COOKIE]
            assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
            await response.read()
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers, data=data, allow_redirects=False)
            assert response.status == 403
            await response.read()
            headers = {"Origin": origin, "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}
            response = await client.get(origin + "/api/state", headers=headers)
            assert response.status == 200
            await response.read()
            report["checks"]["privateTlsGrantCookieCsrfReplay"] = "PASS"

            async def action(name, args, identity):
                response = await client.post(origin + "/api/actions", headers=headers,
                    json={"action": name, "args": args, "id": identity})
                result = await response.json()
                assert response.status == 200 and not result.get("error")
                return result

            service = app["service"]
            if phase == "first":
                report["stage"] = "first_worker_turn"
                await action("session.create", {"bundle": str(bundle)}, "trial-create")
                sid = service._session()["id"]
                current = (await action("access.get", {"sessionId": sid}, "access-read"))["result"]
                await action("access.set", {"sessionId": sid, "scope": "workspace", "mode": "full",
                    "expectedRevision": current["revision"]}, "access-save")
            else:
                report["stage"] = "host_restart_resume"
                saved = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
                sid = saved["sid"]
                assert service._session(sid)["id"] == sid
                assert auth.control_token(home) == saved["control"]
                assert auth.session_secret(home) == saved["secret"]
                output_read = (await action("outputs.read", {"sessionId": sid, "id": saved["output"]},
                                           "read-after-host-restart"))["result"]
                assert output_read["sha256"] == digest((MARKER + "\n").encode())
                assert output_read["text"] == MARKER + "\n"
            inherited = (await action("access.get", {"sessionId": sid}, "access-inherited-" + phase))["result"]
            assert inherited["mode"] == "full" and inherited["source"] == "workspace"
            assert inherited["overrides"]["session"]["mode"] == "inherit"
            report["checks"]["inheritedAccessLookup"] = "PASS"
            await action("conversation.send", {"sessionId": sid,
                "text": "FIRST_TRIAL café 日本語" if phase == "first" else "RESUME_TRIAL"}, "trial-" + phase)
            directory = sessions_dir(workspace) / sid
            expected = MARKER if phase == "first" else RESUMED
            def completed():
                if not runtime.workers.get(sid, {}).get("parked"):
                    return False
                return any(m["role"] == "assistant" and expected in str(m.get("content"))
                           for m in SessionHistoryStore(directory).load_messages())
            await until(completed)
            row = runtime.workers[sid]
            owners.append(row["windows_owner"])
            calls = [json.loads(line) for line in (root / "provider-calls.jsonl").read_text(encoding="utf-8").splitlines()]
            assert calls[-1] == {"pid": row["process"].pid, "access": "full"}
            assert row["process"].pid != row["windows_owner"].job.identity.guard_pid
            assert output.read_text(encoding="utf-8") == MARKER + "\n"
            history = SessionHistoryStore(directory).load_messages()
            if phase == "resume":
                assert any(m["role"] == "assistant" and RESUMED in str(m.get("content")) for m in history)
                assert sum(m["role"] == "user" and "FIRST_TRIAL" in str(m.get("content")) for m in history) == 1
                report["checks"]["hostRestartResumeWithoutReplay"] = "PASS"
                return
            assert any(m["role"] == "assistant" and MARKER in str(m.get("content")) for m in history)
            assert any(m["role"] == "tool" for m in history)
            report["checks"]["realWorkerToolUtf8History"] = "PASS"
            report["stage"] = "output_snapshot"
            record = (await action("outputs.attach", {"sessionId": sid, "kind": "file", "title": "Trial",
                "path": "trial.txt", "expectedSha256": digest(output.read_bytes())}, "trial-output"))["result"]
            read = (await action("outputs.read", {"sessionId": sid, "id": record["id"]}, "trial-output-read"))["result"]
            assert read["text"] == MARKER + "\n" and read["sha256"] == digest(output.read_bytes())
            response = await client.get(origin + read["downloadUrl"], headers=headers)
            assert response.status == 200 and await response.read() == output.read_bytes()
            report["checks"]["outputSnapshotReadExactBytes"] = "PASS"
            write_private(root / "checkpoint.json", json.dumps({"sid": sid, "output": record["id"],
                "control": auth.control_token(home), "secret": auth.session_secret(home)}), owned_root=root)
            report["stage"] = "stop_tree"
            await action("conversation.send", {"sessionId": sid, "text": "WAIT_FOR_STOP"}, "trial-wait")
            await until(blocked.exists)
            kernel, handle = child_handle(json.loads(blocked.read_text())["pid"])
            try:
                await runtime.stop(sid)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel.CloseHandle(handle)
            assert owners[-1].job.tree_empty and owners[-1].job.io_finalized
            assert owners[-1].result.active_processes == 0
            report["checks"]["stopDescendantTreeAndIo"] = "PASS"
    finally:
        entry.read_owner_control_token = original_reader
        # Include a worker that failed before its first parked observation.
        for row in runtime.workers.values():
            if row.get("windows_owner") is not None and row["windows_owner"] not in owners:
                owners.append(row["windows_owner"])
        if runner is not None:
            await runner.cleanup()
        elif app is not None:
            await app["service"].close()
        else:
            await runtime.close()
        if reservation is not None:
            reservation.close()
        # RuntimeManager.close gathers exceptions; do not mistake that for proof.
        report["innerJobsSettled"] = settled(owners)
        assert report["innerJobsSettled"]


def require_ci():
    # An intentional CI-only listener must never be created by local authoring.
    assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert sys.version_info[:2] == (3, 13) and sys.implementation.name == "cpython"
    assert struct.calcsize("P") == 8 and not sysconfig.get_config_var("Py_GIL_DISABLED")


def failure(report, exc):
    # No exception messages, traceback symbols, paths, SIDs or credentials.
    report["status"] = "FAIL"
    report["checks"].setdefault(report["stage"], "FAIL")
    kinds = {AssertionError: "assertion", PermissionError: "permission",
             FileNotFoundError: "missing", TimeoutError: "timeout",
             ImportError: "import", ModuleNotFoundError: "import"}
    report["failureKind"] = kinds.get(type(exc), "other")
    for key in ("errno", "winerror"):
        value = getattr(exc, key, None)
        if type(value) is int:
            report[key] = value


class NativeResults:
    """Retain only fixed labels/counts, never pytest error prose or locations."""
    def __init__(self):
        self.node_labels = {"tests/" + node: label for label, node in NATIVE_CONTROLS.items()}
        self.collected = {label: 0 for label in NATIVE_CONTROLS}
        self.phases = {label: [] for label in NATIVE_CONTROLS}
        self.unexpected = self.collection_errors = 0

    def pytest_collection_finish(self, session):
        for item in session.items:
            label = self.node_labels.get(item.nodeid)
            if label is None:
                self.unexpected += 1
            else:
                self.collected[label] += 1

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.collection_errors += 1

    def pytest_runtest_logreport(self, report):
        label = self.node_labels.get(report.nodeid)
        if (label is None or report.when not in {"setup", "call", "teardown"}
                or report.outcome not in {"passed", "failed", "skipped"}
                or hasattr(report, "wasxfail")):
            self.unexpected += 1
            return
        self.phases[label].append((report.when, report.outcome))

    def finish(self, exit_code):
        cases = {}
        for label, phases in self.phases.items():
            cases[label] = {
                "status": "PASS" if self.collected[label] == 1 and phases == [
                    ("setup", "passed"), ("call", "passed"), ("teardown", "passed")] else "FAIL",
                "collected": self.collected[label],
                "callsPassed": phases.count(("call", "passed")),
                "skips": sum(outcome == "skipped" for _, outcome in phases),
                "failures": sum(outcome == "failed" for _, outcome in phases),
            }
        exact = not self.unexpected and not self.collection_errors and all(
            count == 1 for count in self.collected.values())
        return {"status": "PASS" if int(exit_code) == 0 and exact and all(
                    row["status"] == "PASS" for row in cases.values()) else "FAIL",
                "exitCode": int(exit_code), "collectionExact": exact, "cases": cases}


def stage_controls(root, source):
    """Copy only reviewed tests/helpers: never put checkout production on sys.path.

    The runtime native test inserts its copied parent into a child interpreter.
    That directory contains tests ONLY; the actual app still comes from the wheel.
    """
    target = root / "native-controls"
    private_directory(target / "tests", root)
    hashes = {}
    for name in NATIVE_FILES:
        raw = (source / name).read_bytes()
        (target / "tests" / name).write_bytes(raw)
        assert (target / "tests" / name).read_bytes() == raw
        hashes[name] = digest(raw)
    assert set(path.name for path in target.iterdir()) == {"tests"}
    return target, hashes


def native_controls(wheel):
    """Synchronous pytest entrypoint in its own contained interpreter."""
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    report = {"status": "FAIL", "stage": "native_prerequisites", "checks": {}}
    guard = None
    try:
        with (root / "diagnostics-controls.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                guard = NetworkGuard(root / "network-controls.jsonl")
                sys.addaudithook(guard.audit)
                report["installed"] = installed(wheel)
                target, report["testSources"] = stage_controls(root, Path(__file__).parents[1])
                import pytest
                results = NativeResults()
                with control_socketpairs(guard), native_tls_cases(
                        guard, target, report["testSources"]["test_browser_entry.py"]) as tls_controls:
                    result = pytest.main([
                        "-q", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
                        "-p", "aiohttp.pytest_plugin", "-o", "asyncio_mode=auto",
                        "--rootdir", str(target), "--confcutdir", str(target),
                        "--basetemp", str(root / "native-test-tmp"),
                        *[str(target / "tests" / node) for node in NATIVE_CONTROLS.values()]],
                        plugins=[results, tls_controls])
                report["selection"] = results.finish(result)
                assert installed(wheel) == report["installed"]
                guard.verify()
                assert report["selection"]["status"] == "PASS"
                assert guard.tls_reservations == 2 and len(guard.tls_endpoints) == 4
                assert all(endpoint.server.closed and endpoint.stream.fileno() == -1
                           for endpoint in guard.tls_endpoints)
                report["checks"]["maintainedNativePrerequisites"] = "PASS"
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
    finally:
        if guard is not None:
            report["network"] = {
                "blockedOptionalIpv6Probes": guard.probes, "unknownAttempts": guard.blocked,
                "infrastructureSocketpairBinds": guard.pair_binds,
                "tlsNegativeReservations": guard.tls_reservations,
                "tlsNegativeListeners": len(guard.tls_endpoints),
                "tlsNegativeConnects": guard.tls_connects}
    write_json(root / "controls.json", report)
    return 0 if report["status"] == "PASS" else 1


def host(wheel, phase):
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    report = {"status": "FAIL", "stage": "installed_host", "checks": {}}
    guard = None
    try:
        # Start the loop before auditing; Windows Proactor loop wakeup socket
        # setup is interpreter infrastructure, not the application under test.
        async def qualified():
            nonlocal guard
            guard = NetworkGuard(root / ("network-host-" + phase + ".jsonl"))
            sys.addaudithook(guard.audit)
            report["installed"] = installed(wheel)
            await journey(root, wheel, report, guard, phase)
            report["network"] = guard.verify()
        with (root / ("diagnostics-" + phase + ".log")).open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                asyncio.run(qualified())
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
    finally:
        if guard is not None:
            report["network"] = {"blockedOptionalIpv6Probes": guard.probes, "unknownAttempts": guard.blocked}
    write_json(root / ("host-" + phase + ".json"), report)
    return 0 if report["status"] == "PASS" else 1


async def hosts(root, wheel, report):
    """Two fresh hosts use one DB, each inside the existing owned-job primitive."""
    from amplifier_web.windows_jobs import WindowsOwnedProcess
    for phase in ("controls", "first", "resume"):
        report["stage"] = phase
        # Unknown launch/cleanup means preserve scratch, not a blind rmtree.
        report["outerJobsSettled"] = False
        command = ([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--controls", str(wheel)] if phase == "controls" else
                   [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--host", str(wheel), "--phase", phase])
        job = await WindowsOwnedProcess.start(command,
            cwd=str(root), env=dict(os.environ), trusted_python=sys._base_executable)
        async def drain(stream):
            while await stream.read(65536):
                pass  # No raw child diagnostic output reaches GitHub logs.
        readers = [asyncio.create_task(drain(stream)) for stream in (job.stdout, job.stderr)]
        try:
            result = await asyncio.wait_for(job.wait(), 150)
        finally:
            # close is idempotent and keeps the native lifeline on uncertainty.
            result = await job.close()
            await asyncio.gather(*readers)
            report["outerJobsSettled"] = (
                result.active_processes == 0 and job.tree_empty and job.io_finalized)
        observed = root / ("controls.json" if phase == "controls" else "host-" + phase + ".json")
        observation = json.loads(observed.read_text(encoding="utf-8"))
        if phase == "controls":
            report["nativeControls"] = observation
            assert report["outerJobsSettled"] and result.payload_returncode == 0
            assert observation["status"] == "PASS" and observation["selection"]["status"] == "PASS"
            continue
        report.setdefault("hosts", []).append(observation)
        assert report["outerJobsSettled"] and result.payload_returncode == 0
        assert observation["status"] == "PASS" and observation["innerJobsSettled"]
    observations = list(root.glob("installed-worker-*.json"))
    assert len(observations) == 2
    report["workers"] = [json.loads(path.read_text(encoding="utf-8")) for path in observations]
    reference = report["hosts"][0]["installed"]
    assert report["nativeControls"]["installed"] == reference
    assert report["hosts"][1]["installed"] == reference
    assert all(row == reference for row in report["workers"])
    calls = [json.loads(line) for line in (root / "provider-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({row["pid"] for row in calls}) == 2
    report["checks"]["twoFreshHostsAndInstalledWorkerGraphs"] = "PASS"
    report["checks"]["unexpectedPythonNetworkAttempts"] = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in root.glob("network-*.jsonl"))
    assert report["checks"]["unexpectedPythonNetworkAttempts"] == 0


def run(wheel, receipt):
    require_ci()
    root = None
    created = False
    retained = json.loads(receipt.read_text(encoding="utf-8"))["candidate"]
    assert set(retained) == {"head", "tree"} and all(
        re.fullmatch(r"[0-9a-f]{40,64}", value) for value in retained.values())
    report = dict(candidate=retained, status="FAIL", stage="private_fixture_creation", checks={},
                  qualification="private-adapter-candidate-only", publicAcceptance=False,
                  runtimePreparation="separate-private-project-api-plus-preinstalled-worker",
                  outerJobsSettled=True,
                  python=list(sys.version_info[:3]), windowsBuild=sys.getwindowsversion().build)
    try:
        root = canonical_temp(os.environ["TEMP"]) / ("ut-" + uuid.uuid4().hex[:12])
        assert not root.exists()
        private_directory(root, root)
        created = True
        sanitize(root)
        for folder in ("home", "home/roaming", "home/local", "app", "workspace café 日本語", "shared"):
            private_directory(root / folder, root)
        asyncio.run(hosts(root, wheel, report))
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
    finally:
        # A caught attempt is still a failed policy check. Publish counts only.
        attempts = sum(len(path.read_text(encoding="utf-8").splitlines())
                       for path in root.glob("network-*.jsonl")) if created else 0
        report["checks"]["unexpectedPythonNetworkAttempts"] = attempts
        if attempts:
            report["status"] = "FAIL"
        if created and root.exists() and report["outerJobsSettled"]:
            try:
                shutil.rmtree(root)
                report["scratchRemoved"] = True
            except OSError:
                report["scratchRemoved"] = False
                report["status"] = "FAIL"
        elif root is not None and root.exists():
            report["scratchRemoved"] = False
            report["status"] = "FAIL"
        try:
            validate_public(report)
        except (AssertionError, TypeError, KeyError, ValueError):
            # Never upload a malformed or free-form child record.
            report = {"candidate": retained, "status": "FAIL", "publicAcceptance": False}
        write_json(receipt, report)
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--host", type=Path)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--phase", choices=("first", "resume"))
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.candidate:
        write_json(args.receipt, {"candidate": candidate(args.candidate), "status": "NOT_RUN",
                                 "publicAcceptance": False})
    elif args.requirements:
        print("\n".join(requirements(args.requirements)))
    elif args.worker:
        asyncio.run(worker(args.worker.resolve()))
    elif args.run:
        return run(args.run.resolve(), args.receipt.resolve())
    elif args.host:
        assert args.phase is not None
        return host(args.host.resolve(), args.phase)
    elif args.controls:
        return native_controls(args.controls.resolve())
    else:
        parser.error("Choose candidate, requirements, worker or run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())