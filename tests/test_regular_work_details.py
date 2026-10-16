"""REGULAR-DETAILS-01: ordinary Work must not lose canonical tool fields.

Authored fixture, not evidence of installed producer payloads: input_id,
generation_id and finalMessageRefs below are explicitly supplied by this test.
No model or tool runs. Only canonical event records contain input/result bodies;
real ExecutionEvents hooks, normalize_event and AppService ingest supply the
resident lifecycle rows. Do not repair this test by pre-enriching those rows.

At e4139b8f the resident HTTP route has no canonical-field resolver; the live
cases should fail for absent fields. Real service reopen also drops transient
liveObservation rows, so the cold cases may fail earlier at group retrieval.
These are regression expectations, not executed results or an approved resolver
design. No inferred historical turn association is being qualified here.
"""

import json
import time

import pytest

from amplifier_web.event_log_view import event_path
from amplifier_web.execution import ensure_turn
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.server import create_app
from test_service import Runtime


INPUT_ID = "regular-details-input"
GENERATION_ID = "regular-details-generation"
BROWSER_ID = "regular-details-browser"
DETAIL_URL = "/api/conversation/detail"
BODY_FIELDS = {"input", "output", "error", "_eventFields",
               "inputDetail", "outputDetail", "errorDetail"}


def _long_text(marker):
    # >16,000 code points, including a non-BMP character; tail-only checking
    # cannot substitute for the complete decoded equality assertion below.
    return marker + ":" + "漢字é𝄞" * 5000 + "[[" + marker + "-TAIL]]"


def _calls(later_calls):
    calls = [
        {"call": "state", "tool": "app_control",
         "input": {"operation": "get_state", "args": {"path": "/REGULAR-STATE-INPUT"}},
         "output": {"success": True, "output": {"marker": "REGULAR-STATE-OUTPUT"}}},
        {"call": "read", "tool": "read_file",
         "input": {"file_path": "/fixture/REGULAR-READ-INPUT.txt"},
         "output": {"success": True, "output": {"content": _long_text("REGULAR-READ-OUTPUT")}}},
        {"call": "shell", "tool": "bash",
         "input": {"command": "printf '%s' 'REGULAR-SHELL-INPUT'"},
         "output": {"success": True, "output": {
             "stdout": "REGULAR-SHELL-OUTPUT", "stderr": "", "returncode": 0}}},
        {"call": "canvas", "tool": "app_control",
         "input": {"operation": "dispatch", "args": {"action": "canvas.show", "args": {
             "kind": "text", "content": _long_text("REGULAR-CANVAS-INPUT")}}},
         "output": {"success": True, "output": {"id": "REGULAR-CANVAS-OUTPUT"}}},
    ]
    calls.extend({"call": f"later-{index}", "tool": "read_file",
                  "input": {"file_path": f"/fixture/later-input-{index}.txt"},
                  "output": {"success": True, "output": f"later-output-{index}"}}
                 for index in range(later_calls))
    return calls


async def _emit(service, sid, event):
    normalized = normalize_event(event, sid)
    assert normalized is not None
    await service.on_runtime_event(*normalized)


async def _seed(service, calls):
    await service.dispatch("session.create", {})
    session = service._session()
    sid = session["id"]
    # Seed accepted host-owned input facts, not an executing runtime command.
    user = service._message(session, "user", "Inspect four regular tool results.", inputId=INPUT_ID)
    ensure_turn(session, INPUT_ID)
    await _emit(service, sid, {"type": "input.delivered", "input_id": INPUT_ID})
    await _emit(service, sid, {"type": "generation.started", "generation_id": GENERATION_ID,
                               "input_ids": [INPUT_ID]})
    await _emit(service, sid, {"type": "assistant.message", "generation_id": GENERATION_ID,
        "input_ids": [INPUT_ID], "sequence": 1, "time": time.time(), "text": "Inspecting saved work."})
    interim = session["messages"][-1]
    group = INPUT_ID + "@" + interim["id"]

    emitted = []
    observer = ExecutionEvents(sid, emitted.append)
    observer.lifecycle({"type": "input.delivered", "input_id": INPUT_ID})
    path = event_path(session, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for call in calls:
            for name, fields in (("tool:pre", {"tool_input": call["input"]}),
                                 ("tool:post", {"result": call["output"]})):
                # This explicit input_id is fixture data, not a claim that the
                # installed hook producer records it in real conversations.
                data = {"session_id": sid, "input_id": INPUT_ID,
                        "tool_call_id": call["call"], "tool_name": call["tool"], **fields}
                observer.hook(sid, name, data)
                assert len(emitted) == 1
                envelope = emitted.pop()
                row = envelope["event"]
                assert row["liveObservation"] is True
                assert not BODY_FIELDS.intersection(row)
                assert row["turnId"] == INPUT_ID
                assert row["sessionId"] == row["rootSessionId"] == sid
                stamp = row["startedAt"] if name == "tool:pre" else row["endedAt"]
                stream.write(json.dumps({"event": name, "timestamp": stamp,
                    "session_id": sid, "data": data}, ensure_ascii=False) + "\n")
                stream.flush()
                kind, payload = normalize_event(envelope, sid)
                assert kind == "execution.event" and not BODY_FIELDS.intersection(payload)
                await service.on_runtime_event(kind, payload)

    await _emit(service, sid, {"type": "assistant.message", "generation_id": GENERATION_ID,
        "input_ids": [INPUT_ID], "sequence": 2, "time": time.time(), "text": "Inspection complete."})
    final = session["messages"][-1]
    await _emit(service, sid, {"type": "generation.finished", "generation_id": GENERATION_ID,
        "input_ids": [INPUT_ID], "active_job_ids": [], "text": final["text"],
        "disposition": "manager_turn_finished",
        "finalMessageRefs": {"version": 1, "generationId": GENERATION_ID, "sequences": [2]}})
    await _emit(service, sid, {"type": "session.idle"})
    rows = session["execution"]["nodes"]
    assert len(rows) == len(calls) == len({row["id"] for row in rows})
    assert {row["toolCallId"] for row in rows} == {call["call"] for call in calls}
    assert all(row["liveObservation"] and row["phase"] == "completed" for row in rows)
    return sid, group, user["id"], interim["id"], final["id"]


async def _get(client, query):
    response = await client.get(DETAIL_URL, params=query)
    assert response.status == 200, f"REGULAR-DETAILS-01 {query}: {await response.text()}"
    return await response.json()


async def _all_nodes(client, sid, group, count):
    rows, before, seen = [], None, set()
    for page_number in range(8):
        query = {"sessionId": sid, "part": "nodes", "group": group}
        if before is not None:
            query["before"] = before
        page = await _get(client, query)
        assert page["group"] == group and len(page["items"]) <= 100
        if page_number == 0 and count > 100:
            assert page["before"], "Earlier calls must not disappear behind the tail window"
        rows[0:0] = page["items"]
        before = page["before"]
        if before is None:
            break
        assert before not in seen, "Work pagination did not advance"
        seen.add(before)
    else:
        pytest.fail("REGULAR-DETAILS-01 exceeded eight bounded Work pages")
    assert len(rows) == count, "Ordinary Work lost calls or duplicated pre/post observations"
    assert len({row["id"] for row in rows}) == count
    assert len({row["toolCallId"] for row in rows}) == count
    return rows


async def _field(client, sid, node, field, expected):
    reference = node.get(field + "Detail")
    if reference is None:
        assert field in node, (
            f"REGULAR-DETAILS-01: {node['toolCallId']} has neither {field} nor an owned detail reference")
        value = node[field]
    else:
        assert isinstance(reference, dict)
        assert reference["sessionId"] == sid
        assert isinstance(reference["id"], str) and reference["id"]
        assert reference["part"] == "nodes" and reference["field"] == field
        assert isinstance(reference["digest"], str) and reference["digest"]
        chunks, offset = [], 0
        for _ in range(64):
            chunk = await _get(client, {**reference, "offset": offset, "limit": 4096})
            assert isinstance(chunk["value"], str)
            chunks.append(chunk["value"])
            following = chunk["nextOffset"]
            if following is None:
                break
            assert following == offset + len(chunk["value"]) and following > offset
            offset = following
        else:
            pytest.fail("REGULAR-DETAILS-01 field pagination failed to terminate")
        value = "".join(chunks)
    # Permit exact structured fields or their JSON display encoding. A preview
    # without a functioning reference fails; Unicode and trailing sentinels must
    # survive in full, not merely appear in the first chunk.
    if isinstance(value, str):
        value = json.loads(value)
    assert value == expected, f"REGULAR-DETAILS-01 wrong/truncated {node['toolCallId']} {field}"


@pytest.mark.parametrize("later_calls", [0, 140], ids=["recent", "behind-tail"])
@pytest.mark.parametrize("reopen", [False, True], ids=["observer", "service-reopen"])
async def test_regular_work_details_observer_and_service_reopen(
        authenticated_client, tmp_path, later_calls, reopen):
    """Exact input/output remains attached to its successful ordinary call."""
    home = tmp_path / "regular-detail-app"
    app = await create_app(home, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    try:
        service = app["service"]
        calls = _calls(later_calls)
        sid, group, user_id, interim_id, final_id = await _seed(service, calls)
        if reopen:
            # Actual HTTP/server cleanup closes AppService and its stores. The
            # next instance must reload those stores, not a copied execution dict
            # or a direct call to stored_execution(). No observer rows are replayed.
            old_service = service
            await client.close()
            assert old_service.closed
            app = await create_app(home, workspace=tmp_path, runtime=Runtime(),
                                   voice=False, background_updates=False, preload_providers=False)
            service = app["service"]
            client = await authenticated_client(app)
            assert service is not old_service
            restored = service._session(sid)
            assert not any(row.get("liveObservation")
                           for row in restored.get("execution", {}).get("nodes", []))

        service.clients.attach(BROWSER_ID, kind="web")
        assert service.clients.records[BROWSER_ID]["kind"] == "web"
        client.session.headers["X-Amplifier-Client"] = BROWSER_ID
        session = service._session(sid)
        assert session["status"] == "idle"
        turn = next(row for row in session["execution"]["turns"] if row["id"] == INPUT_ID)
        assert turn["phase"] == "completed" and turn["anchorMessageId"] == user_id
        messages = (await _get(client, {"sessionId": sid, "part": "messages"}))["items"]
        by_message = {row["id"]: row for row in messages}
        for identity, phase in ((interim_id, "interim"), (final_id, "final")):
            response = by_message[identity]["response"]
            assert response["status"] == "completed" and response["phase"] == phase
            assert response["generationId"] == GENERATION_ID
            assert response["inputIds"] == [INPUT_ID] and response["anchorMessageId"] == user_id
            assert response["finalMessageIds"] == [final_id]

        rows = await _all_nodes(client, sid, group, len(calls))
        by_call = {row["toolCallId"]: row for row in rows}
        for call in calls:
            row = by_call[call["call"]]
            assert row["kind"] == "tool" and row["label"] == call["tool"]
            assert row["phase"] == "completed" and row["endedAt"] >= row["startedAt"]
            assert row["sessionId"] == row["rootSessionId"] == sid
            assert row["turnId"] == INPUT_ID and row["anchorMessageId"] == interim_id
            assert row.get("parentId") is None and not row.get("delegate")
            for field in ("input", "output"):
                await _field(client, sid, row, field, call[field])

        synced = await client.post("/api/conversation/work-sync", json={
            "sessionId": sid, "group": group,
            "known": {row["id"]: row["detailVersion"] for row in rows}})
        assert synced.status == 200, await synced.text()
        result = await synced.json()
        assert result["order"] == [row["id"] for row in rows]
        assert len(set(result["order"])) == len(calls)
        # Retained early-page field receipts must remain usable after sync.
        await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        await _field(client, sid, by_call["canvas"], "input", calls[3]["input"])
        assert not service.runtime.sent and not service.runtime.started
    finally:
        await client.close()
