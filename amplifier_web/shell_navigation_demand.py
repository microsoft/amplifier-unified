"""Opt-in navigation resource selection; omitted demand preserves the v1 snapshot."""
import json

NAVIGATION_RESOURCES = frozenset({
    'chatNavigation', 'homeNavigation', 'pinnedNavigation', 'recentNavigation',
    'workspaceShortcuts', 'workspaceExplorer', 'sidebarSearch', 'sidebarWorkspace',
    'sidebarRecent', 'sidebarPinned', 'workspaceOverview',
})


def parse_navigation_resources(value):
    """Parse the bounded HTTP JSON array; None is the complete legacy contract."""
    if value is None:
        return None
    from .service import AppError
    if not isinstance(value, str) or len(value) > 4096:
        raise AppError('Invalid navigation resources.', 400)
    try:
        values = json.loads(value)
    except (ValueError, RecursionError):
        raise AppError('Invalid navigation resources.', 400) from None
    if (not isinstance(values, list) or len(values) > 16
            or any(not isinstance(item, str) or item not in NAVIGATION_RESOURCES for item in values)):
        raise AppError('Invalid navigation resources.', 400)
    return frozenset(values)


def resources_for(instance, requested):
    """Existing third-party packages retain their complete snapshot contract."""
    if requested is None or instance['package'] not in {'builtin.chats', 'builtin.workspaces'}:
        return None
    return requested
