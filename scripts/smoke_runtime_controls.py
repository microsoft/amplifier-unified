"""Opt-in model + real child check of standalone controls and public execution accounting."""
import asyncio
import argparse
import json
from pathlib import Path
import tempfile

from amplifier_web.service import AppService
from amplifier_web.runtime import RuntimeManager


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--bundle',default='anchors-amp-dev')
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='amplifier-controls-check-') as folder:
        service=AppService(Path(folder),workspace=args.workspace)
        manager=RuntimeManager(service.app_bridge)
        service.runtime=manager
        await service.dispatch('session.create',{'title':'Runtime controls integration check','bundle':args.bundle})
        sid=service.state['selectedSessionId']
        print('ISOLATED_SESSION',sid,flush=True)
        queue=service.subscribe()
        try:
            session=service._session(sid)
            await manager.start(session,service.on_runtime_event)
            configured=await manager.control(sid,'configuration.inspect')
            resources=await manager.control(sid,'configuration.exportResources')
            print('CONFIGURATION',json.dumps({'moduleCount':len(configured['modules']),
                'agents':len(configured['plan'].get('agents',{})),'resources':len(resources['context']),
                'namespaces':len(resources['namespaces']),'warnings':resources['warnings']}),flush=True)
            await service.dispatch('conversation.send',{'text':'This is one bounded integration test. First use app_control list_actions exactly once. Then delegate exactly one task to foundation:explorer: use bash pwd exactly once, report the working directory, and do not edit anything or inspect other files. Wait for that worker to finish, then report its directory and say TELEMETRY_OK. Do not spawn any other agents.'},command_id='runtime-controls-smoke')
            previous=None
            async with asyncio.timeout(600):
                while True:
                    session=service._session(sid)
                    nodes=session.get('execution',{}).get('nodes',[])
                    summary=(session['status'],session.get('activity',{}).get('label'),len(nodes),len(session['messages']))
                    if summary != previous:
                        print(json.dumps(summary),flush=True)
                        previous=summary
                    if session['status']=='error':
                        raise RuntimeError(session.get('error'))
                    if session['status']=='idle' and len(session['messages'])>1:
                        break
                    await queue.get()
            usage=await manager.control(sid,'usage.inspect')
            nodes=session['execution']['nodes']
            Path(args.report).write_text(json.dumps({'sessionId':sid,'execution':session['execution'],
                'runtimeUsage':usage,'response':session['messages'][-1]['text']},indent=2))
            children=[row for row in nodes if row['kind']=='worker' and row['sessionId']!=sid]
            assert len(children)==1, 'Expected one real child execution node'
            child=children[0]
            parent=next(row for row in nodes if row['id']==child['parentId'])
            assert parent['kind']=='tool' and parent['label']=='delegate'
            assert any(row['kind']=='tool' and row['label']=='bash' and row['parentId']==child['id'] for row in nodes)
            calls=[row for row in nodes if row['kind']=='llm' and row.get('phase')=='completed']
            assert calls and all(row.get('usage',{}).get('totalTokens',0)>0 for row in calls)
            assert any(row['sessionId']==sid for row in calls)
            assert any(row['sessionId']==child['sessionId'] for row in calls)
            total=sum(row['usage']['totalTokens'] for row in calls)
            assert session['execution']['aggregateUsage']['totalTokens']==total
            assert usage['usage']['totalTokens']==total
            assert sum(turn['aggregateUsage']['totalTokens'] for turn in session['execution']['turns'])==total
            report={'sessionId':sid,'standalone':session.get('runtimeReport',{}).get('standalone'),
                    'execution':session['execution'],'response':session['messages'][-1]['text'],
                    'configuration':{'moduleCount':len(configured['modules']),'resourceCount':len(resources['context']),
                                     'namespaceCount':len(resources['namespaces']),'warnings':resources['warnings']}}
            Path(args.report).write_text(json.dumps(report,indent=2))
            print('TELEMETRY_SMOKE_PASS',json.dumps({'calls':len(calls),'tokens':total,
                'pricedCalls':sum('costUsd' in row.get('usage',{}) for row in calls),'report':args.report}),flush=True)
        finally:
            service.unsubscribe(queue)
            await service.close()

asyncio.run(main())
