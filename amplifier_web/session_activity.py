"""Shared lifecycle facts; navigation loading is not agent execution."""


def activity_flags(session):
    status = session.get('status', 'idle')
    return {
        'execution': status in {'working', 'running', 'stopping'},
        'preparing': bool(status == 'starting' or session.get('configurationBusy')
                          or (session.get('preparation') or {}).get('status') == 'preparing'),
        'loading': bool(session.get('historyLoading')),
    }


def navigation_busy(session):
    return any(activity_flags(session).values())
