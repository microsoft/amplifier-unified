"""Real native turns and offline provider observations for the model-change divider."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path


async def exercise(root, sid, session, runtime, action, workers, report):
    calls = root / "calls.jsonl"

    def read_calls():
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def turns():
        return session.get("execution", {}).get("turns", [])

    def markers():
        return {turn["inputId"]: turn["modelChange"] for turn in turns() if turn.get("modelChange")}

    async def configure(command_id, **settings):
        count, starts, before = len(read_calls()), len(workers), deepcopy(markers())
        await action("conversation.configure", {
            "sessionId": sid, "expectedRevision": session.get("nextMessageRevision", 0), **settings,
        }, command_id)
        assert (len(read_calls()), len(workers), markers()) == (count, starts, before)

    async def send(input_id, expected):
        before = len(read_calls())
        await action("conversation.send", {
            "sessionId": sid, "text": input_id,
            "expectedConfigurationRevision": session.get("nextMessageRevision", 0),
        }, input_id)
        async with asyncio.timeout(45):
            while not any(t.get("inputId") == input_id and t.get("phase") in {
                "completed", "failed", "interrupted", "cancelled"} for t in turns()):
                await asyncio.sleep(.03)
        turn = next(t for t in turns() if t["inputId"] == input_id)
        assert turn["phase"] == "completed", (turn, session.get("failure"))
        observed = read_calls()
        assert len(observed) == before + 1, observed
        received = observed[-1]
        module = "openai" if expected["instance"] == "fixture" else "anthropic"
        assert Path(received["implementation"]).parent.name == "amplifier_module_provider_" + module
        assert (received["model"], received["effort"]) == (expected["model"], expected["effort"]), received
        async with asyncio.timeout(20):
            while not runtime.workers.get(sid, {}).get("idle"):
                await asyncio.sleep(.03)

    first = {"instance": "fixture", "model": "first", "effort": "low"}
    second = {**first, "model": "second"}
    alternate = {**second, "instance": "alternate"}
    high = {**alternate, "effort": "high"}
    await configure("save-first", selection=first)
    await send("baseline-turn", first)
    await configure("browse-second", selection=second)
    await configure("back-to-first", selection=first)
    await send("unchanged-after-picker", first)
    await configure("switch-model", selection=second)
    await send("changed-model", second)
    await configure("switch-connection", selection=alternate)
    await send("changed-connection", alternate)
    await configure("change-effort", selection=high)
    await send("changed-effort", high)
    previous = session["previousTurnSettings"]
    assert previous["inputId"] == "changed-effort" and previous["selection"] == high, previous
    assert previous["bundle"] == str(root / "bundle-a.yaml"), previous
    await configure("pending-other-connection", selection=second, bundle=str(root / "bundle-b.yaml"))
    await configure("restore-last-turn", restorePreviousInputId=previous["inputId"],
                    restorePreviousGenerationId=previous["generationId"])
    assert session["bundle"] == previous["bundle"]
    await send("after-restore", high)
    expected = {"changed-model": (first, second), "changed-connection": (second, alternate),
                "changed-effort": (alternate, high)}
    assert len([t for t in turns() if t.get("modelChange")]) == len(expected)
    observed_markers = markers()
    assert observed_markers.keys() == expected.keys(), observed_markers
    for identity, (old, new) in expected.items():
        assert (observed_markers[identity]["previous"], observed_markers[identity]["current"]) == (old, new)
    assert observed_markers["changed-model"].get("impact") == "cold-request"
    assert observed_markers["changed-connection"].get("impact") == "cold-request"
    assert "impact" not in observed_markers["changed-effort"]
    assert session["nextMessageImpact"]["inputTokens"] == 64_000
    assert len(read_calls()) == len(turns()) == 6
    report.update(passed=True, sessionId=sid, turns=[t["inputId"] for t in turns()],
                  modelChanges=observed_markers, calls=read_calls(),
                  zeroCallsOnConfigureAndRestore=True)


async def reopen(root):
    """Serve the browser from a fresh process over the verified native chat's disk state."""
    import signal

    from aiohttp.test_utils import TestClient, TestServer
    from amplifier_web.auth import SESSION_COOKIE, new_session
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.server import create_app

    report = json.loads((root / "result.json").read_text())
    assert report["passed"] and report["cleanupConfirmed"], "A completed native run is required"
    sid = report["sessionId"]
    calls = root / "calls.jsonl"
    before = calls.read_bytes()
    home = root / "app"
    runtime = RuntimeManager(retention={"prewarm_on_select": False})
    app = await create_app(home, workspace=root / "workspace", runtime=runtime, voice=False,
                           background_updates=False, preload_providers=False)
    runtime.app_bridge, runtime.home = app["service"].app_bridge, home
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        url = str(client.make_url("/")).rstrip("/")
        app["allowed_origins"] = app["allowed_origins"] | {url}
        cookie = new_session(app["session_secret"])
        response = await client.get("/api/state", params={"sessionId": sid},
                                    headers={"Cookie": SESSION_COOKIE + "=" + cookie})
        assert response.status == 200
        state = await response.json()
        session = next(row for row in state["sessions"] if row["id"] == sid)
        turns = session["execution"]["turns"]
        assert [turn["inputId"] for turn in turns] == report["turns"]
        assert {turn["inputId"]: turn["modelChange"] for turn in turns
                if turn.get("modelChange")} == report["modelChanges"]
        assert calls.read_bytes() == before and sid not in runtime.workers
        stop = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
        # The browser launcher reads this private pipe and disposes of its local cookie.
        print(json.dumps({"url": url, "sessionId": sid,
                          "cookieName": SESSION_COOKIE, "cookieValue": cookie}), flush=True)
        await stop.wait()
        assert calls.read_bytes() == before
