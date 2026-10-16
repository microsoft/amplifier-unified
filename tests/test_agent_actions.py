import json

import pytest

from amplifier_web.agent_actions import PAGE_BYTES, encoded, read_actions
from amplifier_web.service import AppService


def test_real_catalog_pages_preserve_every_exact_schema():
    from amplifier_web.service import ACTION_DEFINITIONS
    actions = [{'name': name, 'description': desc, 'inputSchema': spec}
               for name, (desc, spec) in ACTION_DEFINITIONS.items()]
    args, found = {}, []
    while True:
        page = read_actions(actions, args)
        assert len(encoded(page)) <= PAGE_BYTES
        for item in page['items']:
            if 'schemaRead' in item:
                request, text = item['schemaRead'], ''
                while True:
                    schema = read_actions(actions, request)
                    assert len(encoded(schema)) <= PAGE_BYTES
                    text += schema['schemaText']
                    if schema['nextSchemaOffset'] is None:
                        break
                    request = {**request, 'schemaOffset': schema['nextSchemaOffset']}
                item = json.loads(text)
            found.append(item)
        if page['nextOffset'] is None:
            break
        args = {'offset': page['nextOffset'], 'revision': page['revision']}
    assert found == sorted(actions, key=lambda row: row['name'])


def test_large_unicode_schema_is_paged_losslessly_and_rejects_changed_catalog():
    actions = [{'name': 'test.large', 'description': 'Large',
                'inputSchema': {'const': '\U0001f680"\\' * 10000}}]
    page = read_actions(actions, {})
    args = page['items'][0]['schemaRead']
    text = ''
    while True:
        detail = read_actions(actions, args)
        assert len(encoded(detail)) <= PAGE_BYTES
        text += detail['schemaText']
        if detail['nextSchemaOffset'] is None:
            break
        args = {**args, 'schemaOffset': detail['nextSchemaOffset']}
    assert json.loads(text) == actions[0]
    with pytest.raises(ValueError, match='changed'):
        read_actions([{**actions[0], 'description': 'Changed'}], args)


@pytest.mark.parametrize('args', [{'offset': -1}, {'limit': 0}, {'limit': True},
                                  {'prefix': []}, {'name': []},
                                  {'name': 'test.a', 'schemaOffset': -1}])
def test_invalid_paging_is_refused(args):
    with pytest.raises(ValueError):
        read_actions([{'name': 'test.a', 'description': '', 'inputSchema': {}}], args)


async def test_permissions_read_keeps_exact_domain_result_without_repeated_overview(tmp_path):
    from amplifier_web.management import Management
    from test_configuration_authority import finished
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        # Reproduce a sizeable visible app state independently of the permission read.
        app.state['canvas'] = {'content': 'canvas content' * 4000}
        app.management = Management(app)
        args = {'scope': 'session', 'sessionId': sid}
        result = await app.app_bridge('dispatch', {'action': 'permissions.get', 'args': args, 'id': 'read-permissions'}, sid)
        assert result['accepted'] and result['operationId'] == 'read-permissions'
        assert (await finished(app, 'read-permissions'))['phase'] == 'ready'
        permissions = await app.app_bridge('get_state', {'path': '/permissions'}, sid)
        fields = {row['key']: row['value'] for row in permissions['items']}
        assert fields['scope'] == 'session' and fields['sessionId'] == sid
        assert fields['commandId'] == 'read-permissions'
        assert set(result['state']) == {'revision', 'sessionId', '_stateAccess'}
        assert 'canvas content' not in encoded(result)
        assert len(encoded(result)) < 12000
        assert (await app.app_bridge('get_state', {'path': '/canvas/content', 'limit': 12}, sid))['value'] == 'canvas conte'
    finally:
        await app.close()


async def test_catalog_and_permissions_fit_real_programmatic_output_limit(tmp_path):
    """Optional cross-module fixture: real JS process, tool and app; synthetic lease."""
    from types import SimpleNamespace
    module = pytest.importorskip('amplifier_module_tool_exec')
    from amplifier_web.management import Management
    from test_app_control_validation import mounted_tool

    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        sid = app.state['selectedSessionId']
        app.management = Management(app)
        app.state['canvas'] = {'content': 'visible large content' * 4000}

        async def bridge(operation, args):
            return await app.app_bridge(operation, args, sid)

        mounted = await mounted_tool(bridge)

        class Lease:
            tools = ('app_control',)
            context = {'session_id': sid, 'run_id': 'fixture', 'parent_tool_call_id': 'parent'}

            async def call(self, name, arguments, *, request_id):
                result = await mounted.execute(arguments)
                return {'status': 'completed' if result.success else 'failed', 'success': result.success,
                        'output': result.output, 'source': {**self.context, 'tool_name': name,
                        'request_id': request_id, 'tool_call_id': 'child-' + request_id}}

            def close(self):
                pass

        for request in [{'operation': 'list_actions'}, {'operation': 'dispatch', 'args': {
                'action': 'permissions.get', 'args': {'scope': 'session', 'sessionId': sid}}}]:
            lease = Lease()
            tool = module.ProgrammaticTool(SimpleNamespace(get_capability=lambda _: SimpleNamespace(version=1, bind=lambda: lease)))
            result = await tool.execute({'code': 'text(await tools.app_control(' + json.dumps(request) + '))'})
            assert result.success, result.error
            assert result.output['output_bytes'] < 32768
            assert result.output['calls'][0]['status'] == 'completed'
    finally:
        await app.close()
