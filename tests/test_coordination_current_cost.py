"""Bound current receipt lookup before decoding retained request bodies."""
import json
import time

import pytest

from test_coordination import app  # noqa: F401 -- actual host/index fixture


def request(identity, sender, target, **changes):
    return {"requestId": identity, "commandAction": "coordination.send",
            "senderSessionId": sender, "target": {"sessionId": target},
            "delivery": "accepted", "acceptedAt": 0, **changes}


def insert(db, rows):
    db.executemany("INSERT INTO commands(id,fingerprint,receipt) VALUES(?,?,?)",
                   ((row["requestId"], row["requestId"], json.dumps(row)) for row in rows))


@pytest.mark.parametrize("retained_kind", ["unrelated", "outgoing", "incoming"])
async def test_current_receipt_work_is_bounded_by_the_page(app, retained_kind):
    sid, peer = (row["id"] for row in app.state["sessions"])
    sender = sid if retained_kind == "outgoing" else "elsewhere"
    target = sid if retained_kind == "incoming" else "another-peer"
    measurements = []
    for count in (50, 5000):
        app.db.execute("DELETE FROM commands")
        insert(app.db, (request(f"old-{i}", sender, target, text="retained" * 256)
                        for i in range(count)))
        recent = [request(f"recent-{i}", sid if i % 2 else peer,
                          peer if i % 2 else sid) for i in range(48)]
        insert(app.db, recent)
        app.db.commit()
        steps = 0

        def progress():
            nonlocal steps
            steps += 100
            return 0

        app.db.set_progress_handler(progress, 100)
        start = time.thread_time_ns()
        try:
            actual = app.collaboration.current(sid)["requests"]
        finally:
            app.db.set_progress_handler(None, 0)
        measurements.append({"retained": count, "vm": steps,
                             "cpuMs": (time.thread_time_ns() - start) / 1e6})
        assert actual == list(reversed(recent))[:32]
    print(json.dumps({"currentReceipts": retained_kind, "measurements": measurements}))
    # A fixed current page must not parse/filter every retained peer request,
    # including older requests involving this same conversation. Count actual
    # SQLite work, not just the 32 returned/decoded rows or elapsed time.
    assert measurements[1]["vm"] < 5000
    assert measurements[1]["vm"] <= measurements[0]["vm"] + 500


async def test_current_receipts_keep_order_deduplication_and_saved_bodies(app):
    sid, peer = (row["id"] for row in app.state["sessions"])
    rows = [request(f"request-{i}", sid if i % 2 else peer,
                    sid if i % 3 else peer, acceptedAt=1000-i,
                    text=f"Saved body {i}: \u2603", detail={"nested": [i]})
            for i in range(90)]
    # Sender-only and target-only records are still selected. A same-owner
    # request appears once, including at a page boundary; dates do not reorder.
    rows[-1].pop("target")
    rows[-2].pop("senderSessionId")
    rows[-3].update(senderSessionId=sid, target={"sessionId": sid},
                    cancellation={"status": "requested", "revision": 2})
    rows[-4]["commandAction"] = "unrelated.action"
    insert(app.db, rows)
    expected = [row for row in reversed(rows)
                if row["commandAction"] == "coordination.send"
                and (row.get("senderSessionId") == sid
                     or row.get("target", {}).get("sessionId") == sid)][:32]
    actual = app.collaboration.current(sid)["requests"]
    assert actual == expected
    assert len({row["requestId"] for row in actual}) == 32
    actual[0]["detail"]["nested"].append("borrowed edit")
    assert app.collaboration.current(sid)["requests"] == expected


async def test_current_receipts_observe_updates_and_transaction_rollback(app):
    sid, peer = (row["id"] for row in app.state["sessions"])
    original = request("original", peer, sid, cancellation={"status": "requested"})
    insert(app.db, [original])
    app.db.commit()
    app.db.execute("SAVEPOINT receipt_change")
    changed = {**original, "delivery": "held", "text": "not admitted"}
    app.collaboration.save("original", changed)
    insert(app.db, [request("new", sid, peer)])
    value = app.collaboration.current(sid)["requests"]
    assert [row["requestId"] for row in value] == ["new", "original"]
    assert value[1]["cancellation"]["status"] == "withdrawn"
    # The read projection never rewrites the historical cancellation receipt.
    assert app.collaboration.receipt("original")["cancellation"] == {"status": "requested"}
    app.db.execute("ROLLBACK TO receipt_change")
    app.db.execute("RELEASE receipt_change")
    assert app.collaboration.current(sid)["requests"] == [original]
    app.db.execute("DELETE FROM commands WHERE id='original'")
    assert app.collaboration.current(sid)["requests"] == []
