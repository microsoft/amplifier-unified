"""Voice-history positioning fixture; no provider requests."""
import tempfile
from pathlib import Path
from aiohttp import web
import chat_ui_server as fixture

async def main(home):
    app=await fixture.fixture.main(home)
    service=app['service'];session=service._session()
    session['messages']=[{'id':'voice-user','role':'user','text':'Show me the plan','via':'call','createdAt':10},
                         {'id':'voice-ack','role':'assistant','text':'I will work on that','via':'call','createdAt':11},
                         {'id':'voice-result','role':'assistant','text':'Here is your plan','via':'call','createdAt':20}]
    session['execution']={'turns':[{'id':'voice:first','startedAt':10.5,'endedAt':15,'phase':'completed'},
                                   {'id':'voice:second','startedAt':12,'phase':'running'},
                                   {'id':'voice:third','startedAt':13,'endedAt':19,'phase':'completed'}],
                          'nodes':[{'id':'tool-'+str(n),'kind':'tool','turnId':turn,'label':'Tool '+str(n),'phase':'completed'} for n,turn in enumerate(['voice:first','voice:second','voice:third'])]}
    routing={'modelInheritance':'bundle','resolverActive':True,'resolverName':'fixture-routing',
             'matrixSource':'user','crossProviderRestriction':'not_enforced','selectionSource':'delegation_preferences'}
    session['runtimeReport']={'delegationRouting':routing}
    session['execution']['nodes'].extend([
        {'id':'worker:fixture','kind':'worker','turnId':'voice:second','parentId':'tool-1','sessionId':'fixture-child',
         'label':'Fixture worker','provider':'anthropic','model':'child-model','parentProvider':'subscription',
         'routing':routing,'phase':'completed','summary':'Fixture task finished.'},
        {'id':'llm:fixture','kind':'llm','turnId':'voice:second','parentId':'worker:fixture','sessionId':'fixture-child',
         'label':'Model call','provider':'anthropic','model':'child-model','phase':'completed','usage':{'costType':'unavailable'}}])
    from amplifier_web.execution import refresh_usage
    refresh_usage(session['execution'])
    service._publish()
    return app

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-timeline-ui-') as tmp:
        web.run_app(main(Path(tmp)),host='127.0.0.1',port=8958,print=None)
