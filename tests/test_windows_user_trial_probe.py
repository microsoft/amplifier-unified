"""Portable models of the CI fixture only: never launch the Windows journey."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import types

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("windows_trial_fixture",
    ROOT / "tests/fixtures/windows_user_trial_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_fixture_and_embedded_provider_compile_and_require_maintained_modules():
    compile(ast.parse(Path(SPEC.origin).read_text(encoding="utf-8")), SPEC.origin, "exec")
    compile(probe.PROVIDER, "<scripted-provider>", "exec")
    requirements = probe.requirements(ROOT / "pyproject.toml")
    assert len(requirements) == 4
    assert all("git+https://github.com/microsoft/" in value for value in requirements)
    assert any(value.startswith("amplifier-module-context-simple @") for value in requirements)


def test_missing_module_cannot_silently_qualify(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[dependency-groups]\ndev=[]\n')
    with pytest.raises(AssertionError):
        probe.requirements(path)


def test_ci_gate_refuses_before_private_io_or_listener(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(AssertionError):
        probe.require_ci()


def test_sanitized_environment_has_no_inherited_credentials_or_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_SECRET", "do-not-inherit")
    monkeypatch.setenv("PYTHONPATH", "/unrelated")
    monkeypatch.setenv("HTTP_PROXY", "http://unrelated")
    before = dict(probe.os.environ)
    try:
        probe.sanitize(tmp_path)
        assert "SYNTHETIC_SECRET" not in probe.os.environ
        assert "PYTHONPATH" not in probe.os.environ and "HTTP_PROXY" not in probe.os.environ
        assert probe.os.environ["UV_OFFLINE"] == "1"
    finally:
        probe.os.environ.clear()
        probe.os.environ.update(before)


@pytest.mark.parametrize("event,args", [
    ("socket.bind", (None, ("127.0.0.1", 12345))),
    ("socket.connect", (None, ("127.0.0.1", 12345))),
    ("socket.connect", (None, ("192.0.2.1", 443))),
    ("socket.getaddrinfo", (b"private.invalid", 443)),
    ("socket.gethostbyname", ("private.invalid",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)),
    ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
    ("socket.sendto", (None, b"private", ("192.0.2.1", 443))),
])
def test_unknown_network_attempts_block_and_retain_no_arguments(tmp_path, event, args):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    with pytest.raises(PermissionError):
        guard.audit(event, args)
    assert guard.blocked == 1
    assert json.loads(guard.log.read_text()) == {"event": event, "blocked": True}
    with pytest.raises(AssertionError):
        guard.verify()


def test_only_one_owned_reservation_and_exact_port_connect_are_permitted(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.reserving = True
    guard.audit("socket.bind", (None, ("127.0.0.1", 0)))
    with pytest.raises(PermissionError):
        guard.audit("socket.bind", (None, ("127.0.0.1", 0)))
    guard.port = 12345
    guard.audit("socket.connect", (None, ("127.0.0.1", 12345)))


@pytest.mark.parametrize("fault", [None, "wrong-code", "wrong-module", "repeat", "unclosed", "enabled"])
def test_attested_ipv6_probe_model_is_blocked_not_allowed(tmp_path, monkeypatch, fault):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    sock = types.SimpleNamespace(family=socket.AF_INET6, fileno=lambda: -1)
    frame = types.SimpleNamespace(f_code=guard.probe_code,
        f_globals={"__name__": "urllib3.util.connection", "__file__": str(guard.probe_path)},
        f_locals={"host": "::1"})
    if fault == "wrong-code":
        frame.f_code = (lambda: None).__code__
    if fault == "wrong-module":
        frame.f_globals["__name__"] = "unrelated"
    if fault == "repeat":
        guard.probes = 1
    monkeypatch.setattr(probe.sys, "_getframe", lambda _: frame)
    with pytest.raises(PermissionError):
        guard.audit("socket.bind", (sock, ("::1", 0)))
    if fault in {"wrong-code", "wrong-module", "repeat"}:
        assert guard.blocked == 1
        return
    monkeypatch.setitem(probe.sys.modules, "urllib3.util.connection",
                        types.SimpleNamespace(HAS_IPV6=fault == "enabled"))
    if fault == "unclosed":
        sock.fileno = lambda: 42
    if fault:
        with pytest.raises(AssertionError):
            guard.verify()
    else:
        assert guard.verify() == {"blockedOptionalIpv6Probes": 1, "unknownAttempts": 0}
    assert not guard.log.exists()  # accepted BLOCKED probe, no real bind in this model


def test_exact_probe_body_closes_socket_after_blocked_capability_attempt():
    class Socket:
        closed = False
        def bind(self, address):
            assert address == ("::1", 0)
            raise PermissionError("blocked before any OS bind")
        def close(self):
            self.closed = True
    instance = Socket()
    namespace = {"socket": types.SimpleNamespace(has_ipv6=True, AF_INET6=10,
                                                socket=lambda family: instance)}
    exec(compile(probe.IPV6_PROBE, "<pure-probe-body-model>", "exec"), namespace)
    assert namespace["_has_ipv6"]("::1") is False
    assert instance.closed


def test_changed_capability_probe_source_fails_before_import(tmp_path, monkeypatch):
    path = tmp_path / "changed.py"
    path.write_text(probe.IPV6_PROBE.replace("sock.close()", "pass"))
    monkeypatch.setattr(probe.metadata, "distribution",
                        lambda _: types.SimpleNamespace(locate_file=lambda _: path))
    with pytest.raises(AssertionError):
        probe.NetworkGuard(tmp_path / "network.jsonl")


@pytest.mark.parametrize("fault", [None, "no-scope", "wrong-code", "wrong-module",
                                  "wrong-file", "wrong-socket", "wrong-address"])
@pytest.mark.parametrize("event", ["socket.bind", "socket.connect"])
def test_native_test_loop_socketpair_allowance_is_exact_and_scoped(tmp_path, monkeypatch, fault, event):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.pair_depth = 0 if fault == "no-scope" else 1
    server = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 12345))
    client = object()
    frame = types.SimpleNamespace(f_code=socket._fallback_socketpair.__code__,
        f_globals={"__name__": "socket", "__file__": socket.__file__},
        f_locals={"lsock": server, "csock": client}, f_back=None)
    if fault == "wrong-code":
        frame.f_code = (lambda: None).__code__
    elif fault == "wrong-module":
        frame.f_globals["__name__"] = "unrelated"
    elif fault == "wrong-file":
        frame.f_globals["__file__"] = str(tmp_path / "not-socket.py")
    stream = server if event == "socket.bind" else client
    address = ("127.0.0.1", 0 if event == "socket.bind" else 12345)
    if fault == "wrong-socket":
        stream = object()
    elif fault == "wrong-address":
        address = ("192.0.2.1", address[1])
    monkeypatch.setattr(probe.sys, "_getframe", lambda _: frame)
    if fault:
        with pytest.raises(PermissionError):
            guard.audit(event, (stream, address))
        assert guard.blocked == 1
    else:
        guard.audit(event, (stream, address))
        assert guard.verify()["unknownAttempts"] == 0
        assert guard.pair_binds == (1 if event == "socket.bind" else 0)
        assert not guard.log.exists()


def test_socketpair_scope_never_exempts_resolvers(tmp_path):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    guard.pair_depth = 1
    for event in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
        with pytest.raises(PermissionError):
            guard.audit(event, ("private.invalid",))
    assert guard.blocked == 3
    with pytest.raises(AssertionError):
        guard.verify()


def test_socketpair_wrapper_closes_both_on_peer_mismatch(tmp_path, monkeypatch):
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    closed = []
    left = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 1),
        getpeername=lambda: ("127.0.0.1", 2), close=lambda: closed.append("left"))
    right = types.SimpleNamespace(getsockname=lambda: ("127.0.0.1", 2),
        getpeername=lambda: ("127.0.0.1", 3), close=lambda: closed.append("right"))
    original = lambda: (left, right)
    monkeypatch.setattr(socket, "socketpair", original)
    with probe.control_socketpairs(guard):
        with pytest.raises(AssertionError):
            socket.socketpair()
    assert closed == ["left", "right"] and guard.pair_depth == 0
    assert socket.socketpair is original


def immediate(coroutine):
    """Execute an await-free fake transport only; never create an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    pytest.fail("Fake transport unexpectedly waited")


@pytest.fixture
def tls_transport_model(tmp_path, monkeypatch):
    import aiohttp.test_utils as utilities
    guard = probe.NetworkGuard(tmp_path / "network.jsonl")
    source = ROOT / "tests/test_browser_entry.py"
    code = next(part for part in compile(source.read_bytes(), str(source), "exec",
                                        dont_inherit=True).co_consts
                if isinstance(part, types.CodeType) and
                part.co_name == "test_native_unrelated_listener_and_proxy_never_receive_bearer")
    module = types.ModuleType("trial_browser_transport_model")
    module.__file__ = str(source)
    function = types.FunctionType(code, module.__dict__)
    module.__dict__[code.co_name] = function
    monkeypatch.setitem(probe.sys.modules, module.__name__, module)
    item = types.SimpleNamespace(nodeid="tests/" + probe.NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"],
        obj=function, callspec=types.SimpleNamespace(params={"mode": "wrong-leaf"}))
    handler_code = next(part for part in code.co_consts
                        if isinstance(part, types.CodeType) and part.co_name == "catcher")
    handler = types.FunctionType(handler_code, module.__dict__,
                                 closure=tuple(types.CellType([]) for _ in handler_code.co_freevars))
    sockets = []
    class Socket:
        def __init__(self, family, kind):
            self.family, self.address, self.closed = family, None, False
            sockets.append(self)
        def bind(self, address):
            guard.audit("socket.bind", (self, address))
            self.address = (address[0], address[1] or 25000 + len(sockets))
        def getsockname(self):
            return self.address
        def fileno(self):
            return -1 if self.closed else 77
        def close(self):
            self.closed = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
    class Base:
        async def start_server(self, *args, **kwargs):
            if self.started:
                return
            self.stream = self.socket_factory(self.host, self.port or 0, socket.AF_INET)
            self.port = self.stream.getsockname()[1]
            self.started = True
        async def close(self):
            self.closed = True
            if hasattr(self, "stream"):
                self.stream.close()
    class Server(Base):
        def __init__(self, port=None, route_handler=handler):
            self.port, self.host, self.started, self.closed = port, "127.0.0.1", False, False
            def default_factory(host, port, family):
                stream = Socket(family, socket.SOCK_STREAM)
                try:
                    stream.bind((host, port))
                    return stream
                except BaseException:
                    stream.close()
                    raise
            self.socket_factory = default_factory
            self.app = types.SimpleNamespace(router=types.SimpleNamespace(
                routes=lambda: [types.SimpleNamespace(handler=route_handler)]))
    monkeypatch.setattr(utilities, "BaseTestServer", Base)
    monkeypatch.setattr(utilities, "TestServer", Server)
    monkeypatch.setattr(socket, "socket", Socket)
    return types.SimpleNamespace(guard=guard, target=ROOT, source=source, item=item,
                                 sockets=sockets, Server=Server, Base=Base)


@pytest.mark.parametrize("mode", ["wrong-leaf", "redirect"])
def test_exact_tls_negative_case_owns_only_its_live_sink_and_listener(tls_transport_model, mode):
    model = tls_transport_model
    guard = model.guard
    model.item.nodeid = "tests/" + probe.NATIVE_CONTROLS[
        "auth_wrong_leaf_no_disclosure" if mode == "wrong-leaf" else "auth_redirect_proxy_no_disclosure"]
    model.item.callspec.params["mode"] = mode
    original = model.Base.start_server
    with probe.native_tls_cases(guard, model.target, probe.digest(model.source.read_bytes())) as plugin:
        port = plugin.reserve(model.item)
        assert model.sockets[0].closed and guard.tls_binding is None
        hook = plugin.pytest_runtest_call(model.item)
        next(hook)
        sink, listener = model.Server(), model.Server(port)
        if mode == "redirect":
            redirect_code = next(part for part in model.item.obj.__code__.co_consts
                if isinstance(part, types.CodeType) and part.co_name == "redirect")
            redirect = types.FunctionType(redirect_code, model.item.obj.__globals__,
                closure=tuple(types.CellType(None) for _ in redirect_code.co_freevars))
            listener = model.Server(port, route_handler=redirect)
        immediate(sink.start_server())
        immediate(listener.start_server(ssl=object()))
        immediate(listener.start_server())  # cleanup registration is idempotent
        for endpoint in guard.tls_endpoints:
            guard.audit("socket.connect", (None, endpoint.stream.getsockname()))
        # These targets are reachable in CI: original received/trusted_received
        # test assertions, not a blocked transport, decide bearer disclosure.
        assert guard.tls_connects == 2 and len(guard.tls_endpoints) == 2
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, ("127.0.0.1", port + 100)))
        with pytest.raises(PermissionError):
            guard.audit("socket.gethostbyname", ("private.invalid",))
        immediate(listener.close())
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, ("127.0.0.1", port)))
        with pytest.raises(StopIteration):
            next(hook)
        assert guard.tls_scope.get() is None
        with pytest.raises(PermissionError):
            guard.audit("socket.connect", (None, sink.stream.getsockname()))
        immediate(sink.close())
    assert model.Base.start_server is original
    assert all(stream.closed for stream in model.sockets)


@pytest.mark.parametrize("fault", ["node", "parameter", "source-hash", "function-origin"])
def test_tls_negative_port_reservation_requires_exact_case(tls_transport_model, fault):
    model = tls_transport_model
    if fault == "node":
        model.item.nodeid = "tests/test_browser_entry.py::unselected"
    elif fault == "parameter":
        model.item.callspec.params["mode"] = "unselected"
    elif fault == "function-origin":
        model.item.obj = lambda: None
    fingerprint = "f"*64 if fault == "source-hash" else probe.digest(model.source.read_bytes())
    with pytest.raises(AssertionError):
        with probe.native_tls_cases(model.guard, model.target, fingerprint) as plugin:
            plugin.reserve(model.item)
    assert model.sockets == []


@pytest.mark.parametrize("fault", ["no-case", "wrong-port", "wrong-handler", "wrong-tls"])
def test_tls_negative_server_creation_rejects_unowned_transport(tls_transport_model, fault):
    model = tls_transport_model
    with probe.native_tls_cases(model.guard, model.target,
                               probe.digest(model.source.read_bytes())) as plugin:
        port = plugin.reserve(model.item)
        hook = plugin.pytest_runtest_call(model.item)
        if fault != "no-case":
            next(hook)
        server = model.Server(port if fault == "wrong-port" else None,
                              (lambda: None) if fault == "wrong-handler" else
                              model.Server().app.router.routes()[0].handler)
        with pytest.raises((AssertionError, PermissionError)):
            immediate(server.start_server(**({"ssl": object()} if fault == "wrong-tls" else {})))
        if fault != "no-case":
            with pytest.raises(StopIteration):
                next(hook)
    assert not model.guard.tls_endpoints and model.guard.tls_binding is None


@pytest.mark.parametrize("field", ["result", "tree", "io", "active"])
def test_unknown_or_failed_cleanup_never_permits_scratch_deletion(field):
    owner = types.SimpleNamespace(result=types.SimpleNamespace(active_processes=0),
        job=types.SimpleNamespace(tree_empty=True, io_finalized=True))
    assert probe.settled([owner])
    if field == "result":
        owner.result = None
    elif field == "active":
        owner.result.active_processes = 1
    else:
        setattr(owner.job, "tree_empty" if field == "tree" else "io_finalized", False)
    assert not probe.settled([owner])


def report():
    return {"candidate": {"head": "a"*40, "tree": "b"*40}, "status": "FAIL",
        "stage": "first", "checks": {}, "qualification": "private-adapter-candidate-only",
        "publicAcceptance": False, "runtimePreparation": "separate-private-project-api-plus-preinstalled-worker",
        "outerJobsSettled": False, "scratchRemoved": False, "python": [3, 13, 11], "windowsBuild": 26100}


@pytest.mark.parametrize("field,value", [
    ("path", "/private"), ("failureKind", "PrivateExceptionName"),
    ("stage", "/private"), ("publicAcceptance", True), ("errno", "private"),
    ("candidate", {"head": "private", "tree": "b"*40}),
])
def test_public_record_rejects_private_or_unqualified_fields(field, value):
    value_report = report()
    assert probe.validate_public(value_report) is value_report
    value_report[field] = value
    with pytest.raises((AssertionError, TypeError)):
        probe.validate_public(value_report)


def test_nested_child_record_does_not_bypass_public_allowlist():
    value = report()
    value["hosts"] = [{"status": "FAIL", "stage": "first", "checks": {}, "message": "private"}]
    with pytest.raises(AssertionError):
        probe.validate_public(value)
    value = report()
    value["checks"]["private marker"] = "PASS"
    with pytest.raises(AssertionError):
        probe.validate_public(value)


def test_failure_record_omits_prose_tracebacks_and_symbols():
    value = report()
    probe.failure(value, PermissionError("private path or credential"))
    assert value["failureKind"] == "permission"
    assert "private path" not in json.dumps(value)
    probe.validate_public(value)


def native_result(fault=None):
    """Model pytest reports, never invoke native tests or their OS effects."""
    results = probe.NativeResults()
    nodes = list(results.node_labels)
    selected = nodes[:-1] if fault == "missing" else list(nodes)
    if fault == "unexpected":
        selected.append("private/location.py::private_function")
    if fault == "duplicate":
        selected.append(nodes[0])
    results.pytest_collection_finish(types.SimpleNamespace(
        items=[types.SimpleNamespace(nodeid=node) for node in selected]))
    for node in nodes:
        for phase in ("setup", "call", "teardown"):
            if fault == "no-call" and node == nodes[0] and phase == "call":
                continue
            outcome = "passed"
            if node == nodes[0] and phase == ("teardown" if fault == "teardown" else "call"):
                outcome = "skipped" if fault == "skipped" else (
                    "failed" if fault in {"failed", "teardown"} else "passed")
            row = types.SimpleNamespace(nodeid=node, when=phase, outcome=outcome)
            if fault == "xpass" and node == nodes[0] and phase == "call":
                row.wasxfail = "private reason"
            results.pytest_runtest_logreport(row)
    if fault == "collection-error":
        results.pytest_collectreport(types.SimpleNamespace(failed=True, skipped=False))
    return results.finish(2 if fault == "exit-code" else 0)


@pytest.mark.parametrize("fault", [None, "missing", "unexpected", "duplicate", "no-call",
                                  "skipped", "failed", "teardown", "xpass",
                                  "collection-error", "exit-code"])
def test_native_control_result_requires_every_exact_call_pass_with_no_skips(fault):
    result = native_result(fault)
    assert result["status"] == ("PASS" if fault is None else "FAIL")
    assert "private/location.py" not in json.dumps(result)
    assert "private_function" not in json.dumps(result) and "private reason" not in json.dumps(result)
    if fault is None:
        assert len(result["cases"]) == 18
        assert all(case == {"status": "PASS", "collected": 1, "callsPassed": 1,
                            "skips": 0, "failures": 0} for case in result["cases"].values())


def test_native_selection_exists_in_maintained_sources_and_includes_overlap():
    assert len(probe.NATIVE_CONTROLS) == len(set(probe.NATIVE_CONTROLS.values())) == 18
    assert probe.NATIVE_CONTROLS["resource_publish_overlap"].endswith(
        "::test_resource_publication_of_distinct_blob_during_held_projection")
    assert set(probe.NATIVE_FILES) == {"conftest.py", "test_service.py",
        "test_runtime_updates.py", "test_windows_auth.py", "test_runtime.py",
        "test_optional_controller_availability.py", "test_windows_read_files.py", "test_browser_entry.py"}
    for node in probe.NATIVE_CONTROLS.values():
        filename, function = node.split("::")
        name = function.split("[")[0]
        tree = ast.parse((ROOT / "tests" / filename).read_text(encoding="utf-8"))
        matches = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and item.name == name]
        assert len(matches) == 1
        if "[" in function:
            parameter, values = (("forced", [False, True]) if function.endswith(("[False]", "[True]"))
                                 else ("mode", ["wrong-leaf", "redirect"]))
            assert function.endswith(("[False]", "[True]", "[pyloop-wrong-leaf]", "[pyloop-redirect]"))
            assert any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "parametrize" and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == parameter and ast.literal_eval(dec.args[1]) == values
                for dec in matches[0].decorator_list)


def test_native_control_staging_copies_tests_only_not_checkout_production(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "private_directory",
                        lambda path, root: path.mkdir(parents=True, exist_ok=False))
    target, hashes = probe.stage_controls(tmp_path, ROOT / "tests")
    assert set(hashes) == set(probe.NATIVE_FILES)
    assert set(path.name for path in target.iterdir()) == {"tests"}
    assert not (target / "amplifier_web").exists()
    for filename, expected in hashes.items():
        raw = (target / "tests" / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        assert raw == (ROOT / "tests" / filename).read_bytes()


@pytest.mark.parametrize("regression", [None, "legacy-ids", "legacy-mode"])
def test_actual_native_collection_with_declared_plugins_and_tls_mode_binding(tmp_path, monkeypatch, regression):
    """Collect real rewritten tests, but execute no test/fixture OS effects.

    The local subprocess uses this checkout explicitly: this regression proves
    collection/parameter binding, NOT installed-wheel or native acceptance.
    Only the port reservation used to validate actual collected TLS items is a
    pure fake. The NetworkGuard blocks every real socket/DNS attempt.
    """
    monkeypatch.setattr(probe, "private_directory",
                        lambda path, root: path.mkdir(parents=True, exist_ok=False))
    target, hashes = probe.stage_controls(tmp_path, ROOT / "tests")
    receipt = tmp_path / "collection.json"
    script = r'''
import importlib.util, json, os, pathlib, socket, sys
root, target, receipt = map(pathlib.Path, sys.argv[1:4])
regression = sys.argv[4]
spec = importlib.util.spec_from_file_location("collection_probe",
    root / "tests/fixtures/windows_user_trial_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
if regression == "legacy-ids":
    # Reproduce the original selection defect only in this child, no file edit.
    probe.NATIVE_CONTROLS = {label: node.replace("[pyloop-", "[")
                            for label, node in probe.NATIVE_CONTROLS.items()}
elif regression == "legacy-mode":
    # Reproduce the old combined-suffix comparison on actual collected items.
    probe.TLS_NEGATIVE_MODES = {node: node.rsplit("[", 1)[1][:-1]
                                for node in probe.TLS_NEGATIVE_MODES}
guard = probe.NetworkGuard(receipt.with_suffix(".network.jsonl"))
sys.addaudithook(guard.audit)
# Explicit local clean-source selection, never inherited PYTHONPATH.
sys.path.insert(0, str(root))
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import pytest
observed, validated = [], []
class Collection:
    def pytest_collection_finish(self, session):
        observed.extend(item.nodeid for item in session.items)
        expected = ["tests/" + node for node in probe.NATIVE_CONTROLS.values()]
        assert sorted(observed) == sorted(expected) and len(set(observed)) == 18
        class Reservation:
            def __init__(self, family, kind):
                assert family == socket.AF_INET and kind == socket.SOCK_STREAM
                self.closed = False
            def bind(self, address):
                guard.audit("socket.bind", (self, address))
                assert address == ("127.0.0.1", 0)
            def getsockname(self): return ("127.0.0.1", 25000)
            def close(self): self.closed = True
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        original = socket.socket
        socket.socket = Reservation
        try:
            for item in session.items:
                if item.nodeid in probe.TLS_NEGATIVE_MODES:
                    assert controls.reserve(item) == 25000
                    validated.append({"node": item.nodeid, "mode": item.callspec.params["mode"]})
        finally:
            socket.socket = original
    def pytest_runtest_protocol(self, item, nextitem):
        raise AssertionError("Collection regression must never execute a test")
with probe.native_tls_cases(guard, target,
    probe.digest((target / "tests/test_browser_entry.py").read_bytes())) as controls:
    result = pytest.main(["-q", "--collect-only", "-p", "no:cacheprovider",
        "-p", "pytest_asyncio.plugin", "-p", "aiohttp.pytest_plugin",
        "-o", "asyncio_mode=auto", "--rootdir", str(target),
        "--confcutdir", str(target),
        *[str(target / "tests" / node) for node in probe.NATIVE_CONTROLS.values()]],
        plugins=[Collection(), controls])
network = guard.verify()
assert result == 0 and len(observed) == 18 and len(validated) == 2
assert {row["node"]: row["mode"] for row in validated} == probe.TLS_NEGATIVE_MODES
receipt.write_text(json.dumps({"collected": len(observed), "tls": validated,
    "unknownAttempts": network["unknownAttempts"], "testBodiesRun": 0}), encoding="utf-8")
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script,
        str(ROOT), str(target), str(receipt), regression or ""],
        capture_output=True, text=True, timeout=30)
    if regression:
        assert child.returncode != 0 and not receipt.exists()
        assert ("not found" if regression == "legacy-ids" else "INTERNALERROR") in child.stdout + child.stderr
        return
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(receipt.read_text(encoding="utf-8"))
    assert result == {"collected": 18, "tls": [
        {"node": "tests/" + probe.NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"], "mode": "wrong-leaf"},
        {"node": "tests/" + probe.NATIVE_CONTROLS["auth_redirect_proxy_no_disclosure"], "mode": "redirect"},
    ], "unknownAttempts": 0, "testBodiesRun": 0}
    assert set(hashes) == set(probe.NATIVE_FILES)


def test_native_control_public_selection_and_source_hashes_are_allowlisted():
    result = report()
    result["nativeControls"] = {"status": "PASS", "stage": "complete", "checks": {
        "maintainedNativePrerequisites": "PASS"}, "selection": native_result(),
        "testSources": {name: "e"*64 for name in probe.NATIVE_FILES},
        "network": {"blockedOptionalIpv6Probes": 1, "unknownAttempts": 0,
                    "infrastructureSocketpairBinds": 3, "tlsNegativeReservations": 2,
                    "tlsNegativeListeners": 4, "tlsNegativeConnects": 2}}
    probe.validate_public(result)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["selection"]["cases"]["private_function"] = {}
    with pytest.raises(AssertionError):
        probe.validate_public(changed)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["testSources"]["private/path.py"] = "e"*64
    with pytest.raises(AssertionError):
        probe.validate_public(changed)
    changed = copy.deepcopy(result)
    changed["nativeControls"]["selection"]["cases"]["runtime_forced_cleanup"]["skips"] = 1
    with pytest.raises(AssertionError):
        probe.validate_public(changed)


@pytest.mark.parametrize("unknown", [False, True])
def test_run_model_preserves_unknown_job_scratch(tmp_path, monkeypatch, unknown):
    monkeypatch.setattr(probe, "require_ci", lambda: None)
    monkeypatch.setattr(probe, "canonical_temp", lambda _: tmp_path)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(probe.sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(build=26100), raising=False)
    monkeypatch.setattr(probe.uuid, "uuid4", lambda: types.SimpleNamespace(hex="c"*32))
    monkeypatch.setattr(probe, "sanitize", lambda root: None)
    monkeypatch.setattr(probe, "private_directory", lambda path, root: path.mkdir(parents=True, exist_ok=True))
    async def hosts(root, wheel, value):
        value["outerJobsSettled"] = not unknown
        raise TimeoutError("private diagnostic never published")
    monkeypatch.setattr(probe, "hosts", hosts)
    receipt = tmp_path / "result.json"
    probe.write_json(receipt, {"candidate": report()["candidate"]})
    root = tmp_path / ("ut-" + "c"*12)
    assert probe.run(tmp_path / "unused.whl", receipt) == 1
    result = json.loads(receipt.read_text())
    assert result["status"] == "FAIL" and result["scratchRemoved"] is not unknown
    assert root.exists() is unknown
    assert "private diagnostic" not in receipt.read_text()


@pytest.mark.parametrize("field,value", [
    ("name", "private/path"), ("version", "private prose"), ("commit", "private"),
])
def test_installed_graph_public_fields_are_validated(field, value):
    installation = {"wheelSha256": "d"*64, "verifiedMembers": 123,
        "imports": {name: {"origin": "installed", "sha256": "e"*64} for name in probe.IMPORTS},
        "graph": [{"name": "amplifier-core", "version": "2.0.2"}]}
    result = report()
    result["workers"] = [installation]
    probe.validate_public(result)
    installation["graph"][0][field] = value
    with pytest.raises(AssertionError):
        probe.validate_public(result)


def test_workflow_keeps_old_lane_commands_and_bounded_installed_candidate_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/platform-mechanisms.yml").read_text())
    # Strip only EOF scalar newline; appending a new job adds one to the prior
    # final block in YAML. No command, matrix, dependency or budget may change.
    golden = {"passive-process-check": "805c016a876edb93a512cf19e0c265a0bffc69ff2042c44bf68fdae8fe07dae0",
              "windows-private-config-tls": "7e73ef72ea372e123ecff0c8a5b3831268a629faf2a22054877241b9b9128c29"}
    for name, expected in golden.items():
        job = copy.deepcopy(workflow["jobs"][name])
        if name == "windows-private-config-tls":
            job["steps"][-1]["run"] = job["steps"][-1]["run"].rstrip("\n")
        assert hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest() == expected
    job = workflow["jobs"]["windows-user-trial"]
    assert job["timeout-minutes"] == 15 and job["runs-on"] == "windows-latest"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "uv build --wheel" in commands and "--only-binary amplifier-core" in commands
    assert '"pytest-asyncio>=1.4,<2"' in commands and '"pytest-aiohttp>=1.1,<2"' in commands
    assert job["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "-I -B $probe --run" in commands
    upload = job["steps"][-1]["with"]
    assert upload["path"] == "${{ runner.temp }}/ut/evidence/result.json"
    assert "*" not in upload["path"]