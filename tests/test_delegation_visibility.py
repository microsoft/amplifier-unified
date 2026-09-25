"""Worker routing disclosure reflects mounted policy and actual observed calls."""
from types import SimpleNamespace

import pytest

from amplifier_web.execution import ingest
from amplifier_web.execution_events import CALL_PURPOSE, ExecutionEvents
from amplifier_web.host.model_selection import child_routing, delegation_routing, inherited_selection


def coordinator(*, inherit=False, resolver=None):
    loop = SimpleNamespace(config={'inherit_effective_model':inherit},
        root_provider=SimpleNamespace(selection={'instance':'subscription','model':'chosen'}))
    return SimpleNamespace(get=lambda name:loop if name=='orchestrator' else None,
        get_capability=lambda name:resolver if name=='model_role_resolver' else None)


def test_routing_reports_actual_resolver_provenance_without_resolving_or_mutating():
    class Resolver:
        name='personal';matrix_source='user';matrix_path='/private/config';api_key='never-public'
        async def resolve(self, role):raise AssertionError('Read-only disclosure must not resolve')
    c=coordinator(inherit=True,resolver=Resolver())
    report=delegation_routing(c)
    assert report=={'modelInheritance':'conversation_when_unspecified','crossProviderRestriction':'not_enforced',
        'resolverActive':True,'resolverName':'personal','matrixSource':'user'}
    assert c.get('orchestrator').root_provider.selection=={'instance':'subscription','model':'chosen'}
    assert delegation_routing(coordinator())=={'modelInheritance':'bundle',
        'crossProviderRestriction':'not_enforced','resolverActive':False}


@pytest.mark.parametrize(('overlay','preferences','source'), [
    ({}, [], 'inherited_conversation'),
    ({}, [{'provider':'anthropic','model':'specialist'}], 'delegation_preferences'),
    ({'providers':[{'module':'provider-anthropic'}]}, [], 'agent_provider'),
    ({'model_role':'reasoning'}, [], 'agent_model_role'),
])
def test_explicit_parent_selection_and_child_override_precedence_are_disclosed(overlay, preferences, source):
    parent=SimpleNamespace(coordinator=coordinator(inherit=True))
    selection=inherited_selection(parent,overlay,preferences)
    assert child_routing(parent,overlay,preferences,selection)['selectionSource']==source
    assert (selection is not None)==(source=='inherited_conversation')


@pytest.mark.asyncio
async def test_child_actual_provider_usage_and_original_parent_survive_later_lifecycle():
    events=ExecutionEvents('root',lambda _:None)
    class Provider:
        def __init__(self,name):self.name=name
        def get_info(self):return SimpleNamespace(id=self.name,defaults={'model':self.name+'-default'})
        async def complete(self, request):return SimpleNamespace(usage=None)
    events.lifecycle({'type':'input.delivered','input_id':'first-turn'})
    root=events.instrument_provider('root',Provider('subscription'))
    await root.complete(SimpleNamespace(model='parent-choice'))
    events.hook('root','tool:pre',{'tool_call_id':'delegate-call','tool_name':'delegate'})
    routing={'selectionSource':'delegation_preferences','crossProviderRestriction':'not_enforced'}
    event={'type':'child.updated','sessionId':'child','parentSessionId':'root','callId':'delegate-call',
        'agent':'researcher','status':'running','runId':'run-one','routing':routing}
    events.lifecycle(event)
    child=events.instrument_provider('child',Provider('anthropic'))
    await child.complete(SimpleNamespace(model='child-override'))
    node=events.nodes['worker:child']
    assert (node['provider'],node['model'],node['parentProvider'])==('anthropic','child-override','subscription')
    assert node['parentId']=='tool:root:delegate-call'
    assert node['turnId']=='first-turn'
    await events.instrument_provider('root',Provider('later-parent')).complete(SimpleNamespace(model='later-model'))
    await child.complete(SimpleNamespace(model='child-override'))
    assert events.nodes['worker:child']['parentProvider']=='subscription'
    events.lifecycle({'type':'input.delivered','input_id':'later-turn'})
    events.lifecycle({**event,'status':'completed'})
    assert events.nodes['worker:child']['model']=='child-override'
    assert events.nodes['worker:child']['turnId']=='first-turn'
    session={}
    for row in events.nodes.values():ingest(session,row)
    public=next(n for n in session['execution']['nodes'] if n['id']=='worker:child')
    assert public['routing']==routing
    assert public['aggregateUsage']['tokenUnknownCalls']==2
    assert public['aggregateUsage']['costType']=='unavailable'
    # Auxiliary naming is not evidence that the worker switched its provider.
    token=CALL_PURPOSE.set({'label':'Naming','lifecycle':'background'})
    try:await events.instrument_provider('child',Provider('naming-provider')).complete(SimpleNamespace(model=None))
    finally:CALL_PURPOSE.reset(token)
    assert events.nodes['worker:child']['provider']=='anthropic'
    # A resumed run must not claim its previous model before observing a call.
    events.lifecycle({**event,'runId':'run-two','status':'starting'})
    assert 'provider' not in events.nodes['worker:child']
    ingest(session,events.nodes['worker:child'])
    public=next(n for n in session['execution']['nodes'] if n['id']=='worker:child')
    assert 'provider' not in public
    assert 'model' not in public


@pytest.mark.asyncio
async def test_hook_only_calls_report_model_and_do_not_assume_unknown_parent():
    events=ExecutionEvents('root',lambda _:None)
    events.lifecycle({'type':'child.updated','sessionId':'child','agent':'worker','status':'running'})
    events.hook('child','llm:request',{'provider':'test','model':'resolved'})
    assert events.nodes['worker:child']['model']=='resolved'
    assert 'parentProvider' not in events.nodes['worker:child']


def test_execution_boundary_drops_private_or_unknown_routing_fields():
    session={}
    ingest(session,{'id':'worker:c','kind':'worker','routing':{'resolverName':'known','api_key':'private',
        'matrix_path':'/private','selectionSource':'made-up','crossProviderRestriction':'guaranteed'}})
    assert session['execution']['nodes'][0]['routing']=={'resolverName':'known'}


def test_worker_routing_survives_runtime_ingestion_and_bounded_browser_projection():
    from amplifier_web.runtime import normalize_event
    from amplifier_web.browser_detail import compact
    kind, row=normalize_event({'type':'execution.event','event':{
        'id':'worker:c','kind':'worker','provider':'actual','model':'actual-model',
        'parentProvider':'parent','runId':'current',
        'routing':{'resolverName':'known','matrixSource':'user','api_key':'private',
            'selectionSource':'delegation_preferences'}}}, 'root')
    assert kind=='execution.event'
    assert 'api_key' not in row['routing']
    session={}
    ingest(session,row)
    browser=compact(session['execution']['nodes'][0], 'root', 'nodes', 512)
    assert browser['routing']=={'resolverName':'known','matrixSource':'user',
        'selectionSource':'delegation_preferences'}
    assert (browser['provider'],browser['model'],browser['parentProvider'],browser['runId'])==(
        'actual','actual-model','parent','current')
