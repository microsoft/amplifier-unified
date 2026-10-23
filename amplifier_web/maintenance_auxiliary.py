"""Bounded maintenance support from existing host operation owners.

This is a projection, never a job registry or a remote cancellation promise.
Native conversation continuations remain owned by Maintenance/RuntimeManager.
"""

from .update_blockers import auxiliary_blockers


def require_effect_admission(app):
    """Check at the transport's last boundary, after connection/queue waits."""
    from .maintenance import require_open
    require_open(app)


def notify_task_settled(app):
    """Refresh after the canonical task set removes a finished operation."""
    if getattr(app, 'closed', False) or getattr(app, 'maintenance', None) is None:
        return

    async def publish():
        async with app.lock:
            if not app.closed:
                app._publish_changes(globals={'smartTools'})

    app._task(publish())


def snapshot(app):
    items = []
    for row in auxiliary_blockers(app):
        kind = row['kind']
        if kind == 'configuration-unresolved':
            # Historical configuration uncertainty remains an Updates blocker,
            # not active work that a global maintenance Resume could settle.
            continue
        items.append({
            **row,
            'status': 'waiting',
            'pauseSupported': False,
            'detail': (
                'End the call or preview before restarting. Remote audio is not paused by stopping a conversation worker.'
                if kind == 'voice' else
                'Wait for the current naming request to finish. Already accepted provider requests cannot be recalled.'
                if kind == 'naming' else
                'Wait for the original test operation or reconcile its exact owner. Remote tests and setup are not suspended or stopped by Pause all or Stop now.'
                if kind in {'test-environments', 'test-setup'} else
                'Wait for the admitted conversation settings operation to settle before restarting.'
                if kind == 'configuration-active' else
                'Wait for the operation to settle. If its outcome is unknown, inspect the tool; stopping a conversation worker does not prove remote work stopped.'
            ),
        })
    return {'settled': not items, 'items': items, 'scope': 'host-observed',
            'remoteCancellationConfirmed': False}
