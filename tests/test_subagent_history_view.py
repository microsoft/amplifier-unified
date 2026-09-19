"""The subagent history UI state is shared with agents and survives app restarts."""
from amplifier_web.service import AppService


async def test_subagent_history_navigation_is_agent_accessible_and_persistent(tmp_path):
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        created = await app.dispatch('session.create', {})
        session_id = created['state']['selectedSessionId']
        history = {'sessionId': session_id, 'filter': '*research*', 'index': 2}
        await app.app_bridge('dispatch', {'action': 'view.update', 'args': {'patch': {
            'panel': 'subagent-history', 'subagentHistory': history}}}, session_id)
        assert app.get_state()['view']['subagentHistory'] == history
        overview = await app.app_bridge('get_state', {}, session_id)
        assert overview['view']['subagentHistory'] == history
        read = await app.app_bridge('get_state', {'path': '/view/subagentHistory'}, session_id)
        assert {row['key']: row['value'] for row in read['items']} == history
        await app.dispatch('view.update', {'patch': {'panel': None}})
    finally:
        await app.close()

    restored = AppService(tmp_path, workspace=tmp_path)
    try:
        assert restored.get_state()['view']['subagentHistory'] == history
        assert restored.get_state()['view']['panel'] is None
    finally:
        await restored.close()
