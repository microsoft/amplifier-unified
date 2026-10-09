"""User/agent parity and durable MCP App bindings, with no network/model calls."""
import copy
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.smart_canvas import SmartCanvas, configuration_key
from amplifier_web.canvas_library import load
from test_scoped_state_records import committed_session_payloads


class Tools:
    def __init__(self, service):
        self.service = service
        service.state['smartTools'] = {'servers':[{'id':'one','name':'Counter','command':'counter', 'args':[], 'env':{},
            'tools':[{'name':'read','inputSchema':{'type':'object'},'_meta':{'ui':{'resourceUri':'ui://counter'}}}]}], 'operations':[]}
        self.schemas = {'one':copy.deepcopy(service.state['smartTools']['servers'][0]['tools'])}
    async def read_app(self, identity, uri):
        return {'html':'<!doctype html><h1>Independent</h1>','tools':['read'],'csp':{},'permissions':{}}
    async def command(self, action, args, identity, origin, *, defer_publish=False):
        row={'configuration':configuration_key(self.service.state['smartTools']['servers'][0]),'id':identity,'action':action,'origin':origin,'target':copy.deepcopy(args),'arguments':args.get('arguments',{}),
             'status':'completed','result':{'content':[{'type':'text','text':'One'}],'structuredContent':{'count':1}}}
        async with self.service.lock:
            self.service.state['smartTools']['operations'] = [o for o in self.service.state['smartTools']['operations'] if o['id'] != identity]
            self.service.state['smartTools']['operations'].append(row)
            self.service._publish_smart_tool_update(defer_publish=defer_publish)
        return row['result']
    def persist_operation(self, record):pass
    def operation(self, identity):return next((o for o in self.service.state['smartTools']['operations'] if o['id']==identity), None)
    async def close(self):pass
    async def resource_request(self, identity, kind, **args):
        assert identity == 'one'
        assert args['expected_configuration'] == configuration_key(self.service.state['smartTools']['servers'][0])
        return {'contents':[{'uri':args['uri'],'text':'Scoped media'}]}


async def settled(service):
    import asyncio
    while service.tasks:
        await asyncio.gather(*list(service.tasks))


@pytest.fixture
async def service(tmp_path):
    service=AppService(tmp_path,workspace=str(tmp_path))
    service.smart_tools=Tools(service)
    service.smart_canvas=SmartCanvas(service)
    await service.dispatch('session.create',{'title':'Test'})
    yield service
    await service.close()


async def test_agent_call_view_reopen_and_context(service):
    sid=service.state['selectedSessionId']
    result=await service.app_bridge('dispatch',{'action':'smartTools.call','id':'call1','args':{'id':'one','name':'read','arguments':{'object':'shared'}}},sid)
    assert result['operationId']=='call1'
    await settled(service)
    await service.app_bridge('dispatch',{'action':'smartTools.open','args':{'id':'one','tool':'read','operationId':'call1'}},sid)
    await settled(service)
    canvas=copy.deepcopy(service.state['canvas'])
    assert canvas['mcp']['toolArguments']=={'object':'shared'}
    await service.dispatch('smartTools.context',{'canvasId':canvas['id'],'context':{'structuredContent':{'selection':'A'}}})
    await service.dispatch('canvas.close')
    load(service.state,service.db,canvas['id'])
    assert service.state['canvas']['mcp']['context']['structuredContent']['selection']=='A'
    assert len(service.state['smartTools']['operations'])==2
    overview=await service.app_bridge('get_state',{},sid)
    assert 'smartTools' in overview
    # MCP body is in durable storage, not duplicated into every artifact index.
    assert 'mcp' not in service.state['canvasArtifacts'][-1]


async def test_stale_closed_and_unknown_tool_bindings(service):
    await service.smart_canvas.open({'id':'one','tool':'read'})
    cid=service.state['canvas']['id']
    await service.smart_canvas.command('smartTools.appCall',{'canvasId':cid,'name':'ungranted'},'bad','ui')
    assert service.smart_tools.operation('bad')['status']=='failed'
    service.state['smartTools']['servers'][0]['command']='other'
    with pytest.raises(AppError,match='configuration changed'):
        service.smart_canvas.binding(cid)
    service.state['smartTools']['servers'][0]['command']='counter'
    await service.dispatch('canvas.close')
    with pytest.raises(AppError,match='no longer active'):
        service.smart_canvas.binding(cid)


async def test_commands_deduplicate_before_tool_call(service):
    args={'id':'one','name':'read','arguments':{}}
    first=await service.dispatch('smartTools.call',args,command_id='once')
    second=await service.dispatch('smartTools.call',args,command_id='once')
    assert second['duplicate'] and second['operationId']==first['operationId']
    await settled(service)
    assert len(service.state['smartTools']['operations'])==1


async def test_interactive_wait_timeout_does_not_cancel_or_repeat(service):
    import asyncio
    gate = asyncio.Event()
    original = service.smart_tools.command
    calls = []
    async def delayed(*args, **kwargs):
        calls.append(args)
        await gate.wait()
        return await original(*args, **kwargs)
    service.smart_tools.command = delayed
    await service.smart_canvas.open({'id': 'one', 'tool': 'read'})
    args = {'canvasId': service.state['canvas']['id'], 'name': 'read'}
    receipt = await service.dispatch('smartTools.appCall', args, command_id='slow-input')
    assert (await service.wait_smart_tool(receipt['operationId'], timeout=.01))['status'] == 'pending'
    assert not service.smart_tool_requests['slow-input'].done()
    duplicate = await service.dispatch('smartTools.appCall', args, command_id='slow-input')
    assert duplicate['duplicate']
    gate.set()
    assert (await service.wait_smart_tool('slow-input'))['status'] == 'completed'
    assert len(calls) == 1


async def test_resources_use_saved_binding_and_reject_detach_during_read(service):
    await service.smart_canvas.open({'id':'one','tool':'read'})
    cid=service.state['canvas']['id']
    result=await service.smart_canvas.resource(cid,'read',uri='counter://media/one')
    assert result['contents'][0]['text']=='Scoped media'
    original=service.smart_tools.resource_request
    async def detached(*args,**kwargs):
        result=await original(*args,**kwargs)
        await service.dispatch('canvas.close')
        return result
    service.smart_tools.resource_request=detached
    with pytest.raises(AppError,match='no longer active'):
        await service.smart_canvas.resource(cid,'read',uri='counter://media/one')


async def test_failed_open_has_visible_terminal_operation(service):
    receipt=await service.dispatch('smartTools.open',{'id':'missing','tool':'read'})
    await settled(service)
    operation=next(o for o in service.state['smartTools']['operations'] if o['id']==receipt['operationId'])
    assert operation['status']=='failed' and 'connect' in operation['error']


async def test_accepted_app_call_closed_before_execution_is_terminal(service):
    await service.smart_canvas.open({'id':'one','tool':'read'})
    cid=service.state['canvas']['id']
    receipt=await service.dispatch('smartTools.appCall',{'canvasId':cid,'name':'read'})
    await service.dispatch('canvas.close')
    await settled(service)
    operation=service.smart_tools.operation(receipt['operationId'])
    assert operation['status']=='failed' and 'no longer active' in operation['error']


@pytest.mark.usefixtures('committed_session_payloads')
async def test_explicit_presentation_identity_reuses_tab_and_retains_exact_results(service):
    from amplifier_web.mcp_view_recovery import source, inspect
    from amplifier_web.resource_files import collect
    from amplifier_web.state_storage import resource
    sid = service._session()['id']
    service._message(service._session(), 'user', 'Open dashboard')
    await service.smart_tools.command('smartTools.call', {'id':'one','name':'read','sessionId':sid}, 'call-one', 'agent')
    operation = service.smart_tools.operation('call-one')
    operation['result']['_meta'] = {'amplifier/presentationId':'run-123'}
    first = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'call-one'})
    await service.smart_canvas.open({'id':'one','tool':'read','operationId':'call-one'})
    assert len(service.state['canvasArtifacts']) == 1
    assert service.state['canvas']['revision'] == 1
    initial_view_revision = service.canvas_views.revision(service.state['canvasArtifacts'][0])
    service._message(service._session(), 'user', 'Updated dashboard')
    await service.smart_tools.command('smartTools.call', {'id':'one','name':'read','sessionId':sid}, 'call-two', 'agent')
    operation = service.smart_tools.operation('call-two')
    operation['result']['_meta'] = {'amplifier/presentationId':'run-123'}
    operation['result']['structuredContent'] = {'count':2}
    second = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'call-two'})
    assert first['canvasId'] == second['canvasId']
    assert second['revision'] == 2
    assert service.canvas_views.revision(service.state['canvasArtifacts'][0]) != initial_view_revision
    with pytest.raises(AppError, match='changed'):
        await service.dispatch('smartTools.appCall', {'canvasId': first['canvasId'], 'name': 'read', 'expectedRevision': 1})
    with pytest.raises(AppError, match='changed'):
        await service.dispatch('smartTools.context', {'canvasId': first['canvasId'], 'context': {}, 'expectedRevision': 1})
    # Dropping the rolling operation summary cannot erase the saved result.
    service.state['smartTools']['operations'] = []
    from test_scoped_state_records import assert_only_superseded_session_payloads_collected
    assert_only_superseded_session_payloads_collected(service)
    await service.dispatch('canvas.select', {'id':first['canvasId'],'version':1})
    assert 'Independent' in source(service,first['canvasId'])
    assert inspect(service,first['canvasId'])['status'] == 'saved_version'
    assert resource(service.db,service.state['canvas']['mcp']['savedResult']['$resource'])['structuredContent'] == {'count':1}
    with pytest.raises(AppError,match='read-only'):
        service.smart_canvas.binding(first['canvasId'])
    await service.dispatch('canvas.select', {'id':first['canvasId']})
    assert resource(service.db,service.state['canvas']['mcp']['savedResult']['$resource'])['structuredContent'] == {'count':2}


async def test_independent_calls_without_explicit_identity_and_distinct_runs_stay_separate(service):
    sid = service._session()['id']
    for identity in ('a','b'):
        await service.smart_tools.command('smartTools.call', {'id':'one','name':'read','sessionId':sid}, identity, 'agent')
        await service.smart_canvas.open({'id':'one','tool':'read','operationId':identity})
    assert len(service.state['canvasArtifacts']) == 2
    for identity in ('run-a','run-b'):
        await service.smart_tools.command('smartTools.call', {'id':'one','name':'read','sessionId':sid}, identity, 'agent')
        service.smart_tools.operation(identity)['result']['_meta'] = {'amplifier/presentationId':identity}
        await service.smart_canvas.open({'id':'one','tool':'read','operationId':identity})
    assert len(service.state['canvasArtifacts']) == 4
    titles = [row['title'] for row in service.state['canvasArtifacts']]
    assert len(set(titles)) == 4
    assert all(title.startswith(f"[{row['id'][:8]}] ") and title.endswith('Counter')
               for row, title in zip(service.state['canvasArtifacts'], titles))


async def presentation_setup(service):
    server = service.state['smartTools']['servers'][0]
    other = copy.deepcopy(server['tools'][0])
    other['name'] = 'details'
    server['tools'].append(other)
    service.smart_tools.schemas['one'] = copy.deepcopy(server['tools'])
    app = {'html':'<!doctype html><h1>Shared dashboard</h1>', 'tools':['read','details'], 'csp':{}, 'permissions':{}}
    async def read_app(identity, uri):
        return copy.deepcopy(app)
    service.smart_tools.read_app = read_app
    return server, app


async def present(service, operation_id, tool='read', presentation_id='entity-1', metadata=None):
    sid = service._session()['id']
    await service.smart_tools.command('smartTools.call', {'id':'one','name':tool,'sessionId':sid}, operation_id, 'agent')
    operation = service.smart_tools.operation(operation_id)
    operation['result']['_meta'] = {'amplifier/presentationId':presentation_id}
    if metadata is not None:
        operation['result']['_meta'].update(metadata)
    operation['arguments'] = {'entity':presentation_id,'operation':operation_id}
    return await service.smart_canvas.open({'id':'one','tool':tool,'operationId':operation_id})


async def test_cross_method_presentation_keeps_legacy_id_and_exact_saved_links(service):
    from amplifier_web.mcp_view_recovery import digest, source
    from amplifier_web.state_storage import resource
    await presentation_setup(service)
    first = await present(service, 'first')
    binding = copy.deepcopy(service.state['canvas']['mcp'])
    label = binding['presentationLabel']
    assert label == first['canvasId'][:8]
    # Reproduce the old launcher-specific identity, without rewriting history.
    legacy = digest([binding['configuration'],binding['accountIdentity'],'read',binding['resourceUri'],['presentation','entity-1']])
    service.state['canvas']['presentationKey'] = legacy
    service.state['canvasArtifacts'][0]['presentationKey'] = legacy
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    second = await present(service, 'second', 'details', metadata={'amplifier/presentationTitle':'Updated dashboard'})
    assert second['canvasId'] == first['canvasId']
    assert len(service.state['canvasArtifacts']) == 1
    assert second['revision'] == 2
    assert service.state['canvas']['mcp']['tool'] == 'details'
    assert service.state['canvas']['mcp']['presentationLabel'] == label
    assert service.state['canvas']['title'] == f'[{label}] Updated dashboard'
    assert service.state['canvasArtifacts'][0]['versions'][0] == original['versions'][0]
    await service.dispatch('canvas.select', {'id':first['canvasId'],'version':1})
    assert service.state['canvas']['title'] == original['title']
    assert service.state['canvas']['mcp']['tool'] == 'read'
    assert service.state['canvas']['mcp']['toolArguments']['operation'] == 'first'
    assert 'Shared dashboard' in source(service, first['canvasId'])
    assert resource(service.db, service.state['canvas']['mcp']['savedResult']['$resource'])['_meta']['amplifier/presentationId'] == 'entity-1'
    await service.dispatch('canvas.select', {'id':first['canvasId']})
    assert service.state['canvas']['mcp']['tool'] == 'details'
    assert len(service.state['smartTools']['operations']) == 2  # No re-execution.
    # Reopening the same call remains idempotent, including across methods.
    reopened = await service.smart_canvas.open({'id':'one','tool':'details','operationId':'second'})
    assert reopened['revision'] == 2


@pytest.mark.parametrize('change', ['account','grant','schema','permissions','csp','resource','configuration','missing_contract','entity','session','workspace','unconfirmed_account'])
async def test_explicit_identity_never_reuses_different_authority_or_entity(service, change):
    from amplifier_web.resource_files import put
    from amplifier_web.state_storage import resource
    server, app = await presentation_setup(service)
    first = await present(service, 'first')
    if change == 'account':
        server['accountBinding'] = {'issuer':'https://identity.example','subject':'different'}
    elif change == 'grant':
        app['tools'] = ['details']
    elif change == 'schema':
        service.smart_tools.schemas['one'][0]['inputSchema'] = {'type':'object','required':['new_required']}
    elif change == 'permissions':
        app['permissions'] = {'clipboardWrite':{}}
    elif change == 'csp':
        app['csp'] = {'connectDomains':['https://other.example']}
    elif change == 'resource':
        server['tools'][1]['_meta']['ui']['resourceUri'] = 'ui://other-dashboard'
        service.smart_tools.schemas['one'][1]['_meta']['ui']['resourceUri'] = 'ui://other-dashboard'
    elif change == 'configuration':
        server['command'] = 'different-program'
    elif change == 'missing_contract':
        binding = resource(service.db, service.state['canvasArtifacts'][0]['mcpState']['$resource'])
        binding.pop('contractFingerprint')
        service.state['canvas']['mcp'] = binding
        service.state['canvasArtifacts'][0]['mcpState'] = put(service.db, binding)
    elif change == 'session':
        await service.dispatch('session.create', {'title':'Separate conversation'})
    elif change == 'workspace':
        service.state['workspaces'][0]['id'] = 'other-workspace'
        service.state['selectedWorkspaceId'] = 'other-workspace'
    elif change == 'unconfirmed_account':
        server['account'] = {'status':'unconfirmed'}
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    second = await present(service, 'second', 'details', 'other-entity' if change == 'entity' else 'entity-1')
    assert second['canvasId'] != first['canvasId']
    assert len(service.state['canvasArtifacts']) == 2
    assert service.state['canvasArtifacts'][0] == original
    assert service.state['canvas']['title'] != original['title']
    assert service.state['canvas']['title'].endswith('Counter')
    assert original['title'].endswith('Counter')


@pytest.mark.parametrize('damaged', [None, [], 'not a binding'])
async def test_explicit_identity_keeps_malformed_saved_binding(service, damaged):
    from amplifier_web.resource_files import put
    await presentation_setup(service)
    first = await present(service, 'first')
    service.state['canvasArtifacts'][0]['mcpState'] = put(service.db, damaged)
    from amplifier_web.canvas_library import empty
    empty(service.state)  # No live binding to legitimately repair the saved row.
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    second = await present(service, 'second', 'details')
    assert second['canvasId'] != first['canvasId']
    assert service.state['canvasArtifacts'][0] == original


@pytest.mark.parametrize('title, expected', [
    ('  Quarterly report  ', 'Quarterly report'),
    ('שלום עולם', 'שלום עולם'),
    ('تقرير المراجعة', 'تقرير المراجعة'),
    ('\U0001f642' * 160, '\U0001f642' * 160),
    ('<img src=x onerror=alert(1)>', '<img src=x onerror=alert(1)>'),
    ('[review](javascript:alert(1))', '[review](javascript:alert(1))'),
    ('https://display.example/report', 'https://display.example/report'),
    ('', 'Generic dashboard'),
    ('   ', 'Generic dashboard'),
    (None, 'Generic dashboard'),
    (False, 'Generic dashboard'),
    (123, 'Generic dashboard'),
    ([], 'Generic dashboard'),
    ({'title':'not a string'}, 'Generic dashboard'),
    ('\U0001f642' * 161, 'Generic dashboard'),
    ('\nReport', 'Generic dashboard'),
    ('Report\t', 'Generic dashboard'),
    ('Report\x00hidden', 'Generic dashboard'),
    ('Report\x7fhidden', 'Generic dashboard'),
    ('Report\x85hidden', 'Generic dashboard'),
    ('Report\u202ehidden', 'Generic dashboard'),
    ('Report\u2066hidden\u2069', 'Generic dashboard'),
])
async def test_presentation_title_is_bounded_literal_display_only(service, title, expected):
    from amplifier_web.state_storage import resource
    server, _ = await presentation_setup(service)
    server['tools'][0]['title'] = 'Generic dashboard'
    result = await present(service, 'display', metadata={'amplifier/presentationTitle':title})
    canvas = service.state['canvas']
    assert canvas['title'] == f"[{result['canvasId'][:8]}] {expected}"
    assert len(canvas['title']) <= 200
    assert canvas['content'] == '<!doctype html><h1>Shared dashboard</h1>'
    assert canvas['mcp']['allowedTools'] == ['read','details']
    assert canvas['mcp']['requestedCsp'] == {}
    assert canvas['mcp']['requestedPermissions'] == {}
    saved = resource(service.db, canvas['mcp']['savedResult']['$resource'])
    assert saved['_meta']['amplifier/presentationTitle'] == title  # Exact untrusted result, not sanitized domain data.
    assert len(service.state['smartTools']['operations']) == 1


@pytest.mark.parametrize('codepoint', [
    *range(0x20), *range(0x7f, 0xa0), 0x200e, 0x200f,
    *range(0x202a, 0x202f), *range(0x2066, 0x206a),
])
def test_display_title_rejects_every_control_and_bidi_format(codepoint):
    from amplifier_web.smart_canvas import _display_title
    assert _display_title('Report' + chr(codepoint)) is None


@pytest.mark.parametrize('metadata', [None, [], 'not metadata', {}, {'amplifier/presentationTitle':'x' * 161}])
async def test_missing_or_malformed_display_metadata_falls_back_without_domain_parsing(service, metadata):
    sid = service._session()['id']
    await service.smart_tools.command('smartTools.call', {'id':'one','name':'read','sessionId':sid}, 'fallback', 'agent')
    operation = service.smart_tools.operation('fallback')
    operation['result']['_meta'] = metadata
    operation['result']['structuredContent'] = {'title':'Domain title must not be parsed'}
    result = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'fallback'})
    assert service.state['canvas']['title'] == f"[{result['canvasId'][:8]}] Counter"


async def test_long_generic_titles_have_short_leading_distinction_and_safe_fallback(service):
    server, _ = await presentation_setup(service)
    title = 'Identical long generic dashboard ' * 20
    server['tools'][0]['title'] = title
    first = await present(service, 'one', presentation_id='  opaque/id  ')
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    second = await present(service, 'two', presentation_id='opaque/id')
    titles = [row['title'] for row in service.state['canvasArtifacts']]
    assert first['canvasId'] != second['canvasId']  # Never trim/normalize presentationId.
    assert titles == [f"[{identity[:8]}] {title}"[:200] for identity in (first['canvasId'], second['canvasId'])]
    assert all(len(item) == 200 for item in titles)
    assert titles[0][:11] != titles[1][:11]  # Distinction precedes any long caption/ellipsis.
    assert service.state['canvasArtifacts'][0] == original
    server['tools'][0]['title'] = '\u202eunsafe'
    third = await present(service, 'three', presentation_id='third')
    assert service.state['canvas']['title'] == f"[{third['canvasId'][:8]}] Counter"
    server['name'] = '\x00unsafe'
    fourth = await present(service, 'four', presentation_id='fourth')
    assert service.state['canvas']['title'] == f"[{fourth['canvasId'][:8]}] MCP App"


@pytest.mark.usefixtures('committed_session_payloads')
async def test_title_change_preserves_exact_version_body_result_title_and_link(service):
    from amplifier_web.canvas_versions import reference
    from amplifier_web.state_storage import resource
    await presentation_setup(service)
    first = await present(service, 'old', metadata={'amplifier/presentationTitle':'Original'})
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    old_result = resource(service.db, service.state['canvas']['mcp']['savedResult']['$resource'])
    old_body = resource(service.db, original['body']['$resource'])
    # The same retained operation may gain display metadata; no launcher is replayed.
    service.smart_tools.operation('old')['result']['_meta']['amplifier/presentationTitle'] = 'Revised'
    second = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'old'})
    row = service.state['canvasArtifacts'][0]
    assert second['canvasId'] == first['canvasId']
    assert second['revision'] == 2
    assert service.state['canvas']['title'] == f"[{first['canvasId'][:8]}] Revised"
    assert row['versions'][0] == original['versions'][0]
    assert row['publications'][:len(original['publications'])] == original['publications']
    assert reference(row, 1) == first['reference']
    current = copy.deepcopy(row)
    reopened = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'old'})
    assert reopened['revision'] == 2
    assert row['versions'] == current['versions']
    await service.dispatch('canvas.select', {'id':first['canvasId'],'version':1})
    assert service.state['canvas']['title'] == original['title']
    assert service.state['canvas']['content'] == old_body['content']
    assert resource(service.db, service.state['canvas']['mcp']['savedResult']['$resource']) == old_result
    assert service.state['canvas']['readOnlyVersion']
    assert len(service.state['smartTools']['operations']) == 1


async def test_short_prefix_collision_extends_new_label_without_rewriting_existing_history(service, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import smart_canvas
    await presentation_setup(service)
    identities = iter([
        '12345678a' + '0' * 23,
        '12345678b' + '0' * 23,
        '12345678b1' + '0' * 22,
        'ffffffff' + '0' * 24,  # Temporary incoming UUID, not the reused document's label.
        '12345678c' + '0' * 23,
    ])
    monkeypatch.setattr(smart_canvas, 'uuid', SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=next(identities))))
    first = await present(service, 'first', presentation_id='a')
    original = copy.deepcopy(service.state['canvasArtifacts'][0])
    second = await present(service, 'second', presentation_id='b')
    third = await present(service, 'third', presentation_id='c')
    titles = [row['title'] for row in service.state['canvasArtifacts']]
    assert titles == ['[12345678] Counter', '[12345678b] Counter', '[12345678b1] Counter']
    assert service.state['canvasArtifacts'][0] == original
    second_snapshot = copy.deepcopy(service.state['canvasArtifacts'][1])
    reused = await present(service, 'updated', 'details', 'b', {'amplifier/presentationTitle':'Changed'})
    assert reused['canvasId'] == second['canvasId'] and reused['revision'] == 2
    assert service.state['canvas']['title'] == '[12345678b] Changed'
    assert service.state['canvasArtifacts'][1]['versions'][0] == second_snapshot['versions'][0]
    # The original short reference is stable even after two colliding additions.
    reopened = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'first'})
    assert reopened['canvasId'] == first['canvasId'] and reopened['revision'] == 1
    assert service.state['canvas']['title'] == '[12345678] Counter'
    assert third['canvasId'] != second['canvasId']


async def test_legacy_label_upgrade_is_one_new_definition_not_a_backfill(service):
    from amplifier_web.resource_files import put
    from amplifier_web.canvas_versions import reference
    await presentation_setup(service)
    first = await present(service, 'legacy')
    # Reproduce a pre-label saved definition, including its exact old binding.
    canvas = service.state['canvas']
    canvas['title'] = 'Counter'
    canvas['mcp'].pop('presentationLabel')
    row = service.state['canvasArtifacts'][0]
    row['title'] = 'Counter'
    row['mcpState'] = put(service.db, canvas['mcp'])
    row['versions'][0].update(title='Counter', mcpState=copy.deepcopy(row['mcpState']))
    original = copy.deepcopy(row)
    upgraded = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'legacy'})
    assert upgraded['canvasId'] == first['canvasId'] and upgraded['revision'] == 2
    assert service.state['canvas']['title'] == f"[{first['canvasId'][:8]}] Counter"
    assert row['versions'][0] == original['versions'][0]
    assert reference(row, 1) == first['reference']
    current = copy.deepcopy(row['versions'])
    for _ in range(2):
        reopened = await service.smart_canvas.open({'id':'one','tool':'read','operationId':'legacy'})
        assert reopened['revision'] == 2
        assert row['versions'] == current


async def test_display_discriminator_never_uses_account_configuration_or_tool_identity(service, monkeypatch):
    from types import SimpleNamespace
    from amplifier_web import smart_canvas
    server, _ = await presentation_setup(service)
    server['accountBinding'] = {'issuer':'https://identity.example','subject':'private-account-subject'}
    server['env'] = {'TOOL_SECRET':'PRIVATE_ENV_REFERENCE'}
    server['command'] = '/private/store/tool'
    service.smart_tools.schemas['one'] = copy.deepcopy(server['tools'])
    identity = 'abcde123' + '0' * 24
    monkeypatch.setattr(smart_canvas, 'uuid', SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=identity)))
    await present(service, 'private-operation', presentation_id='private-entity')
    canvas = service.state['canvas']
    assert canvas['title'] == '[abcde123] Counter'
    assert canvas['mcp']['presentationLabel'] == identity[:8]
    assert canvas['mcp']['accountIdentity']['subject'] == 'private-account-subject'
    assert canvas['presentationKey'] != identity  # The authority key is unchanged and separate.
