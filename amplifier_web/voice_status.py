"""Bounded public task facts and event-based voice progress pacing."""
from __future__ import annotations

import math
import re
import time

BUSY = {'working', 'starting', 'running', 'stopping', 'busy', 'queued'}
PHASES = {
    'queued': 'The request is queued.', 'starting': 'The conversation is starting.',
    'tools': 'Tools are running.', 'model': 'The model response is pending.',
    'streaming': 'A response is being written.', 'workers': 'Delegated work is active.',
    'retrying': 'A request is being retried.', 'approval': 'Your approval is needed.',
    'stopping': 'The conversation is stopping.',
}


def timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def projection(session, *, now=None):
    now = time.time() if now is None else now
    activity = session.get('activity') or {}
    workers = session.get('workers') or []
    pending = sum(row.get('status') == 'pending' for row in session.get('approvals', []))
    active_workers = sum(row.get('status') in BUSY for row in workers)
    raw_status = session.get('status')
    if pending:
        status = 'waiting_for_approval'
    elif raw_status in {'error', 'failed', 'stopped', 'interrupted', 'cancelled'}:
        status = 'failed' if raw_status in {'error', 'failed'} else 'stopped'
    elif raw_status in BUSY:
        status = 'queued' if raw_status == 'queued' else 'running'
    elif active_workers:
        status = 'workers_running'
    elif raw_status == 'idle':
        status = 'idle'
    else:
        status = 'unknown'
    active = status in {'running', 'queued', 'workers_running', 'waiting_for_approval'}
    phase = activity.get('phase') if active else None
    phase = phase if phase in PHASES else 'unknown'
    started = timestamp(activity.get('startedAt')) if active else None
    updated = timestamp(activity.get('updatedAt'))
    # Names only: never expose tool arguments/results, raw errors or reasoning.
    tools = []
    if active:
        for row in activity.get('activeTools', []):
            name = row.get('tool')
            if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', name) and name not in tools:
                tools.append(name)
            if len(tools) >= 5:
                break
    summary = ('Your approval is needed.' if pending else
               'The conversation failed; inspect its recorded error before retrying.' if status == 'failed' else
               'The conversation stopped.' if status == 'stopped' else
               'The manager is idle; delegated work is still active.' if status == 'workers_running' else
               'No work is currently running.' if status == 'idle' else PHASES.get(phase, 'No verified task progress is available.'))
    return {'status': status, 'phase': phase, 'summary': summary,
            'started_at': started, 'updated_at': updated,
            'elapsed_seconds': int(max(0, now - started) // 5 * 5) if started else None,
            'active_tools': tools, 'active_workers': active_workers,
            'pending_approvals': pending, 'user_action_required': bool(pending),
            'response_pending': status in {'running', 'queued', 'workers_running'}}


def signature(status):
    return (status['status'], status['phase'], tuple(status['active_tools']),
            status['active_workers'], status['pending_approvals'])


class ProgressPacer:
    """No ordinary wait notice before 10s; no repeated unchanged filler.

    Meaningful phase changes use 15/30/60/120s backoff. Approval/failure changes
    bypass that delay. Completed results have their own generation delivery.
    """
    def __init__(self):
        self.run = None
        self.first_seen = None
        self.last_notice = None
        self.last_signature = None
        self.interval = 15

    def delay(self, status, *, now=None):
        now = time.monotonic() if now is None else now
        run = status.get('started_at')
        if self.first_seen is None or run != self.run:
            self.run, self.first_seen = run, now
            self.last_notice, self.last_signature, self.interval = None, None, 15
        current = signature(status)
        if current == self.last_signature:
            return None
        urgent = status['status'] in {'waiting_for_approval', 'failed', 'stopped'}
        if not urgent:
            if not status['response_pending']:
                return None
            deadline = self.first_seen + 10
            if self.last_notice is not None:
                deadline = max(deadline, self.last_notice + self.interval)
            return max(0, deadline - now)
        return 0

    def due(self, status, *, now=None):
        now = time.monotonic() if now is None else now
        if self.delay(status, now=now) != 0:
            return False
        current = signature(status)
        if self.last_notice is not None:
            self.interval = min(120, self.interval * 2)
        self.last_signature, self.last_notice = current, now
        return True

    def suppress(self, status, *, now=None):
        """A confirmed result already supplies this state to the voice."""
        self.delay(status, now=now)
        self.last_signature = signature(status)
