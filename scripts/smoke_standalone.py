"""Explicit opt-in live model check; uses one disposable conversation."""
import asyncio
import argparse
import json
from pathlib import Path
import tempfile
from amplifier_web.service import AppService
from amplifier_web.runtime import RuntimeManager

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default=str(Path.cwd()))
    parser.add_argument('--bundle', default='foundation')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='amplifier-standalone-check-') as folder:
        service = AppService(Path(folder), workspace=args.workspace)
        manager = RuntimeManager(service.app_bridge)
        service.runtime = manager
        await service.dispatch('session.create', {'title': 'Standalone integration check', 'bundle': args.bundle})
        sid = service.state['selectedSessionId']
        print('ISOLATED_SESSION', sid, flush=True)
        queue = service.subscribe()
        try:
            await service.dispatch('conversation.send', {'text': 'This is a bounded integration test. First use app_control list_actions exactly once. Then delegate one task to foundation:explorer: use bash pwd once to report the working directory, without editing anything or examining other files. After the worker reports, tell me the action count and directory, and say STANDALONE_OK. Do not launch any other workers.'}, command_id='standalone-live-smoke')
            previous = None
            async with asyncio.timeout(480):
                while True:
                    session = service._session(sid)
                    summary = (session['status'], session.get('activity', {}).get('label'), len(session['messages']), [(w.get('name'),w.get('status')) for w in session['workers']])
                    if summary != previous:
                        print(json.dumps(summary),flush=True);previous=summary
                    if session['status']=='error':
                        raise RuntimeError(session.get('error'))
                    if session['status']=='idle' and len(session['messages'])>1:
                        print('REPORT',json.dumps({key:session.get('runtimeReport',{}).get(key) for key in ['standalone','bundle','providers','resumed','capabilities']}),flush=True)
                        print('RESPONSE',session['messages'][-1]['text'],flush=True)
                        assert any(w.get('status')=='completed' for w in session['workers']), 'No completed child'
                        assert any(g.get('event')=='generation.finished' for g in session.get('generations',[])), 'No identified generation completion'
                        assert session.get('runtimeReport',{}).get('standalone') is True
                        from amplifier_web.host.storage import SessionStore
                        store = SessionStore()
                        children = []
                        for path in store.base_dir.glob('*/checkpoint.json'):
                            checkpoint = json.loads(path.read_text())
                            if checkpoint['metadata'].get('parent_id') == sid:
                                children.append(checkpoint)
                        assert len(children) == 1, 'Expected exactly one real child'
                        results = [message for message in children[0]['messages'] if message.get('role') == 'tool' and message.get('name') == 'bash']
                        assert len(results) == 1, 'Child must have called bash exactly once'
                        tool_result = json.loads(results[0]['content'])
                        assert tool_result['success'] and tool_result['output']['returncode'] == 0
                        assert tool_result['output']['stdout'].strip() == str(Path(args.workspace).resolve())
                        print('LIVE_SMOKE_PASS',flush=True)
                        break
                    await queue.get()
        finally:
            service.unsubscribe(queue)
            await service.close()

asyncio.run(main())
