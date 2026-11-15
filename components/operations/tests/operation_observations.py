"""Synthetic events for the standalone package tests; no host fixture dependency."""

def event(identity="p1", sequence=1, phase="started", **data):
    return {
        "schemaVersion": 1,
        "eventId": f"{identity}:{sequence}",
        "sequence": sequence,
        "operationId": identity,
        "ownerId": "mount1",
        "source": "tool-bash",
        "kind": "process",
        "phase": phase,
        "at": 1,
        **data,
    }


def output(identity="p1", sequence=2, cursor=0, text="hello\n"):
    return event(
        identity,
        sequence,
        "output",
        chunk={
            "cursor": cursor,
            "next_cursor": cursor + 1,
            "stream": "stdout",
            "text": text,
            "source_bytes": len(text.encode()),
            "binary_output_withheld": False,
        },
    )


def final(identity="p1", sequence=3, **status):
    return event(
        identity,
        sequence,
        "finished",
        status={
            "state": "completed",
            "returncode": 0,
            "output_complete": True,
            "total_output_bytes": 6,
            "ended_at": 2,
            **status,
        },
    )
