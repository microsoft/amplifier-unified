"""REGULAR-DETAILS-01: ordinary Work must not lose canonical tool fields.

Authored fixture, not evidence of installed producer payloads: input_id,
generation_id and finalMessageRefs below are explicitly supplied by this test.
No model or tool runs. Only canonical event records contain input/result bodies;
real ExecutionEvents hooks, normalize_event and AppService ingest supply the
resident lifecycle rows. Do not repair this test by pre-enriching those rows.

Ordinary-work cases discover the current registered-web projection, not a group
ID carried across restart. Separate saved-observation controls read each authored
pre/post record and its body without pairing observations into logical calls.
Readable saved bodies do not establish ownership inside an interim group.
No inferred historical turn association or resolver architecture is qualified.
"""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager

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


async def _seed(service, calls, audit=None):
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
                encoded = json.dumps({"event": name, "timestamp": stamp,
                    "session_id": sid, "data": data}, ensure_ascii=False) + "\n"
                offset = stream.tell()
                stream.write(encoded)
                stream.flush()
                if audit is not None:
                    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                    audit["authored"][f"event:{sid}:{offset}:{digest[:16]}"] = {
                        "call": call["call"], "event": name, "path": str(path),
                        "offset": offset, "sha256": digest}
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
    return sid, user["id"], interim["id"], final["id"]


@asynccontextmanager
async def _scenario(authenticated_client, tmp_path, later_calls, reopen, audit=None):
    home = tmp_path / "regular-detail-app"
    app = await create_app(home, workspace=tmp_path, runtime=Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    stream = None
    patch = pytest.MonkeyPatch()
    try:
        service = app["service"]
        calls = _calls(later_calls)
        if audit is not None:
            from amplifier_web import session_files
            from amplifier_web.event_window import _source
            from amplifier_web.work_windows import source_identity
            audit.update(authored={}, diagnostic={}, sources=[])
            original_append = session_files.append_event
            call_ids = {call["call"] for call in calls}

            def observed_append(workspace, identity, event, data):
                receipt = original_append(workspace, identity, event, data)
                if data.get("tool_call_id") in call_ids and event in {"tool:pre", "tool:post"}:
                    assert len(audit["diagnostic"]) < 2 * len(calls), "Diagnostic receipt budget exceeded"
                    key = f"event:{identity}:{receipt['offset']}:{receipt['sha256'][:16]}"
                    audit["diagnostic"][key] = {
                        "call": data["tool_call_id"], "event": event,
                        "path": receipt["$event"], "kind": data.get("kind"),
                        "turnId": data.get("turnId"), "inputId": data.get("input_id"),
                        "inputPresent": "tool_input" in data or "input" in data,
                        "outputPresent": "result" in data}
                return receipt

            patch.setattr(session_files, "append_event", observed_append)

            def snapshot(label, owner):
                source = source_identity(owner)
                path, identity, _ = _source(source)
                audit["sources"].append({
                    "label": label, "descriptor": source, "path": str(path),
                    "identity": identity, "contextIntelligence":
                    "contextIntelligence" in owner.get("runtimeReport", {})})

        sid, user_id, interim_id, final_id = await _seed(service, calls, audit)
        if audit is not None:
            snapshot("seeded", service._session(sid, hydrate=False))
        if reopen:
            # Reload real stores, not a copied execution dict or replayed rows.
            old_service = service
            await client.close()
            assert old_service.closed
            assert not old_service.runtime.sent and not old_service.runtime.started
            if audit is not None:
                snapshot("closed", old_service._session(sid, hydrate=False))
            app = await create_app(home, workspace=tmp_path, runtime=Runtime(),
                                   voice=False, background_updates=False, preload_providers=False)
            service = app["service"]
            client = await authenticated_client(app)
            assert service is not old_service
            restored = service._session(sid)
            if audit is not None:
                snapshot("reopened", restored)
            assert not any(row.get("liveObservation")
                           for row in restored.get("execution", {}).get("nodes", []))

        service.clients.attach(BROWSER_ID, kind="web")
        assert service.clients.records[BROWSER_ID]["kind"] == "web"
        assert service.clients.records[BROWSER_ID]["selectedSessionId"] == sid
        client.session.headers["X-Amplifier-Client"] = BROWSER_ID
        # A connected browser keeps selected Work interest alive. Registration
        # alone is not a subscription: the reader otherwise correctly retires
        # its request fence while the physical page read is running.
        stream = await client.ws_connect("/api/events/ws?transport=delta-v1")
        initial = await stream.receive_json(timeout=5)
        assert initial["event"] == "state"
        yield service, client, calls, sid, user_id, interim_id, final_id
    finally:
        if stream is not None:
            await stream.close()
        await client.close()
        patch.undo()


async def _get(client, query):
    async with asyncio.timeout(30):
        rejected = []
        for attempt in range(3):
            response = await client.get(DETAIL_URL, params=query)
            body = await response.json()
            # Startup publications can invalidate a first physical read. The
            # diagnostic confirmed an unchanged owner/source with an advanced
            # fence. Reopen the selected view only for this explicit read
            # refusal, never retry a stale field receipt or mutate history.
            if (query.get("part") == "groups" and "before" not in query
                    and response.status == 400 and body.get("error") ==
                    "Invalid request: The requested conversation changed. Open it again."):
                rejected.append(body["error"])
                refreshed = await client.get("/api/state")
                assert refreshed.status == 200
                await refreshed.read()
                continue
            assert response.status == 200, f"REGULAR-DETAILS-01 {query}: {body}"
            if rejected:
                print("REGULAR-READ-REOPEN", {"attempt": attempt + 1, "refusals": rejected})
            return body
        pytest.fail(f"REGULAR-DETAILS-01 initial reader did not settle: {rejected}")


async def _current_group(client, sid, interim_id):
    # Await the real bounded saved-reader request before judging the projection;
    # no FakeProjector, background-read timing assumption or blind sleep.
    await _get(client, {"sessionId": sid, "part": "groups"})
    readiness = {}
    async with asyncio.timeout(30):
        # A sessionId query returns an explicit full session, bypassing the web
        # detail projection. Use this registered browser's selected view instead.
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            response = await client.get("/api/state")
            assert response.status == 200, await response.text()
            state = await response.json()
            assert state["selectedSessionId"] == sid
            projected = next(row for row in state["sessions"] if row["id"] == sid)
            readiness = projected.get("workProjection", {})
            if readiness.get("status") == "ready" and readiness.get("current") is True:
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.05)
    assert readiness.get("status") == "ready" and readiness.get("current") is True, readiness
    segments = projected.get("execution", {}).get("segments", [])
    candidates = [row for row in segments
                  if row.get("turnId") == INPUT_ID
                  and row.get("anchorMessageId") == interim_id
                  and not row.get("observationWindow")]
    ownership = [{key: row.get(key) for key in
                  ("id", "turnId", "anchorMessageId", "phase", "observationWindow")}
                 for row in segments]
    assert len(candidates) == 1, (
        "REGULAR-DETAILS-01 current-group ownership missing or ambiguous: "
        f"expected one group for input={INPUT_ID!r}, interim={interim_id!r}; "
        f"current projected groups={ownership!r}. "
        "Saved observations alone do not establish interim ownership.")
    group = candidates[0]
    assert group["phase"] == "completed"
    return group


async def _all_nodes(client, sid, group, count):
    rows, before, seen = [], None, set()
    for page_number in range(8):
        query = {"sessionId": sid, "part": "nodes", "group": group["id"],
                 "revision": group["detailRevision"]}
        if before is not None:
            query["before"] = before
        page = await _get(client, query)
        assert page["group"] == group["id"] and len(page["items"]) <= 100
        assert page["revision"] == group["detailRevision"]
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


async def _saved_observations(client, sid, count):
    """Page raw saved observations; never merge pre/post into logical calls."""
    rows, before, seen_cursors, seen_groups = [], None, set(), set()
    for page_number in range(8):
        query = {"sessionId": sid, "part": "groups"}
        if before is not None:
            query["before"] = before
        page = await _get(client, query)
        assert page["mode"] == "cursor" and page["total"] is None
        assert page["hasEarlier"] == (page["before"] is not None)
        assert not page["unavailableRanges"], page["unavailableRanges"]
        coverage = page["coverage"]
        assert not coverage["pagingBlocked"] and not coverage["partialRecord"], coverage
        assert coverage["invalidEvents"] == 0, coverage
        if page_number == 0 and count > 100:
            assert page["before"], "Saved observations must expose the earlier pages"
        evidence = page["evidence"]
        assert evidence["sessionId"] == sid and evidence["part"] == "observations"
        assert evidence["revision"] == page["revision"]
        assert evidence["group"] not in seen_groups, "Saved group pagination repeated a window"
        seen_groups.add(evidence["group"])
        nodes = await _get(client, evidence)
        assert nodes["group"] == evidence["group"]
        assert nodes["revision"] == evidence["revision"]
        assert nodes["sessionId"] == sid and nodes["evidence"] == evidence
        assert len(nodes["items"]) <= 100 and nodes["before"] is None
        assert nodes["order"] == [row["id"] for row in nodes["items"]]
        for row in nodes["items"]:
            assert row["observationWindow"] is True and row["observation"] is True
            assert row["association"] == "unverified"
            assert row["turnId"] == evidence["group"]
            assert row["sessionId"] == sid
            assert "_eventFields" not in row
            for field in ("input", "output", "error", "request", "response"):
                if field + "Detail" in row:
                    reference = row[field + "Detail"]
                    assert reference["windowKey"] == evidence["group"]
                    assert reference["sessionId"] == sid and reference["id"] == row["id"]
        rows.extend(nodes["items"])
        before = page["before"]
        if before is None:
            assert coverage["rootExhausted"] is True, coverage
            break
        assert before not in seen_cursors, "Saved group pagination did not advance"
        seen_cursors.add(before)
    else:
        pytest.fail("REGULAR-DETAILS-01 exceeded eight bounded saved-observation pages")
    assert len({row["id"] for row in rows}) == len(rows)
    # Call IDs occur in authored bodies AND metadata-only diagnostic receipts.
    # Callers select exact receipt IDs, allowing unrelated lifecycle records.
    return rows


async def _field(client, sid, node, field, expected):
    reference = node.get(field + "Detail")
    large = len(json.dumps(expected, ensure_ascii=False)) > 16000
    if large:
        assert reference is not None, (
            f"REGULAR-DETAILS-01 {node['toolCallId']} {field} must have a lazy reference")
        if field in node:
            assert isinstance(node[field], str) and len(node[field]) <= 4096, (
                "Large fields must be omitted or carry only a bounded preview")
    if reference is None:
        assert field in node, (
            f"REGULAR-DETAILS-01: {node['toolCallId']} has neither {field} nor an owned detail reference")
        value = node[field]
    else:
        assert isinstance(reference, dict)
        assert reference["sessionId"] == sid
        assert isinstance(reference["id"], str) and reference["id"] == node["id"]
        assert reference["part"] == "nodes" and reference["field"] == field
        assert isinstance(reference["digest"], str) and reference["digest"]
        chunks, offset = [], 0
        for _ in range(64):
            chunk = await _get(client, {**reference, "offset": offset, "limit": 4096})
            assert isinstance(chunk["value"], str)
            assert len(chunk["value"]) <= 4096
            chunks.append(chunk["value"])
            following = chunk["nextOffset"]
            if following is None:
                break
            assert following == offset + len(chunk["value"]) and following > offset
            offset = following
        else:
            pytest.fail("REGULAR-DETAILS-01 field pagination failed to terminate")
        if large:
            assert len(chunks) > 1, "Large fields must exercise continuation"
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
    async with _scenario(authenticated_client, tmp_path, later_calls, reopen) as fixture:
        service, client, calls, sid, user_id, interim_id, final_id = fixture
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

        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, len(calls))
        by_call = {row["toolCallId"]: row for row in rows}
        for call in calls:
            row = by_call[call["call"]]
            assert row["kind"] == "tool" and row["label"] == call["tool"]
            assert row["phase"] == "completed" and row["endedAt"] >= row["startedAt"]
            assert row["sessionId"] == row["rootSessionId"] == sid
            assert row["turnId"] == group["turnId"]
            assert row["anchorMessageId"] == group["anchorMessageId"]
            assert row.get("parentId") is None and not row.get("delegate")
            for field in ("input", "output"):
                await _field(client, sid, row, field, call[field])

        result = await _paged_sync(client, sid, group, rows)
        assert result["order"] == [row["id"] for row in rows]
        assert len(set(result["order"])) == len(calls)
        # Retained early-page field receipts must remain usable after sync.
        await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        await _field(client, sid, by_call["canvas"], "input", calls[3]["input"])
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.asyncio
@pytest.mark.parametrize("later_calls", [0, 140], ids=["complete-recent", "partial-behind-tail"])
async def test_live_resolver_proof_coverage_is_distinct_from_body_availability(
        authenticated_client, tmp_path, later_calls):
    """Association prerequisite, NOT acceptance of missing ordinary live fields.

    Keep the failing observer acceptance cases above. Exact raw bytes and
    resident ownership separately do not authorize a call-ID-only join.
    """
    from copy import deepcopy
    from amplifier_web.event_window import MAX_ITEMS, MAX_EVENTS, MAX_BYTES

    audit = {}
    async with _scenario(authenticated_client, tmp_path, later_calls, False, audit) as (
            service, client, calls, sid, user_id, interim_id, final_id):
        await service.diagnostics.flush()
        resident = service._session(sid, hydrate=False)["execution"]["nodes"]
        before = deepcopy(resident)
        assert len(before) == len(calls)
        assert all(not BODY_FIELDS.intersection(row) for row in before)
        page = await _get(client, {"sessionId": sid, "part": "groups"})
        windows = service.event_log_view.windows
        accepted = windows.pages[page["evidence"]["group"]]
        bound = accepted["_boundWork"]
        if later_calls == 0:
            assert accepted["bindingCoverage"]["status"] == "complete-root"
            assert accepted["coverage"]["rootExhausted"] and page["before"] is None
            assert len(bound) == 4
            for row in bound:
                expected_ids = {
                    identity for manifest in (audit["authored"], audit["diagnostic"])
                    for identity, record in manifest.items()
                    if record["call"] == row["toolCallId"]}
                assert len(expected_ids) == 4
                assert set(row["sourceOccurrenceIds"]) == expected_ids
                assert row["turnId"] == INPUT_ID and row["rootSessionId"] == sid
        else:
            assert page["before"] is not None
            assert accepted["bindingCoverage"]["status"] == "partial-source"
            assert not accepted["coverage"]["rootExhausted"]
            assert not bound  # No complete collision proof in this retained page.

        raw = await _saved_observations(client, sid, len(calls))
        by_id = {row["id"]: row for row in raw}
        assert len(by_id) == len(raw)
        assert len(audit["authored"]) == len(audit["diagnostic"]) == 2 * len(calls)
        assert set(audit["authored"]).isdisjoint(audit["diagnostic"])
        assert set(audit["authored"]) | set(audit["diagnostic"]) <= by_id.keys()
        if later_calls:
            assert len(audit["authored"]) + len(audit["diagnostic"]) > MAX_EVENTS
            # Explicit older paging makes bytes available; it does not turn an
            # older range's exhaustion into whole-source collision attestation.
            assert 1 < len(windows.pages) <= 8
            assert all(not value["_boundWork"] for value in windows.pages.values())
        for value in windows.pages.values():
            assert len(value["items"]) <= MAX_ITEMS
            assert value["coverage"]["scannedEvents"] <= MAX_EVENTS
            assert value["coverage"]["scannedBytes"] <= MAX_BYTES
        targets = {call["call"]: call for call in calls[:4]}
        for identity, receipt in audit["authored"].items():
            if receipt["call"] not in targets:
                continue
            row = by_id[identity]
            assert row["association"] == "unverified" and row["observationWindow"]
            field = "input" if receipt["event"] == "tool:pre" else "output"
            await _field(client, sid, row, field, targets[receipt["call"]][field])
        assert service._session(sid, hydrate=False)["execution"]["nodes"] == before
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.parametrize("later_calls", [0, 140], ids=["recent", "behind-tail"])
@pytest.mark.parametrize("reopen", [False, True], ids=["observer", "service-reopen"])
async def test_regular_saved_observation_bodies(
        authenticated_client, tmp_path, later_calls, reopen):
    """Saved-body availability is independent of interim-group ownership."""
    audit = {}
    async with _scenario(authenticated_client, tmp_path, later_calls, reopen, audit) as fixture:
        service, client, calls, sid, _, _, _ = fixture
        observations = {row["id"]: row for row in
                        await _saved_observations(client, sid, 2 * len(calls))}
        assert len(audit["authored"]) == 2 * len(calls)
        assert audit["authored"].keys() <= observations.keys(), "An authored record is not visible"
        rows = [observations[identity] for identity in audit["authored"]]
        expected = {(call["call"], event): (field, call[field], call["tool"])
                    for call in calls
                    for event, field in (("tool:pre", "input"), ("tool:post", "output"))}
        keys = [(row["toolCallId"], row["event"]) for row in rows]
        assert len(keys) == len(set(keys)) == len(expected), (
            "Each authored hook observation must appear exactly once")
        assert set(keys) == set(expected)
        for row, key in zip(rows, keys):
            assert row["inputId"] == INPUT_ID
            receipt = audit["authored"][row["id"]]
            assert key == (receipt["call"], receipt["event"])
            field, body, tool = expected[key]
            assert row["kind"] == "tool" and row["label"] == tool
            assert isinstance(row.get(field + "Detail"), dict), (
                "Saved bodies must be read through their returned owned reference")
            await _field(client, sid, row, field, body)
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.parametrize("later_calls", [0, 140], ids=["recent", "behind-tail"])
async def test_cold_observation_exact_origin_diagnostic(authenticated_client, tmp_path, later_calls):
    """Diagnostic only: distinguish authored bodies from fallback receipts.

    Ordinary ownership/body acceptance is separate from raw evidence access.
    No event file is reread: identities come from bytes this fixture wrote and
    transparent append receipts, compared with the supported bounded HTTP read.
    """
    audit = {}
    async with _scenario(authenticated_client, tmp_path, later_calls, True, audit) as fixture:
        service, client, calls, sid, _, _, _ = fixture
        observations = {row["id"]: row for row in
                        await _saved_observations(client, sid, 2 * len(calls))}

        authored = set(audit["authored"])
        diagnostic = set(audit["diagnostic"])
        print("REGULAR-EXACT-ORIGINS", {
            "authored": len(authored), "authoredVisible": len(authored & observations.keys()),
            "diagnostic": len(diagnostic), "diagnosticVisible": len(diagnostic & observations.keys()),
            "sources": audit["sources"]})
        assert len(authored) == 2 * len(calls)
        assert authored <= observations.keys(), "An authored record is not visible"
        assert len(diagnostic) == 2 * len(calls)
        assert diagnostic <= observations.keys(), "A fallback receipt is not visible"
        assert authored.isdisjoint(diagnostic)
        assert len({row["path"] for row in audit["sources"]}) == 1
        by_call = {call["call"]: call for call in calls}
        for identity in authored:
            row = observations[identity]
            expected = audit["authored"][identity]
            assert row["inputId"] == INPUT_ID
            assert (row["toolCallId"], row["event"]) == (expected["call"], expected["event"])
            field = "input" if row["event"] == "tool:pre" else "output"
            await _field(client, sid, row, field, by_call[expected["call"]][field])
        for identity in diagnostic:
            row = observations[identity]
            receipt = audit["diagnostic"][identity]
            assert receipt["kind"] == "tool" and receipt["turnId"] == INPUT_ID
            assert receipt["inputId"] is None
            assert not receipt["inputPresent"] and not receipt["outputPresent"]
            assert "inputId" not in row
            assert "inputDetail" not in row and "outputDetail" not in row
        assert not service.runtime.sent and not service.runtime.started


async def test_cold_ordinary_calls_keep_original_evidence(authenticated_client, tmp_path):
    """Four ordinary calls coexist with eight authored and eight diagnostic rows."""
    audit = {}
    async with _scenario(authenticated_client, tmp_path, 0, True, audit) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        group = await _current_group(client, sid, interim_id)
        ordinary = await _all_nodes(client, sid, group, 4)
        evidence = group["evidence"]
        assert evidence["sessionId"] == sid and evidence["part"] == "observations"
        assert evidence["revision"] == group["detailRevision"]
        assert evidence["group"] != group["id"]
        by_call = {call["call"]: call for call in calls}
        for row in ordinary:
            assert row["evidence"] == evidence
            assert row["turnId"] == INPUT_ID and row["anchorMessageId"] == interim_id
            assert row["observationWindow"] is False and row["phase"] == "completed"
            assert set(row["sourceOccurrenceIds"]) == {
                identity for manifest in (audit["authored"], audit["diagnostic"])
                for identity, receipt in manifest.items() if receipt["call"] == row["toolCallId"]}
            for field in ("input", "output"):
                await _field(client, sid, row, field, by_call[row["toolCallId"]][field])

        raw = await _get(client, evidence)
        assert raw["sessionId"] == sid and raw["group"] == evidence["group"]
        assert raw["revision"] == evidence["revision"] and raw["evidence"] == evidence
        assert raw["before"] is None and len(raw["items"]) <= 100
        assert raw["order"] == [row["id"] for row in raw["items"]]
        observations = {row["id"]: row for row in raw["items"]}
        assert len(observations) == len(raw["items"]) == raw["total"]
        authored, diagnostic = set(audit["authored"]), set(audit["diagnostic"])
        assert len(authored) == len(diagnostic) == 8 and authored.isdisjoint(diagnostic)
        assert authored | diagnostic <= observations.keys()
        for identity in authored:
            row = observations[identity]
            receipt = audit["authored"][identity]
            assert row["id"] == identity and row["inputId"] == INPUT_ID
            assert (row["toolCallId"], row["event"]) == (receipt["call"], receipt["event"])
            assert row["observationWindow"] is True and row["association"] == "unverified"
            assert row["turnId"] == evidence["group"] and "_eventFields" not in row
            field, absent = (("input", "output") if receipt["event"] == "tool:pre"
                             else ("output", "input"))
            assert absent not in row and absent + "Detail" not in row
            reference = row[field + "Detail"]
            assert reference["id"] == identity and reference["windowKey"] == evidence["group"]
            await _field(client, sid, row, field, by_call[receipt["call"]][field])
        for identity in diagnostic:
            row = observations[identity]
            receipt = audit["diagnostic"][identity]
            assert row["id"] == identity and row["sessionId"] == sid
            assert (row["toolCallId"], row["event"]) == (receipt["call"], receipt["event"])
            assert receipt["kind"] == "tool" and receipt["turnId"] == INPUT_ID
            assert receipt["inputId"] is None
            assert not receipt["inputPresent"] and not receipt["outputPresent"]
            assert "inputId" not in row and not BODY_FIELDS.intersection(row)
            assert row["observationWindow"] is True and row["association"] == "unverified"

        # Root identity and exact revision are required; never substitute a
        # merged subgroup, omit the revision, or silently accept an old one.
        for query in ({**evidence, "group": group["id"]},
                      {**evidence, "revision": "obsolete"},
                      {key: value for key, value in evidence.items() if key != "revision"}):
            rejected = await client.get(DETAIL_URL, params=query)
            assert rejected.status == 400, await rejected.text()
            await rejected.read()
        retained = await _all_nodes(client, sid, group, 4)
        assert [row["id"] for row in retained] == [row["id"] for row in ordinary]
        assert not service.runtime.sent and not service.runtime.started


async def _paged_sync(client, sid, group, rows):
    """Sync only admitted IDs, pinning every later chunk to the first result."""
    order, items, revision = [], [], None
    assert rows and len({row["id"] for row in rows}) == len(rows)
    async with asyncio.timeout(30):
        for start in range(0, len(rows), 100):
            chunk = rows[start:start + 100]
            known = {row["id"]: row["detailVersion"] for row in chunk}
            payload = {"sessionId": sid, "group": group["id"],
                       "paged": True, "known": known}
            if revision is not None:
                payload["revision"] = revision
            response = await client.post("/api/conversation/work-sync", json=payload)
            assert response.status == 200, await response.text()
            page = await response.json()
            assert page["group"] == group["id"]
            assert isinstance(page["revision"], str) and page["revision"]
            if revision is None:
                revision = page["revision"]
            assert page["revision"] == revision
            assert page["order"] == list(known), "Sync leaked, lost or reordered another admitted range"
            assert len(page["items"]) <= len(known) <= 100
            changed = [row["id"] for row in page["items"]]
            assert len(changed) == len(set(changed)) and set(changed) <= known.keys()
            assert changed == [identity for identity in known if identity in set(changed)]
            order.extend(page["order"])
            items.extend(page["items"])
    assert order == [row["id"] for row in rows]
    assert len(order) == len(set(order)) == len(rows)
    return {"group": group["id"], "revision": revision, "order": order, "items": items}


async def _selected_web_session(client, sid):
    response = await client.get("/api/state")
    assert response.status == 200, await response.text()
    state = await response.json()
    assert state["selectedSessionId"] == sid
    return next(row for row in state["sessions"] if row["id"] == sid)


async def _resident_group_without_detail_read(client, sid, interim_id):
    """Discover through public metadata, without privately priming a proof."""
    projected = await _selected_web_session(client, sid)
    candidates = [row for row in projected["execution"]["segments"]
                  if row.get("turnId") == INPUT_ID
                  and row.get("anchorMessageId") == interim_id
                  and not row.get("observationWindow")]
    assert len(candidates) == 1, candidates
    return candidates[0]


async def _refused_field(client, reference):
    async with asyncio.timeout(30):
        response = await client.get(DETAIL_URL, params=reference)
        body = await response.json()
    assert response.status == 400, body
    assert body.get("error") and "value" not in body and "nextOffset" not in body


class _HeldWindowModel:
    """Real WindowModel results; a one-shot gate AFTER computation, never a fake field.

    This isolates the host's post-await checks. It does not qualify subprocess
    transport. The outer HTTP route and all source/index readers remain real.
    """

    def __init__(self, home, operation):
        from amplifier_web.event_window_worker import WindowModel
        self.model = WindowModel(home)
        self.operation = operation
        self.operations = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.result = None
        self.used = False

    async def project(self, inputs, *, key):
        assert len(self.operations) < 64, "Consumer control exceeded its worker-call bound"
        self.operations.append(inputs["operation"])
        deadline = time.monotonic() + 20

        def check_budget():
            if time.monotonic() >= deadline:
                raise TimeoutError("Consumer control exceeded its real-reader deadline")

        result = await asyncio.to_thread(self.model.project, inputs, check_budget=check_budget)
        if inputs["operation"] == self.operation and not self.used:
            self.used = True
            self.result = result
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), 10)
        return result


@asynccontextmanager
async def _hold_model_result(service, operation):
    model = _HeldWindowModel(service.data_dir, operation)
    patch = pytest.MonkeyPatch()
    # Only the transport seam is replaced. No rows, references, proof facts or
    # WindowModel result bodies are manufactured by this test.
    patch.setattr(service.event_log_view.windows, "_worker", lambda: model)
    try:
        yield model
    finally:
        model.release.set()
        patch.undo()


@pytest.mark.parametrize("entry_route", ["groups", "nodes"])
async def test_public_work_read_starts_proof_without_enriching_resident_metadata(
        authenticated_client, tmp_path, entry_route):
    from copy import deepcopy
    async with _scenario(authenticated_client, tmp_path, 0, False) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        resident = service._session(sid, hydrate=False)["execution"]["nodes"]
        before = deepcopy(resident)
        assert len(before) == 4 and all(not BODY_FIELDS.intersection(row) for row in before)
        metadata = await _selected_web_session(client, sid)
        assert metadata.get("workProjection", {}).get("rootProof") is None

        if entry_route == "groups":
            group = await _current_group(client, sid, interim_id)
            rows = await _all_nodes(client, sid, group, 4)
        else:
            group = await _resident_group_without_detail_read(client, sid, interim_id)
            page = await _get(client, {"sessionId": sid, "part": "nodes", "group": group["id"]})
            assert page["group"] == group["id"] and page["before"] is None
            rows = page["items"]
            assert len(rows) == len({row["id"] for row in rows}) == 4
        assert [row["toolCallId"] for row in rows] == [call["call"] for call in calls]
        for row, call in zip(rows, calls):
            for field in ("input", "output"):
                assert isinstance(row.get(field + "Detail"), dict)
                await _field(client, sid, row, field, call[field])
        proof = (await _selected_web_session(client, sid))["workProjection"]["rootProof"]
        assert proof["status"] == "complete" and proof["boundCount"] == 4
        assert 1 <= proof["steps"] <= 8
        assert service._session(sid, hydrate=False)["execution"]["nodes"] == before
        assert all(not BODY_FIELDS.intersection(row) for row in resident)
        assert not service.runtime.sent and not service.runtime.started


async def test_field_sync_and_state_reads_do_not_start_missing_or_pending_proof(
        authenticated_client, tmp_path):
    async with _scenario(authenticated_client, tmp_path, 0, False) as fixture:
        service, client, _, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _resident_group_without_detail_read(client, sid, interim_id)
        node = service._session(sid, hydrate=False)["execution"]["nodes"][1]
        missing = {"sessionId": sid, "part": "nodes", "id": node["id"],
                   "field": "output", "digest": "not-admitted",
                   "windowKey": "resident-work:not-admitted"}
        windows = service.event_log_view.windows
        async with _hold_model_result(service, "proof-step") as model:
            async def passive_controls(expected_status):
                proof_before = windows._root_proofs.get(sid)
                steps_before = model.operations.count("proof-step")
                await _refused_field(client, missing)
                response = await client.post("/api/conversation/work-sync", json={
                    "sessionId": sid, "group": group["id"], "paged": True, "known": {}})
                assert response.status == 400, await response.text()
                await response.read()
                metadata = await _selected_web_session(client, sid)
                proof = metadata.get("workProjection", {}).get("rootProof")
                assert (proof.get("status") if proof else None) == expected_status
                assert windows._root_proofs.get(sid) is proof_before
                assert model.operations.count("proof-step") == steps_before
                assert "field" not in model.operations

            await passive_controls(None)
            waiters, both_waiting = [], asyncio.Event()
            await_proof = windows._await_proof

            async def observe_waiter(identity, task, record):
                # Observation only: both entry points still originate in HTTP.
                waiters.append(task)
                if len(waiters) == 2:
                    both_waiting.set()
                return await await_proof(identity, task, record)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(windows, "_await_proof", observe_waiter)
                opening = asyncio.create_task(_get(client, {"sessionId": sid, "part": "groups"}))
                joining = None
                try:
                    await asyncio.wait_for(model.entered.wait(), 5)
                    assert model.result["status"] == "complete"
                    assert windows._root_proofs[sid]["status"] == "pending"
                    await passive_controls("pending")
                    joining = asyncio.create_task(_get(client, {
                        "sessionId": sid, "part": "nodes", "group": group["id"]}))
                    await asyncio.wait_for(both_waiting.wait(), 5)
                    assert len(waiters) == 2 and waiters[0] is waiters[1]
                    assert not waiters[0].done()
                    model.release.set()
                    await asyncio.wait_for(asyncio.gather(opening, joining), 15)
                    assert model.operations.count("proof-step") == 1
                    proof = (await _selected_web_session(client, sid))["workProjection"]["rootProof"]
                    assert proof["status"] == "complete" and proof["boundCount"] == 4
                finally:
                    model.release.set()
                    for task in (opening, joining):
                        if task is not None and not task.done():
                            task.cancel()
                    await asyncio.gather(*(task for task in (opening, joining) if task is not None),
                                         return_exceptions=True)
        assert not service.runtime.sent and not service.runtime.started


async def test_forged_resident_lifecycle_never_inherits_canonical_body_references(
        authenticated_client, tmp_path):
    from copy import deepcopy
    async with _scenario(authenticated_client, tmp_path, 0, False) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, 4)
        by_call = {row["toolCallId"]: row for row in rows}
        await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        target = next(row for row in service._session(sid, hydrate=False)["execution"]["nodes"]
                      if row["toolCallId"] == "read")
        original = deepcopy(target)
        mutations = {
            "id": original["id"] + "-forged",
            "turnId": INPUT_ID + "-forged",
            "sessionId": sid + "-forged",
            "rootSessionId": sid + "-forged",
            "parentId": "forged-parent",
            "phase": "failed",
            "revision": original["revision"] + 1,
            "startedAt": original["startedAt"] + 0.001,
            "endedAt": original["endedAt"] + 0.001,
        }
        for name, value in mutations.items():
            # Only this synthetic resident lifecycle is altered. Canonical
            # records and the completed proof remain untouched.
            target[name] = value
            try:
                changed = await _get(client, {
                    "sessionId": sid, "part": "nodes", "group": group["id"]})
                candidates = [row for row in changed["items"] if row.get("toolCallId") == "read"]
                assert len(candidates) <= 1
                assert all(not BODY_FIELDS.intersection(row) for row in candidates), name
                await _refused_field(client, by_call["read"]["outputDetail"])
                unaffected = {row["toolCallId"]: row for row in changed["items"]
                              if row.get("toolCallId") != "read"}
                assert set(unaffected) == {"state", "shell", "canvas"}, name
                await _field(client, sid, unaffected["canvas"], "input", calls[3]["input"])
            finally:
                target.clear()
                target.update(deepcopy(original))
            restored = await _get(client, {
                "sessionId": sid, "part": "nodes", "group": group["id"]})
            by_call = {row["toolCallId"]: row for row in restored["items"]}
            await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        assert target == original and not BODY_FIELDS.intersection(target)
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.parametrize("reopen", [False, True], ids=["resident", "cold"])
async def test_public_ordinary_field_receipts_are_browser_owned(
        authenticated_client, tmp_path, reopen):
    async with _scenario(authenticated_client, tmp_path, 0, reopen) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, 4)
        original = next(row for row in rows if row["toolCallId"] == "read")
        await _field(client, sid, original, "output", calls[1]["output"])
        other_id = "regular-details-other-browser"
        service.clients.attach(other_id, kind="web")
        assert service.clients.records[other_id]["selectedSessionId"] == sid
        client.session.headers["X-Amplifier-Client"] = other_id
        stream = None
        try:
            stream = await client.ws_connect("/api/events/ws?transport=delta-v1")
            assert (await stream.receive_json(timeout=5))["event"] == "state"
            await _refused_field(client, original["outputDetail"])
            other = await _get(client, {
                "sessionId": sid, "part": "nodes", "group": group["id"],
                "revision": group["detailRevision"]})
            own_row = next(row for row in other["items"] if row["toolCallId"] == "read")
            await _field(client, sid, own_row, "output", calls[1]["output"])
            # Admitting the same node to another browser cannot redeem the
            # first browser's exact receipt.
            await _refused_field(client, original["outputDetail"])
        finally:
            client.session.headers["X-Amplifier-Client"] = BROWSER_ID
            if stream is not None:
                await stream.close()
        await _field(client, sid, original, "output", calls[1]["output"])
        await _refused_field(client, own_row["outputDetail"])
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.parametrize("reopen", [False, True], ids=["resident", "cold"])
async def test_public_144_node_pages_preserve_receipts_and_sync_exact_subsets(
        authenticated_client, tmp_path, reopen):
    async with _scenario(authenticated_client, tmp_path, 140, reopen) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, 144)
        assert [row["toolCallId"] for row in rows] == [call["call"] for call in calls]
        by_call = {row["toolCallId"]: row for row in rows}
        latest_query = {"sessionId": sid, "part": "nodes", "group": group["id"],
                        "revision": group["detailRevision"], "preserve": "true"}
        latest = await _get(client, latest_query)
        assert len(latest["items"]) == 100 and latest["before"] is None
        assert [row["id"] for row in latest["items"]] == [row["id"] for row in rows[-100:]]
        await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        await _field(client, sid, by_call["canvas"], "input", calls[3]["input"])
        over_limit = await client.post("/api/conversation/work-sync", json={
            "sessionId": sid, "group": group["id"], "paged": True,
            "known": {row["id"]: row["detailVersion"] for row in rows}})
        assert over_limit.status == 400, await over_limit.text()
        await over_limit.read()
        result = await _paged_sync(client, sid, group, rows)
        assert result["order"] == [row["id"] for row in rows]
        assert len(set(result["order"])) == 144
        # A later chunk cannot silently discard earlier admitted IDs.
        again = await _paged_sync(client, sid, group, rows[:44])
        assert again["order"] == [row["id"] for row in rows[:44]]
        await _field(client, sid, by_call["read"], "output", calls[1]["output"])
        await _field(client, sid, by_call["canvas"], "input", calls[3]["input"])

        # Acquire a fresh latest window for cursor refusal controls. preserve
        # above correctly retained the exhausted oldest cursor of all144 calls.
        fresh = await _get(client, {key: value for key, value in latest_query.items()
                                   if key != "preserve"})
        assert fresh["before"]
        cursor_query = {**latest_query, "before": fresh["before"]}
        cursor = fresh["before"]
        forged_cursor = ("A" if cursor[0] != "A" else "B") + cursor[1:]
        for invalid in ({**cursor_query, "before": forged_cursor},
                        {**cursor_query, "group": group["id"] + "-other"},
                        {**cursor_query, "revision": "obsolete"}):
            response = await client.get(DETAIL_URL, params=invalid)
            assert response.status == 400, await response.text()
            await response.read()
        service.clients.attach("regular-cursor-other-browser", kind="web")
        client.session.headers["X-Amplifier-Client"] = "regular-cursor-other-browser"
        try:
            response = await client.get(DETAIL_URL, params=cursor_query)
            assert response.status == 400, await response.text()
            await response.read()
        finally:
            client.session.headers["X-Amplifier-Client"] = BROWSER_ID
        retained = await _get(client, cursor_query)
        assert [row["id"] for row in retained["items"]] == [row["id"] for row in rows[:44]]
        assert retained["before"] is None
        # A valid token cannot survive a different canonical source owner.
        # Change only the synthetic owner descriptor, not its proof or cursor.
        owner = service._session(sid, hydrate=False)
        had_identity = "nativeIdentity" in owner
        prior_identity = owner.get("nativeIdentity")
        owner["nativeIdentity"] = "regular-rebound-owner"
        try:
            response = await client.get(DETAIL_URL, params=cursor_query)
            assert response.status == 400, await response.text()
            await response.read()
        finally:
            if had_identity:
                owner["nativeIdentity"] = prior_identity
            else:
                owner.pop("nativeIdentity", None)
        assert not service.runtime.sent and not service.runtime.started


async def test_stale_proof_sync_removes_refs_with_new_versions_without_restarting_proof(
        authenticated_client, tmp_path):
    from copy import deepcopy
    async with _scenario(authenticated_client, tmp_path, 0, False) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, 4)
        original = next(row for row in rows if row["toolCallId"] == "read")
        await _field(client, sid, original, "output", calls[1]["output"])
        resident = deepcopy(service._session(sid, hydrate=False)["execution"]["nodes"])
        windows = service.event_log_view.windows
        proof_id = windows._root_proofs[sid]["id"]
        windows.invalidate({sid})  # Negative dependency change, never proof creation.
        async with _hold_model_result(service, None) as model:
            await _refused_field(client, original["outputDetail"])
            result = await _paged_sync(client, sid, group, rows)
            assert result["order"] == [row["id"] for row in rows]
            assert [row["id"] for row in result["items"]] == result["order"]
            old = {row["id"]: row for row in rows}
            for row in result["items"]:
                assert not BODY_FIELDS.intersection(row)
                assert row["detailVersion"] != old[row["id"]]["detailVersion"]
            proof = (await _selected_web_session(client, sid))["workProjection"]["rootProof"]
            assert proof["id"] == proof_id and proof["status"] == "stale"
            assert "proof-step" not in model.operations and "field" not in model.operations
        assert service._session(sid, hydrate=False)["execution"]["nodes"] == resident
        assert not service.runtime.sent and not service.runtime.started


@pytest.mark.parametrize("mutation", [
    "source", "index", "epoch", "entry-eviction", "proof-eviction", "resident-identity",
])
async def test_public_field_refuses_dependency_changes_after_real_model_await(
        authenticated_client, tmp_path, mutation):
    async with _scenario(authenticated_client, tmp_path, 0, False) as fixture:
        service, client, calls, sid, _, interim_id, _ = fixture
        await service.diagnostics.flush()
        group = await _current_group(client, sid, interim_id)
        rows = await _all_nodes(client, sid, group, 4)
        original = next(row for row in rows if row["toolCallId"] == "read")
        await _field(client, sid, original, "output", calls[1]["output"])
        windows = service.event_log_view.windows
        session = service._session(sid, hydrate=False)
        reference = {**original["outputDetail"], "offset": 0, "limit": 4096}
        async with _hold_model_result(service, "field") as model:
            task = asyncio.create_task(_refused_field(client, reference))
            try:
                await asyncio.wait_for(model.entered.wait(), 5)
                assert model.result["status"] == "complete"
                # These are real canonical bytes, computed before invalidation.
                value = model.result["field"]["text"]
                assert value and len(value) <= 4096
                assert "REGULAR-READ-OUTPUT" in value
                if mutation == "source":
                    with event_path(session, sid).open("a", encoding="utf-8") as stream:
                        stream.write("{}\n")
                elif mutation == "index":
                    with service.diagnostics._db() as db:
                        db.execute(
                            "INSERT INTO records(id,at,stream,session,workspace,event,data) "
                            "VALUES(?,0,'tools',?,?,'tool:post','{}')",
                            ("regular-held-field-index-change", sid, str(tmp_path)))
                elif mutation == "epoch":
                    windows.invalidate({sid})
                elif mutation == "entry-eviction":
                    removed = windows.live_pages.groups.pop((BROWSER_ID, sid, group["id"]))
                    assert original["id"] in removed["ids"]
                elif mutation == "proof-eviction":
                    support = windows._root_proofs[sid]["pages"][0][0]
                    assert windows.pages.pop(support)
                else:
                    target = next(row for row in session["execution"]["nodes"]
                                  if row["id"] == original["id"])
                    target["parentId"] = "changed-after-field-await"
                model.release.set()
                await asyncio.wait_for(task, 5)
                assert model.operations.count("field") == 1
                assert "proof-step" not in model.operations
            finally:
                model.release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not service.runtime.sent and not service.runtime.started
