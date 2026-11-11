"""Closed private test transport; no production hook, install or receipt effects."""
import asyncio
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).parent / "fixtures/windows_supervised_update_transport.py"
SPEC = importlib.util.spec_from_file_location("private_windows_transport", PATH)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def plan(root):
    return {"schema": 1, "kind": "private-native-transport-fixture", "failBeforeIdentity": False,
            "target": {"version": "0.77.1", "revision": "c" * 40,
                       "path": str(root / "fixture-input/amplifier_unified-0.77.1-py3-none-any.whl"),
                       "sha256": hashlib.sha256(b"test wheel").hexdigest(), "bytes": 10}}


@pytest.mark.parametrize("change", ["extra", "schema", "failure", "revision", "hash", "size", "url",
                                  "timeout-type", "two-faults"])
def test_transport_plan_refuses_unbound_input(tmp_path, change):
    value = plan(tmp_path)
    if change == "extra": value["env"] = {"SECRET": "never"}
    elif change == "schema": value["schema"] = True
    elif change == "failure": value["failBeforeIdentity"] = "yes"
    elif change == "revision": value["target"]["revision"] = "main"
    elif change == "hash": value["target"]["sha256"] = "not a digest"
    elif change == "size": value["target"]["bytes"] = True
    elif change == "timeout-type": value["readinessTimeout"] = "yes"
    elif change == "two-faults":
        value.update(readinessTimeout=True, failBeforeIdentity=True)
    else: value["target"]["url"] = "https://untrusted.example"
    with pytest.raises(ValueError): fixture.validate(value)


def test_unready_fault_is_explicit_and_does_not_change_target_or_old_plan(tmp_path):
    value = plan(tmp_path)
    expected = copy.deepcopy(value["target"])
    assert fixture.validate(value) == expected
    value["readinessTimeout"] = True
    assert fixture.validate(value) == expected


@pytest.mark.parametrize("mode", ["ordinary", "old-unready", "target-unready"])
def test_only_exact_transport_is_substituted_and_no_host_state_is_written(tmp_path, monkeypatch, mode):
    from amplifier_web import app_updates, app_release_artifacts, windows_service, windows_files
    from amplifier_web import windows_read_files, update_readiness
    root = tmp_path / "candidates/windows-consumerfixture-sandbox-v1"
    value = plan(root)
    if mode != "ordinary":
        value["readinessTimeout"] = True
    if mode == "target-unready":
        from amplifier_web import server
        auth = object()
        app = SimpleNamespace(middlewares=[auth])
        async def create_app(*args, **kwargs):
            return app
        monkeypatch.setattr(server, "create_app", create_app)
    @contextmanager
    def directory(path, **kwargs):
        yield SimpleNamespace(read_private=lambda name, limit: json.dumps(value).encode())
    @contextmanager
    def open_file(path):
        yield io.BytesIO(b"test wheel")
    monkeypatch.setattr(windows_service, "SUPERVISED_UPDATES_QUALIFIED", True)
    monkeypatch.setattr(windows_files, "address", Path)
    monkeypatch.setattr(windows_files, "directory", directory)
    monkeypatch.setattr(windows_read_files, "open_file", open_file)
    verified = []
    monkeypatch.setattr(app_release_artifacts, "verify", lambda *args: verified.append(args))
    monkeypatch.setattr(update_readiness, "running_identity",
                        lambda: {"revision": ("c" if mode == "target-unready" else "a") * 40})
    calls = []
    async def read(*args, **kwargs):
        calls.append(("read", args, kwargs)); return "ordinary metadata"
    async def process(*args, **kwargs):
        calls.append(("process", args, kwargs)); return "ordinary process"
    monkeypatch.setattr(app_updates, "github_read", read)
    monkeypatch.setattr(app_updates, "process", process)
    # Restore fixture-installed wrappers through monkeypatch's saved owners.
    monkeypatch.setattr(app_release_artifacts, "_get", app_release_artifacts._get)
    result = fixture.install(SimpleNamespace(supervised=True, data_dir=root / "app"))
    assert result["transportSubstituted"] and not result["publicAcceptance"] and len(verified) == 1
    assert result["readinessWithheld"] == (mode == "target-unready")
    async def check():
        if mode == "target-unready":
            assert await server.create_app() is app
            assert app.middlewares[0] is auth and len(app.middlewares) == 2
            calls_to_handler = []
            async def handler(request):
                calls_to_handler.append(request.path)
                return "ordinary handler"
            fault = app.middlewares[-1]
            response = await fault(SimpleNamespace(path="/api/health"), handler)
            assert response.status == 503 and not calls_to_handler
            assert await fault(SimpleNamespace(path="/api/state"), handler) == "ordinary handler"
        metadata = json.loads(await app_updates.github_read("repos/microsoft/amplifier-unified/releases/latest"))
        assert metadata["tag_name"] == "v0.77.1"
        assert await app_updates.github_read("another/path") == "ordinary metadata"
        assert await app_updates.process("git", "ls-remote", app_updates.SOURCE,
            "refs/tags/v0.77.1", "refs/tags/v0.77.1^{}") == "c" * 40 + "\trefs/tags/v0.77.1"
        assert await app_updates.process("git", "checkout", "c" * 40, cwd=root) == "ordinary process"
        url = app_release_artifacts.SOURCE + "/releases/download/v0.77.1/amplifier_unified-0.77.1-py3-none-any.whl"
        async with app_release_artifacts._get(None, url) as response:
            assert b"".join([chunk async for chunk in response.aiter_bytes(3)]) == b"test wheel"
    asyncio.run(check())
    assert [row[0] for row in calls] == ["read", "process"]
    assert not root.exists()
    value.pop("readinessTimeout", None)
    value["failBeforeIdentity"] = True
    monkeypatch.setattr(update_readiness, "running_identity", lambda: {"revision": "c" * 40})
    with pytest.raises(RuntimeError, match="before runtime identity"):
        fixture.install(SimpleNamespace(supervised=True, data_dir=root / "app"))
    with pytest.raises(ValueError, match="isolated"):
        fixture.install(SimpleNamespace(supervised=True, data_dir=tmp_path / "app"))
