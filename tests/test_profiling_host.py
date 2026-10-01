"""Opt-in host profiling traverses the real authenticated service boundary."""
import asyncio
import builtins
import json
import os
import time
from pathlib import Path

import pytest

from amplifier_web.deployment import load_server_config, save_server_config, validate_server
from amplifier_web.profiling_host import ProfilingHost, profiling_enabled
from amplifier_web.server import create_app
from amplifier_web.service import AppError
from test_service import Runtime


def profiling_cpu_fixture():
    deadline = time.monotonic() + 0.15
    private_local = "PROFILE_PRIVATE_LOCAL"
    value = 9
    while time.monotonic() < deadline:
        value = (value * 17 + 3) % 1000000007
    return private_local, value


async def quiet(service):
    await service.diagnostics.close()
    await service.history.close()
    await service.event_log_view.close()
    await service.schedules.close()
    await service.observations.close()


async def host(tmp_path, enabled=True):
    pytest.importorskip("amplifier_profiling")
    config = load_server_config(tmp_path)
    config["debug"]["profiling"] = enabled
    save_server_config(tmp_path, config)
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False)
    await quiet(app["service"])
    return app


@pytest.mark.parametrize("value", [True, "yes", [], {"profiling": 1}, {"profiling": "true"},
                                 {"profiling": {"enabled": True}}])
def test_debug_extension_preserved_but_malformed_capture_fails_closed(value):
    config = validate_server({"debug": value})
    assert config["debug"] == value
    assert profiling_enabled(config) is False


def test_profiling_consumes_only_its_boolean_preserving_other_extension_keys():
    config = validate_server({"debug": {"profiling": True, "labels": ["cpu"]}})
    assert profiling_enabled(config) is True
    assert config["debug"] == {"profiling": True, "labels": ["cpu"]}


def test_debug_default_off():
    assert validate_server({})["debug"]["profiling"] is False


async def test_off_and_missing_library_keep_host_usable(tmp_path, authenticated_client, monkeypatch):
    app = await host(tmp_path, False)
    client = await authenticated_client(app)
    status = await (await client.post("/api/actions", json={"action": "profiling.status"})).json()
    assert not status["result"]["debugEnabled"]
    assert not status["result"]["available"]
    response = await client.post("/api/actions", json={
        "action": "profiling.start", "args": {"target": "anything"}, "id": "off"})
    assert response.status == 403
    config = load_server_config(tmp_path)
    config["debug"]["profiling"] = True
    save_server_config(tmp_path, config)
    def missing(name):
        raise ImportError("absent")
    monkeypatch.setattr("amplifier_web.profiling_host.importlib.import_module", missing)
    status = await (await client.post("/api/actions", json={"action": "profiling.status"})).json()
    assert "not installed" in status["result"]["reason"]
    assert (await client.get("/api/state")).status == 200


async def test_real_http_capture_auth_retries_privacy_and_no_publication(tmp_path, authenticated_client, monkeypatch):
    app = await host(tmp_path)
    service = app["service"]
    client = await authenticated_client(app)
    original = Path(os.environ["AMPLIFIER_HOME"]) / "projects" / "synthetic" / "sessions" / "one" / "context-intelligence" / "events.jsonl"
    original.parent.mkdir(parents=True)
    original.write_text('{"event":"canonical sentinel"}\n')
    before = original.read_bytes(), original.stat().st_mtime_ns
    saved_revision = service._state["revision"]
    monkeypatch.setattr(service, "_publish", lambda: pytest.fail("Profiling published app state"))
    monkeypatch.setattr(service, "_save", lambda: pytest.fail("Profiling saved full app state"))
    monkeypatch.setattr(service, "browser_state", lambda *a, **kw: pytest.fail("Profiling projected catalog"))
    def no_record(*a, **kw):
        pytest.fail("Profiling recorded capture contents in canonical events")
    monkeypatch.setattr(service.diagnostics, "record", no_record)

    # Ordinary authenticated owner actions are supported; neither anonymous
    # requests nor foreign origins may sample or download.
    client.session.headers.pop("Authorization")
    response = await client.post("/api/actions", json={"action": "profiling.targets"})
    assert response.status in {401, 403}
    client.session.headers["Authorization"] = "Bearer " + app["control_token"]
    response = await client.post("/api/actions", headers={"Origin": "https://bad.example"},
                                 json={"action": "profiling.targets"})
    assert response.status == 403
    targets = await (await client.post("/api/actions", json={"action": "profiling.targets"})).json()
    target = targets["result"]["targets"][0]
    assert target["pid"] == os.getpid()
    assert targets["result"]["scope"] == "serving-host-process"
    payload = {"action": "profiling.start", "id": "once", "args": {
        "target": target["id"], "seconds": 0.4, "rateHz": 40}}
    started = await (await client.post("/api/actions", json=payload)).json()
    cid = started["result"]["id"]
    assert "state" not in started
    duplicate = await (await client.post("/api/actions", json=payload)).json()
    assert duplicate["duplicate"] and duplicate["result"]["id"] == cid
    bad = await client.post("/api/actions", json={**payload, "args": {**payload["args"], "seconds": 0.5}})
    assert bad.status == 409
    running_download = await client.get("/api/debug/profiles/" + cid)
    assert running_download.status == 409
    for _ in range(3):
        await asyncio.to_thread(profiling_cpu_fixture)
    for _ in range(30):
        summary = await (await client.post("/api/actions", json={
            "action": "profiling.read", "args": {"id": cid}})).json()
        if summary["result"]["state"] != "running":
            break
        await asyncio.sleep(0.02)
    assert summary["result"]["state"] == "completed"
    assert summary["result"]["processCpuSeconds"] > 0
    response = await client.get(summary["result"]["downloadUrl"])
    assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
    result = await response.json()
    assert any(frame["function"] == "profiling_cpu_fixture" for frame in result["frames"])
    assert "PROFILE_PRIVATE_LOCAL" not in json.dumps(result)
    client.session.headers.pop("Authorization")
    assert (await client.get("/api/debug/profiles/" + cid)).status in {401, 403}
    client.session.headers["Authorization"] = "Bearer " + app["control_token"]
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert service._state["revision"] == saved_revision
    # Undo spies before app teardown performs its legitimate final save.
    monkeypatch.undo()


async def test_revocation_shutdown_unknown_target_and_restart_receipt(tmp_path, authenticated_client):
    app = await host(tmp_path)
    client = await authenticated_client(app)
    service = app["service"]
    target = (await service.profiling_host.dispatch("profiling.targets", {}))["result"]["targets"][0]["id"]
    first = await service.dispatch("profiling.start", {"target": target, "seconds": 10},
                                   command_id="restart-once")
    cid = first["result"]["id"]
    config = load_server_config(tmp_path)
    config["debug"]["profiling"] = False
    save_server_config(tmp_path, config)
    assert not (await service.dispatch("profiling.status"))["result"]["debugEnabled"]
    result = await asyncio.to_thread(service.profiling_host.controller.wait, cid)
    assert result["state"] == "revoked"
    assert (await client.get("/api/debug/profiles/" + cid)).status == 403
    config["debug"]["profiling"] = True
    save_server_config(tmp_path, config)
    # A process-local restart target changes identity. Retry remains evidence,
    # never another profile. The same commands table is retained.
    previous_host = service.profiling_host
    await previous_host.close()
    service.instance_id = "new-instance"
    service.profiling_host = ProfilingHost(service, config)
    repeated = await service.dispatch("profiling.start", {"target": target, "seconds": 10},
                                      command_id="restart-once")
    assert repeated["duplicate"] and repeated["result"]["state"] == "unavailable_after_restart"
    assert service.profiling_host.controller.status()["activeCapture"] is None
    with pytest.raises(AppError, match="stale"):
        await service.dispatch("profiling.start", {"target": target}, command_id="stale")
    new_target = service.profiling_host.controller.targets()[0]["id"]
    last = await service.dispatch("profiling.start", {"target": new_target, "seconds": 60}, command_id="last")
    await service.profiling_host.close()
    assert service.profiling_host.controller.read(last["result"]["id"])["state"] == "closed"


@pytest.mark.parametrize("invalid", [None, "3: invalid\n", "debug: [broken]\n"])
async def test_revocation_without_followup_request(tmp_path, invalid):
    app = await host(tmp_path)
    service = app["service"]
    try:
        target = (await service.dispatch("profiling.targets"))["result"]["targets"][0]["id"]
        receipt = await service.dispatch("profiling.start", {"target": target, "seconds": 10}, command_id="revoke")
        path = tmp_path / "config" / "server.yaml"
        if invalid is None:
            path.unlink()
        else:
            path.write_text(invalid)
        result = await asyncio.to_thread(service.profiling_host.controller.wait, receipt["result"]["id"], 2)
        assert result["state"] == "revoked"
        prior = await service.dispatch("profiling.start", {"target": target, "seconds": 10}, command_id="revoke")
        assert prior["duplicate"] and prior["result"]["id"] == receipt["result"]["id"]
    finally:
        await service.close()


async def test_agent_bridge_does_not_flush_dirty_progress(tmp_path, monkeypatch):
    app = await host(tmp_path)
    service = app["service"]
    try:
        await service.dispatch("session.create", {"title": "caller"})
        sid = service._session()["id"]
        service._progress_dirty = True
        with monkeypatch.context() as m:
            m.setattr(service, "_publish", lambda: pytest.fail("profiling published"))
            m.setattr(service, "state_context", lambda: pytest.fail("profiling copied catalog"))
            m.setattr(service, "_flush_pending_progress", lambda: pytest.fail("profiling flushed progress"))
            result = await service.app_bridge("dispatch", {"action": "profiling.status"}, sid)
            assert result["result"]["debugEnabled"]
            assert "state" not in result
    finally:
        service._progress_dirty = False
        await service.close()


async def test_release_retention_without_restart(tmp_path):
    app = await host(tmp_path)
    service = app["service"]
    try:
        target = (await service.dispatch("profiling.targets"))["result"]["targets"][0]["id"]
        ids = []
        for i in range(4):
            receipt = await service.dispatch("profiling.start", {"target": target, "seconds": 0.1},
                                            command_id=f"capture-{i}")
            ids.append(receipt["result"]["id"])
            await asyncio.to_thread(service.profiling_host.controller.wait, ids[-1])
        with pytest.raises(AppError, match="retention"):
            await service.dispatch("profiling.start", {"target": target, "seconds": 0.1}, command_id="overflow")
        await service.dispatch("profiling.release", {"id": ids[0]}, command_id="release-one")
        repeated = await service.dispatch("profiling.release", {"id": ids[0]}, command_id="release-one")
        assert repeated["duplicate"]
        receipt = await service.dispatch("profiling.start", {"target": target, "seconds": 0.1}, command_id="later")
        assert receipt["result"]["state"] == "running"
    finally:
        await service.close()


async def test_download_rechecks_revocation_before_return(tmp_path, monkeypatch):
    app = await host(tmp_path)
    service = app["service"]
    try:
        target = (await service.dispatch("profiling.targets"))["result"]["targets"][0]["id"]
        item = await service.dispatch("profiling.start", {"target": target, "seconds": 0.1}, command_id="dl")
        cid = item["result"]["id"]
        controller = service.profiling_host.controller
        await asyncio.to_thread(controller.wait, cid)
        original = controller.read
        def revoke_during_read(identity):
            result = original(identity)
            config = load_server_config(tmp_path)
            config["debug"]["profiling"] = False
            save_server_config(tmp_path, config)
            return result
        monkeypatch.setattr(controller, "read", revoke_during_read)
        with pytest.raises(AppError, match="revoked"):
            await service.profiling_host.content(cid)
    finally:
        await service.close()


async def test_failed_receipt_settlement_reconciles_without_replay(tmp_path):
    import sqlite3
    app = await host(tmp_path)
    service = app["service"]
    original_db = service.db
    class FaultDatabase:
        def __init__(self):
            self.fail = True
        def execute(self, sql, *args):
            if sql.startswith("UPDATE commands SET receipt") and self.fail:
                self.fail = False
                raise sqlite3.OperationalError("injected receipt failure")
            return original_db.execute(sql, *args)
        def commit(self):
            return original_db.commit()
    try:
        target = (await service.dispatch("profiling.targets"))["result"]["targets"][0]["id"]
        service.db = FaultDatabase()
        payload = {"target": target, "seconds": 0.1}
        with pytest.raises(sqlite3.OperationalError):
            await service.dispatch("profiling.start", payload, command_id="commit-fault")
        retained = service.profiling_host.controller.status()["retainedCaptures"]
        retry = await service.dispatch("profiling.start", payload, command_id="commit-fault")
        assert retry["duplicate"] and retry["result"]["id"]
        assert service.profiling_host.controller.status()["retainedCaptures"] == retained == 1
        await asyncio.to_thread(service.profiling_host.controller.wait, retry["result"]["id"])
    finally:
        service.db = original_db
        await service.close()