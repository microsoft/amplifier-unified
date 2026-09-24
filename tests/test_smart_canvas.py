"""User/agent parity and durable MCP App bindings, with no network/model calls."""
import copy
import pytest
from amplifier_web.service import AppService, AppError
from amplifier_web.smart_canvas import SmartCanvas, configuration_key
from amplifier_web.canvas_library import load


class Tools:
    def __init__(self, service):
        self.service = service
        service.state['smartTools'] = {'servers':[{'id':'one','name':'Counter','command':'counter', 'args':[], 'env':{},
            'tools':[{'name':'read','inputSchema':{'type':'object'},'_meta':{'ui':{'resourceUri':'ui://counter'}}}]}], 'operations':[]}
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
    assert not collect(service.db, service._state)
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
