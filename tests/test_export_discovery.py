"""Export/share procedures remain available without an optional skill or browser."""
import pytest

from test_configuration_authority import app
from test_full_child_configuration import mounted, dispatch


@pytest.mark.parametrize('actor', ['root', 'child'])
async def test_native_caller_discovers_and_reads_private_snapshots(mounted, actor):
    h = mounted
    text = 'Visible α conversation. ' * 80
    h.app._message(h.source, 'user', text, 'text')
    visited = []

    async def journey(control):
        catalog = {}
        for prefix in ('session.export', 'session.share'):
            args = {'prefix': prefix}
            while True:
                result = await control.execute({'operation': 'list_actions', 'args': args})
                assert result.success, result.error
                page = result.output
                catalog.update((row['name'], row) for row in page['items'])
                if page['nextOffset'] is None:
                    break
                args.update(offset=page['nextOffset'], revision=page['revision'])
        # The tool alone supplies the complete schemas and the displaced recipe.
        assert 'statePath' in catalog['session.export']['description']
        assert 'nextOffset' in catalog['session.export']['description']
        assert 'connected browser' in catalog['session.exportDeliver']['description']
        assert 'contentHash' in catalog['session.shareCreate']['inputSchema']['required']
        assert 'bearer link' in catalog['session.shareCreate']['description']
        assert 'nextOffset' in catalog['session.shareRead']['description']

        result = await dispatch(control, 'session.export', {
            'id': h.source['id'], 'format': 'markdown', 'destination': 'none'}, 'export')
        assert result.success, result.error
        snapshot = result.output['result']
        h.app._message(h.source, 'assistant', 'AFTER SNAPSHOT', 'text')
        parts, offset = [], 0
        while True:
            page = await control.execute({'operation': 'get_state', 'args': {
                'path': snapshot['statePath'], 'offset': offset, 'limit': 400}})
            assert page.success, page.error
            parts.append(page.output['value'])
            if page.output['nextOffset'] is None:
                break
            offset = page.output['nextOffset']
        value = ''.join(parts)
        assert text in value and 'AFTER SNAPSHOT' not in value
        assert len(parts) > 1

        preview = await dispatch(control, 'session.sharePreview', {
            'sessionId': h.source['id']}, 'preview')
        assert preview.success, preview.error
        preview = preview.output['result']
        assert preview['status'] == 'preview' and 'path' not in preview
        assert 'AFTER SNAPSHOT' in preview['text']
        assert not h.source.get('approvals')
        visited.append(True)

    if actor == 'root':
        await journey(h.parent.coordinator.get('tools')['app_control'])
    else:
        async def execute(self, prompt, context, providers, tools, hooks, coordinator):
            assert coordinator.session_id == 'export-child'
            await journey(tools['app_control'])
            return 'inspected'
        h.install_execute(execute)
        await h.registry.spawn('worker', 'Inspect the conversation export', h.parent,
                               sub_session_id='export-child')
    assert visited == [True]
    h.approval_prompt.assert_not_awaited()
