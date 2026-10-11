"""One scoped Gemini generation after real automatic-provider discovery.

Run only with authorization for a paid image request, in an owned DTU. Supply
GOOGLE_API_KEY in a JSON object on stdin (never argv or a saved config). No chat
request, retries, production state, or account configuration changes occur.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys


async def main(folder):
    os.umask(0o077)
    credentials = json.load(sys.stdin)
    key = credentials['GOOGLE_API_KEY']
    folder.mkdir(parents=True, exist_ok=False)
    workspace = folder / 'workspace'
    workspace.mkdir()
    os.environ.update(AMPLIFIER_HOME=str(folder/'native'), AMPLIFIER_WEB_HOME=str(folder/'app'))
    from amplifier_core import AmplifierSession
    from amplifier_module_provider_gemini import mount as mount_provider
    from amplifier_module_tool_image import mount as mount_tool
    from amplifier_web.image_defaults import apply_defaults
    from amplifier_web.service import AppService

    plan = {'providers': [{'module': 'provider-gemini', 'id': 'scoped-gemini',
                          'config': {'api_key': key, 'priority': 1, 'default_model': 'gemini-flash-latest'}}]}
    selected = await apply_defaults(plan, {})
    assert selected == 'scoped-gemini', 'Automatic discovery did not select Gemini'
    assert plan['providers'][0]['config']['image_generation']['model'] == 'auto'
    session = AmplifierSession({'session': {'orchestrator': {'module': 'fixture'}, 'context': {'module': 'fixture'}}})
    coordinator = session.coordinator
    coordinator.register_capability('session.working_dir', str(workspace))
    cleanup = tool_cleanup = app = None
    report = {'passed': False, 'automaticProvider': selected, 'generationRequests': 0,
              'retries': 0, 'chatRequests': 0, 'scope': 'owned DTU; synthetic prompt and workspace'}
    try:
        cleanup = await mount_provider(coordinator, plan['providers'][0]['config'])
        tool_cleanup = await mount_tool(coordinator, {'backend': 'images', 'allow_paid': True})
        tool = coordinator.get('tools')['image_generate']
        capability = await tool.execute({'action': 'capabilities'})
        assert capability.success and capability.output['ready']
        report['generationRequests'] = 1
        generated = await tool.execute({'action': 'generate', 'request_id': 'automatic-gemini-once',
            'prompt': 'A simple flat illustration of one blue circle on a plain white background. No text or other objects.'})
        if not generated.success or generated.output.get('status') != 'completed':
            report['outcome'] = generated.output.get('status', 'unknown')
            raise RuntimeError('Image did not complete; preserve the receipt without replay.')
        result = generated.output
        app = AppService(folder/'app', workspace=workspace)
        await app.dispatch('session.create', {'title': 'Automatic Gemini image acceptance', 'workspace': str(workspace)})
        sid = app._session()['id']
        attached = (await app.app_bridge('dispatch', {'action': 'outputs.attachImage', 'args': {
            'title': 'Automatic Gemini result', 'receiptPath': result['receiptPath']}, 'id': 'attach-once'}, sid))['result']
        read = await app.app_bridge('outputs.image.read', {'id': attached['id'], 'sha256': attached['sha256']}, sid)
        assert read.get('success', True) is not False
        artifact = result['artifact']
        image = workspace / artifact['path']
        assert hashlib.sha256(image.read_bytes()).hexdigest() == artifact['sha256']
        report.update(passed=True, model=result['model'], artifact=artifact, attachedOutputId=attached['id'],
                      persistence='durable tool receipt and app image output', visualAcceptance='not scored')
    finally:
        if app: await app.close()
        if tool_cleanup: await tool_cleanup()
        if cleanup: await cleanup()
        (folder/'qualification.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]).absolute()))
