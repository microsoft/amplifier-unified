"""Bounded facts from an already resident session; no hydration or I/O.

Identifiers remain private to adapters. A spoken/public sentence must not use
these raw tool names, paths or labels directly.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityFacts:
    status: str = 'unavailable'
    phase: str = ''
    approvals: int = 0
    questions: int = 0
    tools: tuple[str, ...] = ()
    workers: int = 0
    truncated: bool = False
    configuring: bool = False


def resident_facts(session):
    if not isinstance(session, dict):
        return ActivityFacts()
    # ColdRecord.get can load history; dict.get deliberately bypasses it.
    raw = lambda key, default=None: dict.get(session, key, default)
    activity = raw('activity', {}) or {}
    approvals, questions = raw('approvals', []) or [], raw('questions', []) or []
    tools, workers = activity.get('activeTools', []) or [], raw('workers', []) or []
    return ActivityFacts(
        status=raw('status', 'unknown'), phase=activity.get('phase', ''),
        approvals=sum(row.get('status') == 'pending' for row in approvals[:200]),
        questions=sum(row.get('status') == 'pending' for row in questions[:200]),
        tools=tuple(row.get('tool') for row in tools[:32]),
        workers=sum(row.get('status') in {'working', 'running', 'starting'} for row in workers[:32]),
        truncated=len(approvals) > 200 or len(questions) > 200 or len(tools) > 32 or len(workers) > 32,
        configuring=bool(raw('configurationBusy') or raw('fileAccessPending')),
    )
