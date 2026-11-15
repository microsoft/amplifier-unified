"""Offline qualification of the real runtime_worker.py file and JSON-lines pipes.

The fresh child's explicit sitecustomize installs only a finite source resolver,
the source-only loop locator and bottom HTTPX/HTTPX2 transports. It never creates
a Worker or changes a provider, SDK resource, witness, graph, binding or publisher.
Source mode is the default. Explicit installed mode requires an outside-checkout
qualification venv and the manager's fixed file/distribution profile; it never
repairs package discovery or provisions an environment. Both modes use synthetic
bottom HTTP transport, not live-account or real-inference evidence.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
SESSION = "normal-media-file-entry"


def helpers():
    # Importing this helper does not run its main or construct its Worker.
    import normal_media_worker_probe
    return normal_media_worker_probe


def private_parent(root):
    assert root.is_dir() and not root.is_symlink()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700, "Probe parent must already be private (0700)"
    assert not any(root.iterdir()), "Use a fresh empty root; never replay an earlier probe"


def qualification_root(value, target=None):
    root = Path(value).absolute()
    private_parent(root)
    if target is not None:
        # The final component may be ordinary while an ancestor aliases a
        # checkout/installation. Refuse that before any fixture writes.
        resolved = root.resolve(strict=True)
        if (resolved != root or resolved.is_relative_to(ROOT.resolve())
                or resolved.is_relative_to(target.environment)):
            raise ValueError("Installed qualification root must be outside source and installation without aliases")
    return root


def save_json(path, value):
    path.write_text(json.dumps(value, default=str))
    path.chmod(0o600)


def typed_checkpoint_facts(binding):
    """Observe actual live receipt/result and cold bytes; JSON facts grant nothing."""
    from dataclasses import asdict
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.transcript_writer import CommitAck
    from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
    from amplifier_foundation.session.transcript_source import open_committed_source
    assert type(binding) is VersionedSessionBinding
    binding.check()
    result, receipt = binding.last_result, binding.last_receipt
    assert type(result) is CommitAck and type(receipt) is VersionedCheckpointReceipt
    assert result.commit == binding.expected
    expected = asdict(binding.expected)
    verified = receipt.verify()
    assert {key: verified[key] for key in expected} == expected
    with open_committed_source(binding.directory, expected=binding.expected) as source:
        assert source.commit == result.commit
        assert asdict(source.commit) == expected
        rows = source.window(0, 100).messages
    refs = list(receipt.iter_refs())
    actual_refs = [ref for row in rows
                   for ref in ((row.get("metadata") or {}).get(helpers().MEDIA) or {}).get("refs", [])]
    assert refs and refs == actual_refs
    binding.check()
    return {"prefix": expected, "receipt_type": type(receipt).__name__,
            "result_type": type(result).__name__, "kind": verified["kind"],
            "refs": refs}


def deny_socket_connections(stack, report):
    """Deny and record both socket connection entry points, before SDK imports."""
    import socket
    from unittest.mock import patch
    denied = []

    def no_socket(*args, **kwargs):
        denied.append({"operation": "socket.connect", "attempt": len(denied) + 1})
        save_json(report, denied)
        raise AssertionError("Offline pipe fixture denied an unintercepted socket connection")

    stack.enter_context(patch.object(socket.socket, "connect", no_socket))
    stack.enter_context(patch.object(socket.socket, "connect_ex", no_socket))
    return denied


def finite_sources(paths, modules, *, installed=False):
    """Same finite source policy as warm_worker_probe, without its app import.

    Delay only the explicit source-only loop locator until ordinary bundle
    resolution. The file's own canonical entry must bootstrap the app package;
    neither this fixture nor its PYTHONPATH preloads the Worker or fixes aliases.
    """
    from amplifier_foundation.paths.resolution import ResolvedSource
    from amplifier_foundation.sources import source_resolution_policy
    from amplifier_foundation.sources.policy import SourceResolutionDenied
    admitted = {str(Path(path).resolve(strict=True)): Path(path).resolve(strict=True) for path in paths}
    selected = Path(modules["loop-live"]).resolve(strict=True)
    assert helpers().origin("amplifier_module_loop_live").parent == selected
    located = False

    def resolve(uri, *, base_path):
        nonlocal located
        path = admitted.get(uri)
        if path is None:
            raise SourceResolutionDenied("Pipe fixture source is not explicitly local: " + uri)
        if not located and not installed:
            host = sys.modules.get("amplifier_web.host.session")
            assert host is not None, "Ordinary app bootstrap must precede source resolution"
            host.installed_loop_source = lambda: str(selected)
            located = True
        return ResolvedSource(active_path=path, source_root=path if path.is_dir() else path.parent)

    return source_resolution_policy(resolve)


def installed_target(profile, worker, *, bootstrap_app=False):
    """Explicit mode selector; absent/partial inputs never downgrade to source."""
    fixture = helpers()
    if profile is None and worker is None:
        return None
    if not profile or not worker:
        raise ValueError("Installed worker and profile must be supplied together")
    target = fixture.InstalledTarget(profile)
    if Path(worker).absolute() != target.worker:
        raise ValueError("Worker does not match installed profile")
    target.check_runtime()
    if bootstrap_app:
        target.bootstrap_app()
    fixture.INSTALLED_TARGET = target
    return target


def child_environment(bootstrap_dir, modules, root, target=None):
    """Installed mode discards overlays rather than trusting inherited PYTHONPATH."""
    env = dict(os.environ)
    # Only this invocation's explicit CLI selector may choose the negative.
    env.pop("NORMAL_MEDIA_CONTRACT_NEGATIVE", None)
    env.pop("NORMAL_MEDIA_DELEGATE_JOURNEY", None)
    env["NORMAL_MEDIA_PIPE_ROOT"] = str(root)
    fixture_paths = [str(bootstrap_dir), str(Path(__file__).parent)]
    if target is None:
        dependencies = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep)
                        if entry and Path(entry).resolve() != ROOT]
        env.update(WARM_LOOP_SOURCE=modules["loop-live"],
                   PYTHONPATH=os.pathsep.join([*fixture_paths, *dependencies]))
        env.pop("NORMAL_MEDIA_INSTALLED_PROFILE", None)
        env.pop("NORMAL_MEDIA_INSTALLED_WORKER", None)
    else:
        for key in ("WARM_LOOP_SOURCE", "PYTHONHOME", "PYTHONUSERBASE"):
            env.pop(key, None)
        env.update(PYTHONPATH=os.pathsep.join(fixture_paths), PYTHONNOUSERSITE="1",
                   NORMAL_MEDIA_INSTALLED_PROFILE=str(target.profile),
                   NORMAL_MEDIA_INSTALLED_WORKER=str(target.worker))
    return env


def bootstrap():
    """Explicit fixture-only child bootstrap, before importing any vendor SDK.

    Socket denial is installed FIRST, including HTTPX2's independent path. Even
    failed/unexpected routes cannot contact a real account. The profile observer
    is unnecessary: transport-local GC inspection reads the real Worker only.
    """
    import atexit
    from unittest.mock import patch

    root = Path(os.environ["NORMAL_MEDIA_PIPE_ROOT"]).resolve(strict=True)
    label = os.environ["NORMAL_MEDIA_PIPE_CHILD"]
    contract_negative = os.environ.get("NORMAL_MEDIA_CONTRACT_NEGATIVE") == "1"
    delegate_journey = os.environ.get("NORMAL_MEDIA_DELEGATE_JOURNEY") == "1"
    report = root / (label + "-observer.json")
    stack = ExitStack()
    denied = deny_socket_connections(stack, root / (label + "-socket-denials.json"))
    # No imports capable of starting an SDK precede those two guards.
    fixture = helpers()
    target = installed_target(os.environ.get("NORMAL_MEDIA_INSTALLED_PROFILE"),
                              os.environ.get("NORMAL_MEDIA_INSTALLED_WORKER"))
    if target and delegate_journey:
        assert "amplifier_module_tool_delegate" in target.packages, "Delegate journey needs its artifact/package profile"
    import importlib
    import importlib.util
    import gc
    import httpx

    class PipeResponses(fixture.OfflineResponses):
        def __init__(self, pixels):
            super().__init__(pixels)
            self.owners, self.snapshots = [], []
            self.typed_receipts = []
            self.expected_handles = {}
            self.native_children = []

        async def receive(self, request):
            try:
                import amplifier_web.runtime_worker as module
                owners = [obj for obj in gc.get_objects() if type(obj) is module.Worker]
                assert len(owners) == 1, "Actual file entry did not use the canonical Worker class"
                owner = owners[0]
                assert owner.command.__func__ is module.Worker.command
                assert owner.command.__func__.__globals__["publish"] is module.publish
                assert module.publish.__module__ == "amplifier_web.runtime_worker"
                assert module._PROTOCOL is not None
                assert str(ROOT) not in sys.path, "Outer app/source root leaked into the child dependency path"
                assert fixture.catalog_status() == catalog
                if target:
                    target.check_origins()
                if contract_negative:
                    return await self.receive_contract_negative(owner, request)
                assert owner.media_graph_selection is not None
                assert owner.canonical_binding is not None
                assert owner.canonical_resume is (label == "second")
                assert not owner.controls.capacity.policy["enabled"]
                self.check_installed_boundary(owner)
                # The real issuer object is verified before each SDK count/send
                # response, not inferred from hydration or lease contention.
                coordinator, binding = owner.session.coordinator, owner.canonical_binding
                if delegate_journey:
                    from amplifier_module_provider_openai import _media
                    provider_view = _media.CURRENT.get().provider.coordinator
                    provider_bridge = provider_view.get_capability("web.media.hook_bridge")
                    assert provider_bridge is not None
                    coordinator = provider_bridge._coordinator  # read-only scope identity, not authority
                    assert coordinator.session_id == provider_view.session_id
                    if coordinator is not owner.session.coordinator:
                        from amplifier_web.host.children import ChildCheckpoint
                        from amplifier_web.host.canonical_bootstrap import _ChildFactory
                        checkpoint = coordinator.get_capability("session.durable_checkpoint")
                        assert type(checkpoint) is ChildCheckpoint
                        assert checkpoint.child.coordinator is coordinator
                        assert checkpoint.ready is True
                        binding = checkpoint.binding
                        assert checkpoint.store.canonical_binding is binding
                        registry = owner.session.coordinator.get_capability("live.children")
                        assert registry.sessions[coordinator.session_id].coordinator is coordinator
                        assert registry.canonical_binding_factory is owner.canonical_binding_factory
                        assert type(owner.canonical_binding_factory) is _ChildFactory
                        assert owner.canonical_binding_factory.root is owner.canonical_binding
                        assert binding is not owner.canonical_binding
                        assert binding.directory == owner.canonical_binding_factory.parent / coordinator.session_id
                        root_rows = await owner.session.coordinator.get("context").get_messages()
                        root_refs = [ref for row in root_rows for ref in
                                     (row.get("metadata") or {}).get(fixture.MEDIA, {}).get("refs", [])]
                        assert len(root_refs) == 1 and root_refs[0]["session_id"] == SESSION
                        root_facts = typed_checkpoint_facts(owner.canonical_binding)
                        assert root_facts["refs"] == root_refs
                        native_facts = typed_checkpoint_facts(binding)
                        assert native_facts["refs"] and all(
                            ref["session_id"] == coordinator.session_id for ref in native_facts["refs"])
                        assert all(ref != root_refs[0] for ref in native_facts["refs"])
                        if not any(row[0] is binding for row in self.native_children):
                            self.native_children.append((binding, coordinator.get_capability("web.media.store"), coordinator))
                self.typed_receipts.append(typed_checkpoint_facts(binding))
                if delegate_journey and coordinator is not owner.session.coordinator:
                    graph = coordinator.get_capability("web.media.hook_graph")
                    selection = owner.media_graph_selection
                    evidence = graph.participants_evidence() if graph else {}
                    bridge = coordinator.get_capability("web.media.hook_bridge")
                    state = sys.modules["amplifier_web.host.media"]._CONSTRUCTIONS.get(id(coordinator))
                    join = coordinator.get_capability("web.media.source_join")
                    capability = coordinator.get_capability(fixture.MEDIA)
                    save_json(root / (label + "-native-graph-diagnosis.json"), {
                        "session_id": coordinator.session_id,
                        "plan_accepted": selection.accepts_plan(coordinator.config),
                        "construction_accepted": selection.accepts_construction(graph) if graph else False,
                        "capability_enabled": coordinator.get_capability(fixture.MEDIA).enabled,
                        "source_join": coordinator.get_capability("web.media.source_join") is not None,
                        "receipt_valid": bridge.validate_receipt(bridge.graph_receipt),
                        "state_join_identity": state is not None and state[4] is join[2],
                        "consumer_state": {key: value for key, value in vars(capability).items()
                                           if "consumer" in key},
                        "callbacks": [{"kind": kind, "accepted": selection.accepts_callable(row[field]),
                                       "name": getattr(row[field], "__qualname__", type(row[field]).__name__)}
                                      for kind, field in (("loads", "mount"), ("ready", "callback"),
                                                          ("registrations", "original"), ("contributions", "original"))
                                      for row in evidence.get(kind, ())]})
                if not self.owners:
                    self.owners.append(owner)
                else:
                    assert self.owners == [owner]
                result = await super().receive(request)
                if delegate_journey:
                    result = self.delegate_response(owner, coordinator, request, result)
                media = coordinator.get_capability("web.media.store")
                self.snapshots.append({
                    "worker_class": type(owner).__module__ + "." + type(owner).__qualname__,
                    "wire_http_package": type(request).__module__.split(".")[0],
                    "worker_id": id(owner), "session_id": id(owner.session),
                    "execution_id": id(owner.execution), "shared_id": id(owner.shared_handle),
                    "binding_id": id(binding), "media_id": id(media),
                    "canonical_directory": str(binding.directory),
                    "media_root": str(media.root), "media_session_id": media.session_id,
                    "resumed": owner.canonical_resume,
                    **({"native_session_id": coordinator.session_id,
                        "native_resumed": coordinator.get_capability("web.child.resumed") is True,
                        "native_child": coordinator is not owner.session.coordinator}
                       if delegate_journey else {}),
                })
                self.dump(False)
                return result
            except BaseException as error:
                if not any(error is item for item in self.failures):
                    self.failures.append(error)
                self.dump(False)
                raise

        def delegate_response(self, owner, coordinator, request, result):
            if request.url.path.endswith("/input_tokens") or coordinator is not owner.session.coordinator:
                return result
            inputs = [value for value in owner.runtime.generation["input_ids"] if value in {"one", "two", "three"}]
            assert len(inputs) == 1
            turn = inputs[0]
            if getattr(self, "delegated_turn", None) == turn:
                return result
            self.delegated_turn = turn
            arguments = {"agent": "self", "instruction": "Inspect the inherited image.",
                         "context_depth": "all", "context_scope": "conversation", "async": False}
            if turn != "one":
                previous = (json.loads((root / "first-observer.json").read_text())["snapshots"]
                            if label == "second" else self.snapshots)
                identities = {row["native_session_id"] for row in previous if row.get("native_child")}
                assert len(identities) == 1
                arguments["session_id"] = identities.pop()
            value = fixture.response(len(self.records))
            value["output"] = [{"type": "function_call", "id": "fc_delegate_" + turn,
                                "call_id": "call_delegate_" + turn, "name": "delegate",
                                "arguments": json.dumps(arguments), "status": "completed"}]
            package = importlib.import_module(type(request).__module__.split(".")[0])
            return package.Response(200, json=value)

        async def receive_contract_negative(self, owner, request):
            """Observe the real refusal; never enable or substitute a graph."""
            from amplifier_module_provider_openai import OpenAIProvider, _media
            from amplifier_foundation.session.media import SessionMediaStore
            from amplifier_web.host.canonical_bootstrap import _ChildFactory
            from amplifier_web.host.children import Children
            from dataclasses import asdict
            from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
            from amplifier_foundation.session.transcript_writer import CommitAck
            from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
            from amplifier_foundation.session.transcript_source import open_committed_source
            scope = _media.CURRENT.get()
            assert scope is not None and type(scope.provider) is OpenAIProvider
            coordinator = scope.provider.coordinator
            root_coordinator = owner.session.coordinator
            children = root_coordinator.get_capability("live.children")
            factory = owner.canonical_binding_factory
            assert type(children) is Children and type(factory) is _ChildFactory
            self.child_registry = children
            assert children.canonical_binding_factory is factory and factory.root is owner.canonical_binding
            assert owner.media_graph_selection is None
            assert coordinator.get_capability("web.media.graph_selection") is None
            media = coordinator.get_capability("web.media.store")
            assert type(media) is SessionMediaStore, type(media).__name__
            assert coordinator.get_capability(fixture.MEDIA).enabled is False
            tool = root_coordinator.get("tools")["media_child"]
            # Host wrappers remain intact. Diagnose their declared delegate
            # chain read-only, rather than remounting the authored tool.
            chain = []
            for _ in range(8):
                chain.append({"class": type(tool).__module__ + "." + type(tool).__qualname__,
                              "source": tool.execute.__func__.__code__.co_filename})
                if Path(chain[-1]["source"]).resolve() == root / "child-tool/__init__.py":
                    break
                tool = getattr(tool, "original", None)
                assert tool is not None, chain
            assert Path(chain[-1]["source"]).resolve() == root / "child-tool/__init__.py", chain
            assert (root / "child-tool/__init__.py").read_text() == fixture.CHILD_TOOL
            assert hashlib.sha256((root / "child-tool/__init__.py").read_bytes()).hexdigest() == fixture.CHILD_TOOL_SHA256
            assert callable(coordinator.get_capability("session.spawn"))
            assert callable(coordinator.get_capability("session.resume"))
            assert scope.provider.coordinator is coordinator and _media.standard_dispatch(scope.provider.client)
            assert scope.cap is None or scope.cap.enabled is False
            body = json.loads(request.content)
            assert request.method == "POST" and request.headers["authorization"] == "Bearer " + fixture.DUMMY_KEY
            assert str(request.url) in {"https://api.openai.com/v1/responses/input_tokens",
                                        "https://api.openai.com/v1/responses"}
            assert body["model"] == fixture.MODEL and body.get("stream") is not True
            fixture.no_pixels(body, pixels)
            is_child = coordinator is not root_coordinator
            binding = owner.canonical_binding
            child_row = None
            if is_child:
                assert coordinator.session_id == "normal-media-native-child"
                assert children.sessions[coordinator.session_id].coordinator is coordinator
                child_row = children.rows[coordinator.session_id]
                assert child_row["parentSessionId"] == SESSION
                assert child_row["callId"] == "call_media_child_spawn"
                checkpoint = coordinator.get_capability("session.durable_checkpoint")
                from amplifier_web.host.children import ChildCheckpoint
                assert type(checkpoint) is ChildCheckpoint
                binding = checkpoint.binding
                assert checkpoint.child.coordinator is coordinator
                assert checkpoint.store.canonical_binding is binding
                assert type(binding) is VersionedSessionBinding
                assert binding.session_id == coordinator.session_id
                assert binding.directory == factory.parent / coordinator.session_id
                if not any(row[0] is binding for row in self.native_children):
                    self.native_children.append((binding, media, coordinator))
            else:
                assert "compatible media handling is not installed" in json.dumps(body)
            binding.check()
            assert type(binding.last_result) is CommitAck
            assert type(binding.last_receipt) is VersionedCheckpointReceipt
            assert binding.last_result.commit == binding.expected
            prefix = asdict(binding.expected)
            assert {key: binding.last_receipt.verify()[key] for key in prefix} == prefix
            assert list(binding.last_receipt.iter_refs()) == []
            with open_committed_source(binding.directory, expected=binding.expected) as cold:
                assert cold.commit == binding.expected
                cold_rows = cold.window(0, 100).messages
            fixture.no_pixels(cold_rows, pixels)
            assert not any(fixture.MEDIA in (row.get("metadata") or {}) for row in cold_rows)
            rows = await coordinator.get("context").get_messages()
            fixture.no_pixels(rows, pixels)
            assert not any(fixture.MEDIA in (row.get("metadata") or {}) for row in rows)
            if not self.owners:
                self.owners.append(owner)
            else:
                assert self.owners == [owner]
            phase = "count" if request.url.path.endswith("/input_tokens") else "send"
            self.requests.append(scope.request)
            self.records.append({"phase": phase, "request_id": id(scope.request),
                                 "provider_id": id(scope.provider), "pixels_supplied": False,
                                 "session_id": coordinator.session_id})
            self.snapshots.append({"recipe_selected": False, "media_store": type(media).__name__,
                                   "real_child_factory": type(factory).__name__,
                                   "filesystem_tool_loaded": True, "child_dispatched": bool(children.rows),
                                   "session_id": coordinator.session_id, "binding_id": id(binding),
                                   "canonical_directory": str(binding.directory),
                                   "media_root": str(media.root), "media_session_id": media.session_id,
                                   "prefix": prefix,
                                   "call_id": child_row["callId"] if child_row else None})
            self.dump(False)
            package = importlib.import_module(type(request).__module__.split(".")[0])
            result = fixture.response(len(self.records))
            if not is_child and phase == "send" and not children.rows:
                result["output"] = [{"type": "function_call", "id": "fc_media_child_spawn",
                                     "call_id": "call_media_child_spawn", "name": "media_child",
                                     "arguments": '{"resume":false}', "status": "completed"}]
            return package.Response(200, json=(
                {"object": "response.input_tokens", "input_tokens": 30} if phase == "count" else result))

        def dump(self, final):
            import traceback
            save_json(report, {
                "final": final, "pid": os.getpid(), "records": self.records,
                "snapshots": self.snapshots, "socket_denials": denied,
                "typed_receipts": self.typed_receipts,
                "failures": [type(item).__name__ + ": " + str(item) for item in self.failures],
                "failure_frames": [[{"file": frame.filename, "line": frame.lineno, "name": frame.name}
                                    for frame in traceback.extract_tb(item.__traceback__)]
                                   for item in self.failures],
            })

    pixels = (root / "private.png").read_bytes()
    transport = PipeResponses(pixels)
    # Deliberately do not use OfflineResponses.installed(): this bootstrap must
    # install socket denial before importing either independent HTTP package.
    packages = [httpx]
    if importlib.util.find_spec("httpx2") is not None:
        packages.append(importlib.import_module("httpx2"))
    for package in packages:
        mock = package.MockTransport(transport.receive)

        def boundary(mock_transport):
            async def handle(_transport, request):
                return await mock_transport.handle_async_request(request)
            return handle

        handle = boundary(mock)
        transport.expected_handles[package.AsyncHTTPTransport] = handle
        stack.enter_context(patch.object(package.AsyncHTTPTransport, "handle_async_request", handle))

    declaration = fixture.authored_bundle(root, child_tool=contract_negative, delegate=delegate_journey)
    modules = json.loads((root / "source-modules.json").read_text())
    paths = [root / "fixture.md", *modules.values(),
             declaration["session"]["context"]["source"],
             *[row["source"] for row in declaration["providers"]],
             *[row["source"] for row in declaration["hooks"]],
             *[row["source"] for row in declaration.get("tools", [])]]
    stack.enter_context(finite_sources(paths, modules, installed=target is not None))
    catalog = json.loads((root / "catalog-observed.json").read_text())
    assert not catalog["mismatches"], {"boundary": "unchanged packaged catalog", **catalog}
    assert "amplifier_web.runtime_worker" not in sys.modules, "Fixture must not preload the Worker"
    transport.dump(False)

    def finish():
        try:
            assert not transport.failures
            assert not denied, "An unintercepted connection was attempted"
            if contract_negative:
                assert sum(row["phase"] == "send" for row in transport.records) == 3
                assert any(row["phase"] == "count" for row in transport.records)
                assert len(transport.native_children) == 1
                from amplifier_foundation.session.transcript_lifetime import VersionedSessionConflict
                for binding, media, coordinator in transport.native_children:
                    try:
                        binding.check()
                    except VersionedSessionConflict:
                        pass
                    else:
                        raise AssertionError("Actual native child writer remained open")
                    assert coordinator.session_id not in transport.child_registry.sessions
            else:
                transport.check_pairs((6 if label == "first" else 3) if delegate_journey
                                      else (2 if label == "first" else 1))
                if delegate_journey:
                    assert len(transport.native_children) == (2 if label == "first" else 1)
                    from amplifier_foundation.session.transcript_lifetime import VersionedSessionConflict
                    for binding, _, _ in transport.native_children:
                        try:
                            binding.check()
                        except VersionedSessionConflict:
                            pass
                        else:
                            raise AssertionError("Delegate native child binding remained open")
            assert len(transport.owners) == 1
            owner = transport.owners[0]
            assert owner._cleanup_settled and owner.shared_handle is None
            assert owner.canonical_binding is None and owner.execution.done()
            assert owner.execution.exception() is None
            assert fixture.catalog_status() == catalog, "Catalog or dependency source changed during the probe"
            transport.dump(True)
            value = json.loads(report.read_text())
            value.update(private_requests_cleared=not contract_negative, canonical_cleanup_joined=True,
                         file_bootstrapped_app_without_outer_root=True,
                         source=fixture.source_evidence(),
                         transport_packages=[package.__name__ for package in packages])
            fixture.no_pixels(value, pixels)
            save_json(report, value)
        except BaseException as error:
            transport.failures.append(error)
            transport.dump(True)
        finally:
            stack.close()

    atexit.register(finish)


def host_result(event, *, child_ids=()):
    """Finite known-service fixture; unknown operations fail closed."""
    operation, args = event["operation"], event["args"]
    context = {"surfaces": [], "inputIds": args.get("_contextInputs", [])}
    current = {"sessionId": SESSION, "grants": [], "requests": [], "bounded": True}
    if operation == "context.prepare":
        return {"context.manifest": {"available": True, "value": context},
                "message.reactions.context": {"available": False},
                "coordination.current": {"available": True, "value": current},
                "memory.context": {"available": True, "value": {"items": []}}}
    if operation == "context.manifest":
        return context
    if operation == "coordination.current":
        return current
    if operation == "memory.context":
        return {"items": []}
    if operation == "capacity.admit":
        assert args["call"]["sessionId"] in {SESSION, "normal-media-native-child", *child_ids}
        # This fresh fixture has the ordinary disabled budget (also independently
        # checked on the actual child owner at count/send), not an account change.
        return {"allowed": True, "budgetRevision": 0}
    if operation == "work.effect.check":
        if args["toolName"] == "media_child":
            assert args["toolInput"] == {"resume": False}
            assert args["toolCallId"] == "call_media_child_spawn"
        else:
            assert args["toolName"] == "delegate"
            assert args["toolInput"]["agent"] == "self"
            assert args["toolInput"]["instruction"] == "Inspect the inherited image."
            assert args["toolInput"]["context_depth"] == "all"
            assert args["toolInput"]["context_scope"] == "conversation"
            # loop-live consumes its explicit async:false execution selector
            # before passing public delegate arguments to the guarded tool.
            assert "async" not in args["toolInput"]
            assert set(args["toolInput"]) <= {"agent", "instruction", "context_depth", "context_scope", "session_id", "async"}
            assert args["toolCallId"] in {"call_delegate_one", "call_delegate_two", "call_delegate_three"}
        assert args["_runtimeSessionId"] == SESSION
        # Finite synthetic host permits this sole local fixture call. No host
        # defaults/production guard are bypassed or replaced.
        return {"allowed": True, "effectAuthorized": True}
    raise AssertionError("Unexpected pipe fixture bridge: " + operation)


class Pipe:
    def __init__(self, process, pixels, *, contract_negative=False):
        self.process, self.pixels = process, pixels
        self.contract_negative = contract_negative
        self.events, self.commands = [], []
        self.changed, self.eof = asyncio.Event(), False
        self.eof_index = None
        self.reader = asyncio.create_task(self.read())
        self.stderr = asyncio.create_task(process.stderr.read(2_000_000))

    async def write(self, command):
        from amplifier_web.runtime_protocol import encode_message
        self.commands.append(command["op"])
        self.process.stdin.write(encode_message(command))
        await self.process.stdin.drain()

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                event = json.loads(line)
                helpers().no_pixels(event, self.pixels)
                self.events.append(event)
                assert event.get("type") not in {
                    "error", "runtime.error", "worker.error", "generation.failed"}, event
                assert not (event.get("op") == "reply" and event.get("error")), event
                if event.get("op") == "bridge":
                    child_ids = {row["event"]["sessionId"] for row in self.events
                                 if row.get("type") == "execution.event"
                                 and row.get("event", {}).get("kind") == "worker"
                                 and row["event"].get("parentSessionId") == SESSION}
                    await self.write({"op": "bridge.result", "id": event["id"],
                                      "result": host_result(event, child_ids=child_ids)})
                self.changed.set()
            self.eof_index = len(self.events)
        finally:
            self.eof = True
            self.changed.set()

    async def until(self, predicate, *, after=0):
        async with asyncio.timeout(35):
            while True:
                self.changed.clear()
                for event in self.events[after:]:
                    if predicate(event):
                        return event
                if self.reader.done():
                    await self.reader
                    raise AssertionError("Worker EOF before required JSON reply")
                await self.changed.wait()

    async def turn(self, identity, upload=None):
        start = len(self.events)
        command = {"op": "send", "id": identity + "-request", "input_id": identity, "text": identity}
        if upload is not None:
            command["attachments"] = [upload]
        await self.write(command)
        reply = await self.until(lambda row: row.get("op") == "reply"
                                 and row.get("id") == identity + "-request", after=start)
        assert reply["result"]["accepted"] is True
        await self.until(lambda row: row.get("type") == "generation.finished"
                         and identity in row.get("input_ids", []), after=start)
        await self.until(lambda row: row.get("type") == "session.idle", after=start)

    async def retire(self, label):
        start = len(self.events)
        await self.write({"op": "retire", "id": label})
        reply = await self.until(lambda row: row.get("op") == "reply"
                                 and row.get("id") == label, after=start)
        assert reply["result"] == {"retired": True}, reply
        # Observing the exact reply in the real stdout stream precedes observing
        # EOF there. stdin remains OPEN until the child has exited by itself.
        assert not self.process.stdin.is_closing()
        async with asyncio.timeout(15):
            await self.reader
            await self.process.wait()
        assert self.process.returncode == 0
        self.process.stdin.close()
        stderr = (await self.stderr).decode(errors="replace")
        helpers().no_pixels(stderr, self.pixels)
        forbidden = ("Traceback (most recent call last)", "Task was destroyed",
                     "Task exception was never retrieved", "coroutine was never awaited",
                     "Exception ignored in", "Error in sitecustomize")
        assert not any(text in stderr for text in forbidden), stderr
        allowed_naming_warning = (
            "Error generating name for session " + SESSION[:8] +
            (": Private media request, selected connection or wire changed/expired/revoked; "
             "prepare and fit a new request. Request not sent." if self.contract_negative else
             ": Naming cannot attribute the selected provider request; no call was sent."))
        assert all(line == allowed_naming_warning for line in stderr.splitlines()), stderr
        assert sum(row.get("type") == "session.closed" for row in self.events) == 1
        reply_index = next(index for index, row in enumerate(self.events) if row is reply)
        assert self.eof_index is not None and reply_index < self.eof_index
        return {"reply_before_eof": reply_index < self.eof_index,
                "retirement_reply_index": reply_index, "stdout_eof_index": self.eof_index,
                "exit_code": self.process.returncode,
                "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                "stderr_lines": stderr.splitlines(), "event_count": len(self.events),
                "commands": self.commands,
                "bridge_operations": sorted({row["operation"] for row in self.events if row.get("op") == "bridge"})}


def manager_command(python, root, target=None):
    """Read the real manager's final file path, without preparation/provisioning.

    Only its finite launcher setup lookups are substituted. We run the selected
    prepared interpreter directly, not uv, and do not claim manager preparation.
    """
    from unittest.mock import patch
    from amplifier_web.runtime import RuntimeManager
    project = python.parent.parent
    with patch("amplifier_web.host_dependencies.require_uv", return_value="uv"), \
            patch("amplifier_web.runtime_environment.prepare_project", return_value=project), \
            patch("amplifier_web.runtime_environment.receipt_directory", return_value=project):
        selected = RuntimeManager()._command(release=None, home=root / "home")
    script = Path(selected[-1]).resolve(strict=True)
    assert script == (target.worker if target else ROOT / "amplifier_web/runtime_worker.py")
    if target:
        target.verify_file(script)
    return [str(python), str(script)], selected


def checkpoint(directory, pixels):
    from amplifier_foundation.session.transcript_source import open_committed_source
    with open_committed_source(directory) as source:
        assert source.commit.message_count < 100
        messages = source.window(0, 100).messages
        commit = source.commit
    helpers().no_pixels(messages, pixels)
    rows = [row for row in messages if helpers().MEDIA in (row.get("metadata") or {})]
    assert len(rows) == 1 and len(rows[0]["metadata"][helpers().MEDIA]["refs"]) == 1
    return commit, messages, rows[0]["metadata"][helpers().MEDIA]["refs"][0]


def custody(snapshot, commit, held):
    from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding, VersionedSessionConflict
    try:
        media = SessionMediaStore(snapshot["media_root"], snapshot["media_session_id"])
    except MediaRefusal as error:
        assert held and "active writer" in str(error)
    else:
        media.close()
        assert not held, "Idle child lost its physical media writer"
    try:
        binding = VersionedSessionBinding.resume(snapshot["canonical_directory"],
                                                session_id=snapshot["media_session_id"], expected=commit)
    except VersionedSessionConflict as error:
        assert held and "retains the source lease" in str(error)
    else:
        binding.close()
        assert not held, "Idle child lost its physical canonical writer"


async def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--installed-worker")
    parser.add_argument("--installed-profile")
    parser.add_argument("--contract-negative", action="store_true",
                        help="Reproduce the existing nonempty-tool recipe refusal; NOT child qualification")
    parser.add_argument("--delegate-journey", action="store_true",
                        help="Require reviewed canonical delegate source and actual child private hydration")
    args = parser.parse_args()
    fixture = helpers()
    # This happens before application imports, fixture settings, or worker spawn.
    target = installed_target(args.installed_profile, args.installed_worker, bootstrap_app=True)
    if target and args.delegate_journey:
        assert "amplifier_module_tool_delegate" in target.packages, "Delegate journey needs its artifact/package profile"
    if target:
        target.check_origins()
    root = qualification_root(args.root, target)
    os.environ.update(HOME=str(root / "os-home"), AMPLIFIER_HOME=str(root / "amplifier-home"),
        AMPLIFIER_WEB_HOME=str(root / "home"), AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "shared"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "capture"),
        AMPLIFIER_RUNTIME_IMMUTABLE="1", UV_OFFLINE="1")
    catalog = fixture.catalog_status()
    assert not catalog["mismatches"], {"boundary": "packaged source catalog", **catalog}
    save_json(root / "catalog-observed.json", catalog)
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.attachments import save
    from PIL import Image

    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    bundle = root / "fixture.md"
    assert not (args.contract_negative and args.delegate_journey)
    bundle.write_text("---\n" + json.dumps(fixture.authored_bundle(
                          root, child_tool=args.contract_negative, delegate=args.delegate_journey))
                      + "\n---\nInspect the image.\n")
    if args.contract_negative:
        tool = root / "child-tool"
        tool.mkdir(mode=0o700)
        (tool / "__init__.py").write_text(fixture.CHILD_TOOL)
    modules = {name: str(fixture.origin(package).parent) for name, package in (
        ("loop-live", "amplifier_module_loop_live"),
        ("hook-context-intelligence", "amplifier_module_hook_context_intelligence"))}
    save_json(root / "source-modules.json", modules)
    settings = root / "amplifier-home/settings.yaml"
    settings.parent.mkdir(mode=0o700)
    save_json(settings, {"bundle": {"active": str(bundle), "app": []},
                         "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
                         "sources": {"modules": modules}})
    raw = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 71, 149)).save(raw, format="PNG")
    pixels = raw.getvalue()
    (root / "private.png").write_bytes(pixels)
    (root / "private.png").chmod(0o600)
    upload = save(root / "home", "normal.png", base64.b64encode(pixels).decode())
    bootstrap_dir = root / "fixture-bootstrap"
    bootstrap_dir.mkdir(mode=0o700)
    # Explicit child PYTHONPATH, not a production test hook or sys.modules alias.
    (bootstrap_dir / "sitecustomize.py").write_text(
        "from normal_media_pipe_probe import bootstrap\nbootstrap()\n")
    python = target.python if target else Path(sys.executable).absolute()
    command, selected = manager_command(python, root, target)
    env = child_environment(bootstrap_dir, modules, root, target)
    if args.contract_negative:
        env["NORMAL_MEDIA_CONTRACT_NEGATIVE"] = "1"
    if args.delegate_journey:
        env["NORMAL_MEDIA_DELEGATE_JOURNEY"] = "1"
    config = {"id": SESSION, "workspace": str(workspace), "bundle": str(bundle)}
    if args.delegate_journey:
        config["selection"] = {"instance": "openai", "model": fixture.MODEL}
    children, receipts, reports = [], [], []
    saved = ref = None
    try:
        journeys = (("first", ("one",)),) if args.contract_negative else (
            ("first", ("one", "two")), ("second", ("three",)))
        for label, turns in journeys:
            process = await asyncio.create_subprocess_exec(*command, cwd=workspace,
                env={**env, "NORMAL_MEDIA_PIPE_CHILD": label},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=2_000_000)
            pipe = Pipe(process, pixels, contract_negative=args.contract_negative)
            children.append(pipe)
            await pipe.write({"op": "start", "session": config})
            ready = await pipe.until(lambda row: row.get("type") == "runtime.ready")
            assert ready["workerIdentity"] == {"pid": process.pid, "sessionId": SESSION}
            assert ready["report"]["resumed"] is (label == "second")
            report_path = root / (label + "-observer.json")
            # Protocol roundtrip fences startup; no counted/sent request may have
            # occurred while resuming the exact saved transcript.
            await pipe.write({"op": "dependencies", "id": label + "-readback", "integrationValidation": True})
            await pipe.until(lambda row: row.get("op") == "reply" and row.get("id") == label + "-readback")
            assert not json.loads(report_path.read_text())["records"], "Startup replayed provider input"
            if saved is not None:
                _, restored, restored_ref = checkpoint(snapshot["canonical_directory"], pixels)
                assert restored == saved and restored_ref == ref
            for identity in turns:
                await pipe.turn(identity, upload if identity == "one" else None)
                # session.idle is an event, not writer release. Give the loop one
                # scheduling tick; all custody probes below are passive failures.
                await asyncio.sleep(.05)
                observed = json.loads(report_path.read_text())
                assert not observed["failures"] and not observed["socket_denials"], observed
                if args.contract_negative:
                    continue
                snapshot = next(row for row in reversed(observed["snapshots"])
                                if not row.get("native_child"))
                commit, messages, current_ref = checkpoint(snapshot["canonical_directory"], pixels)
                if ref is None:
                    ref = current_ref
                assert current_ref == ref
                custody(snapshot, commit, True)
            receipts.append(await pipe.retire("retire-" + label))
            report = json.loads(report_path.read_text())
            assert report["final"] and not report["failures"] and not report["socket_denials"], report
            assert report["canonical_cleanup_joined"]
            if args.contract_negative:
                assert report["snapshots"] and all(row["recipe_selected"] is False for row in report["snapshots"])
                from amplifier_foundation.session.transcript_source import open_committed_source
                child_snapshots = [row for row in report["snapshots"] if row["session_id"] != SESSION]
                assert child_snapshots and all(row["call_id"] == "call_media_child_spawn" for row in child_snapshots)
                with open_committed_source(child_snapshots[-1]["canonical_directory"]) as source:
                    commit = source.commit
                    rows = source.window(0, 100).messages
                fixture.no_pixels(rows, pixels)
                assert rows and not any(fixture.MEDIA in (row.get("metadata") or {}) for row in rows)
                custody(child_snapshots[-1], commit, False)
                evidence = {"mode": "installed-offline-transport" if target else "source-only",
                            "contract_blocker_reproduced": True, "root_child_hydration_qualified": False,
                            "first_loss": "root recipe admission before upload ingestion",
                            "actual_native_child_executed": True,
                            "child_writer_closed_and_reacquired": True,
                            "real_file_entry": command, "receipt": receipts[-1], "observer": report,
                            "missing_contract": "Preconstruction source-known authored-tool recipe selection"}
                fixture.no_pixels(evidence, pixels)
                print(json.dumps(evidence))
                return
            assert report["private_requests_cleared"]
            reports.append(report)
            commit, saved, current_ref = checkpoint(snapshot["canonical_directory"], pixels)
            assert current_ref == ref
            custody(snapshot, commit, False)
        assert children[0].process.pid != children[1].process.pid
        users = [row for row in saved if (row.get("metadata") or {}).get("amplifier_input", {}).get("kind") == "user"]
        assert [row["content"] if isinstance(row["content"], str) else row["content"][0]["text"]
                for row in users] == ["one", "two", "three"]
        assert sum(row.get("type") == "assistant.message" for pipe in children for row in pipe.events) == 3
        assert fixture.catalog_status() == catalog
        for report in reports:
            snapshots = report["snapshots"]
            root_snapshots = [row for row in snapshots if not row.get("native_child")]
            assert all(row == root_snapshots[0] for row in root_snapshots)
        # Installed status is reported only after the real lifecycle and both
        # child loaded-origin/file-manifest checks succeeded, never from a flag.
        if target:
            target.check_origins()
            assert all(report["source"]["mode"] == "installed-offline-transport"
                       and report["source"]["installed_package_parity"] for report in reports)
        evidence = {"mode": "installed-offline-transport" if target else "source-only",
                    "installed_package_parity": target is not None,
                    "manager_preparation_executed": False,
                    "real_file_entry": command, "manager_selected_command_not_executed": selected,
                    "actual_vendor_provider_and_sdk": True, "offline_transport_not_inference": True,
                    "two_turn_idle_custody": True, "fresh_process_resume_without_replay": True,
                    "canonical_t2_ack": True, "canonical_and_protocol_no_pixels": True,
                    "cas_scope": "successful exact-prefix path; idle contention is lease refusal, not independent stale-CAS rejection",
                    "receipts": receipts, "children": reports}
        if args.delegate_journey:
            native = [row for report in reports for row in report["snapshots"] if row["native_child"]]
            assert len({row["native_session_id"] for row in native}) == 1
            assert any(row["native_resumed"] for row in native)
            evidence.update(actual_delegate_native_child_hydration=True,
                            fresh_process_native_child_resume_without_replay=True)
        fixture.no_pixels(evidence, pixels)
        print(json.dumps(evidence))
    finally:
        # Only our own newly launched test children. No unknown live worker,
        # daemon, DTU, account or managed-resource mutation is involved.
        for pipe in children:
            if pipe.process.returncode is None:
                pipe.process.terminate()
                try:
                    await asyncio.wait_for(pipe.process.wait(), 10)
                except TimeoutError:
                    pipe.process.kill()
                    await pipe.process.wait()
            await asyncio.gather(pipe.reader, pipe.stderr, return_exceptions=True)
            stderr = pipe.stderr.result()
            fixture.no_pixels(stderr.decode(errors="replace"), pixels)
            fixture.no_pixels(pipe.events, pixels)
            save_json(root / ("child-" + str(pipe.process.pid) + "-protocol.json"), pipe.events)
            save_json(root / ("child-" + str(pipe.process.pid) + "-stderr.json"),
                      {"stderr": stderr.decode(errors="replace")})


if __name__ == "__main__":
    asyncio.run(main())