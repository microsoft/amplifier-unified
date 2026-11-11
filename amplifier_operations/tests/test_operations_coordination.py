"""Bounded delivery cursors and change notification without a host."""
import pytest

from amplifier_operations.coordination import ChangeSignal, delivery


def test_cursor_rejects_other_identity_marks_rewrites_and_can_acknowledge_expired_history():
    source = {"identity": "one", "results": [{"id": "a", "sequence": 1, "text": "old"}]}
    cursor = delivery(source)["nextCursor"]
    with pytest.raises(ValueError, match="another target"):
        delivery({**source, "identity": "two"}, cursor)
    rewritten = delivery({**source, "results": [{"id": "b", "sequence": 1, "text": "new"}]}, cursor)
    assert rewritten["cursorGap"]
    expired = {"identity": "one", "results": [], "latestSequence": 50}
    gap = delivery(expired, cursor)
    assert gap["cursorGap"] and not gap["hasMore"]
    assert not delivery(expired, gap["nextCursor"])["changed"]


def test_pages_bound_utf8_and_acknowledge_only_delivered_reports():
    rows = [{"id": f"r{i}", "sequence": i, "text": "🌱" * 300} for i in range(5, 9)]
    source = {"identity": "child", "results": rows, "signal": {"status": "completed"}}
    first = delivery(source, max_bytes=512)
    assert first["cursorGap"] and first["hasMore"]
    assert len(first["results"][0]["text"].encode()) == 512
    assert first["results"][0]["textTruncated"]
    second = delivery(source, first["nextCursor"], max_bytes=4096)
    assert [r["id"] for r in second["results"]] == ["r6", "r7", "r8"]
    assert not second["hasMore"]
    assert not delivery(source, second["nextCursor"])["changed"]


async def test_notifier_has_no_read_wait_lost_wakeup():
    signal = ChangeSignal()
    reads = 0
    def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            signal.notify()
        return {"changed": reads == 2}
    result = await signal.wait(read, wait_ms=100)
    assert result["changed"] and not result["timedOut"] and reads == 2
