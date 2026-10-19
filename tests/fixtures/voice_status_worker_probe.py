"""Actual Core/Foundation subprocess, held local provider, captured voice wire.

No network model/audio or real user data. The baseline mode retains 0.20.176's
extra status input and verifies the same original worker completes without replay.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def fixture(root):
    root = Path(root)
    workspace = root/'workspace'; workspace.mkdir(parents=True, exist_ok=True)
    provider = root/'provider/amplifier_module_provider_voicefixture'; provider.mkdir(parents=True, exist_ok=True)
    gates = root/'gates'; gates.mkdir(exist_ok=True)
    provider.joinpath('__init__.py').write_text('GATES = '+repr(str(gates))+'''\nimport asyncio, json, os
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock
class Provider:
    name='voicefixture'
    def get_info(self): return ProviderInfo(id='voicefixture', display_name='Scripted voice review', defaults={'model':'fixture'})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        with (Path(GATES)/'calls.jsonl').open('a') as stream:
            stream.write(json.dumps({'pid':os.getpid()})+'\\n')
        if not (Path(GATES)/'release').exists():
            (Path(GATES)/'entered').touch()
            try:
                while not (Path(GATES)/'release').exists(): await asyncio.sleep(.02)
            except asyncio.CancelledError:
                (Path(GATES)/'cancelled').touch()
                raise
        return ChatResponse(content=[TextBlock(text='Verified scripted worker result.')])
async def mount(coordinator, config=None): await coordinator.mount('providers', Provider(), name='voicefixture')
''')
    from amplifier_module_context_simple import __file__ as context
    plan = {'bundle': {'name': 'voice-review-fixture'},
            'session': {'orchestrator': {'module': 'loop-live'},
                        'context': {'module': 'context-simple', 'source': str(Path(context).parent.parent)}},
            'providers': [{'module': 'provider-voicefixture', 'source': str(provider.parent)}]}
    bundle=root/'fixture.yaml'; bundle.write_text(json.dumps(plan))
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    settings=root/'native/settings.yaml'; settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({'bundle': {'active':str(bundle), 'app':[]},
                                   'web_bundles': {'excluded':list(DEFAULT_BEHAVIORS)}}))
    os.environ.update(AMPLIFIER_HOME=str(root/'native'), AMPLIFIER_WEB_HOME=str(root/'app'),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'), AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'))
    return workspace, bundle, gates


class Socket:
    def __init__(self): self.closed=False; self.sent=[]
    async def send_json(self, event): self.sent.append(event)
    async def close(self): self.closed=True


async def until(predicate, seconds=45):
    async with asyncio.timeout(seconds):
        while not predicate(): await asyncio.sleep(.02)


async def run(root, baseline=False):
    workspace, bundle, gates = fixture(root)
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.service import AppService
    from amplifier_web.management import Management
    from amplifier_web.voice import VoiceCall, VoiceService
    runtime = RuntimeManager(command=[sys.executable, str(ROOT/'amplifier_web/runtime_worker.py')], startup_timeout=45)
    service = AppService(Path(root)/'app', runtime=runtime, workspace=workspace)
    service.management=Management(service); runtime.app_bridge=service.app_bridge; runtime.home=service.data_dir
    await service.dispatch('session.create', {'title':'Voice pacing native proof', 'bundle':str(bundle)})
    sid=service.state['selectedSessionId']
    manager=VoiceService(service, http=object()); call=VoiceCall(manager, sid); manager.call=call
    call.id='native-proof'; call.socket=Socket()
    await service.set_voice_status({'id':call.id, 'sessionId':sid, 'status':'connected'})
    tasks=[]
    try:
        await call.record('user', 'Hold the scripted worker.', 'work')
        tasks.append(asyncio.create_task(call.delegate_live('work')))
        await until(lambda:(gates/'entered').exists())
        assert not tasks[0].done() and not call.socket.sent
        worker_pid=runtime.workers[sid]['process'].pid
        await call.record('user', 'Are you still working?', 'status')
        status=asyncio.create_task(call.delegate_live('status')); tasks.append(status)
        done, _ = await asyncio.wait([status], timeout=1)
        assert bool(done) is not baseline
        if not baseline:
            assert len((gates/'calls.jsonl').read_text().splitlines()) == 1
            assert 'still working' in json.dumps(call.socket.sent)
            assert not any('Verified scripted' in json.dumps(row) for row in call.socket.sent)
            assert not any('voice:native-proof:status' in json.dumps(row) for row in service._session(sid)['messages'])
        call.final.set(); await call.close()
        assert not tasks[0].done() and not (gates/'cancelled').exists()
        before_close=len(call.socket.sent)
        (gates/'release').touch()
        await asyncio.wait_for(asyncio.gather(*tasks), 45)
        await until(lambda:any(row.get('event')=='generation.finished' and 'voice:native-proof:work' in row.get('input_ids', []) for row in service._session(sid).get('generations', [])))
        generations=service._session(sid)['generations']
        calls=len((gates/'calls.jsonl').read_text().splitlines())
        assert calls == (2 if baseline else 1), calls
        assert len(call.socket.sent) == before_close
        assert not (gates/'cancelled').exists()
        return {'baseline':baseline, 'workerPid':worker_pid, 'workerHeld':True,
                'immediateStatus':bool(done), 'providerCalls':calls, 'acceptedWorkSurvivesHangup':True,
                'exactWorkGeneration':True, 'statusSubmittedWork':baseline,
                'wire':call.socket.sent, 'generationIds':[row.get('generation_id') for row in generations]}
    finally:
        (gates/'release').touch()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
        assert not runtime.workers


if __name__=='__main__':
    result=asyncio.run(run(Path(sys.argv[1]), '--baseline' in sys.argv))
    print(json.dumps(result))
