"""Two explicitly authorized Images API calls through the real tool and saved outputs.

No chat-model call, production settings change, credential copy, or automatic retry.
Install reviewed image tool/provider sources into the same isolated interpreter.
"""
import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml


async def run(args):
    from amplifier_core import AmplifierSession
    from amplifier_module_provider_openai import mount as mount_provider
    from amplifier_module_tool_image import mount as mount_images
    from amplifier_web.host.config import _load_keys, expand_environment
    from amplifier_web.service import AppService

    settings_path = args.settings.expanduser().resolve()
    settings = yaml.safe_load(settings_path.read_text()) or {}
    matches = [row for row in settings.get('config', {}).get('providers', [])
               if (row.get('id') or row.get('module')) == args.provider and row.get('module') == 'provider-openai']
    if len(matches) != 1:
        raise ValueError('Choose one configured ordinary provider-openai instance.')
    _load_keys(settings_path.with_name('keys.env'))
    configured = expand_environment(copy.deepcopy(matches[0].get('config', {})))
    endpoint = urlsplit(configured.get('base_url') or 'https://api.openai.com/v1')
    if endpoint.scheme != 'https' or endpoint.hostname != 'api.openai.com' or endpoint.path.rstrip('/') != '/v1' or endpoint.username or endpoint.password or endpoint.query:
        raise ValueError('This bounded runner requires the ordinary https://api.openai.com/v1 endpoint.')
    if not configured.get('api_key') or '${' in configured['api_key']:
        raise ValueError('The selected profile needs its ordinary configured Images API credential.')
    folder = args.output.expanduser().resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    workspace = folder / 'workspace'
    workspace.mkdir(mode=0o700)
    os.environ.update(AMPLIFIER_HOME=str(folder / 'shared'), AMPLIFIER_WEB_HOME=str(folder / 'app'))
    report = {'providerInstance': args.provider, 'endpointHost': endpoint.hostname, 'imageModel': args.image_model,
              'scenario': 'real image tool and shared output actions', 'checks': {}, 'passed': False,
              'modelVisualAcceptance': 'not tested', 'browserAcceptance': 'not tested',
              'accountIdentity': 'selected local profile; remote account identity unverified'}
    session = AmplifierSession({'session': {'orchestrator': {'module': 'fixture'}, 'context': {'module': 'fixture'}}})
    coordinator = session.coordinator
    coordinator.register_capability('session.working_dir', str(workspace))
    cleanup = None
    app = None
    try:
        cleanup = await mount_provider(coordinator, {
            'api_key': configured['api_key'], 'base_url': 'https://api.openai.com/v1',
            'image_generation': {'enabled': True, 'id': 'images', 'model': args.image_model}})
        await mount_images(coordinator, {'backend': 'images', 'allow_paid': True})
        tool = coordinator.get('tools')['image_generate']
        capability = await tool.execute({'action': 'capabilities'})
        report['capabilities'] = capability.output
        if not capability.output['ready']:
            raise RuntimeError('Image capability is not configured.')
        generated = await tool.execute({'action': 'generate', 'request_id': 'acceptance-original',
            'prompt': 'Create a simple flat illustration: one solid blue circle centered on a pure white background. No text, shadows or extra objects.',
            'size': '1024x1024', 'quality': 'low', 'background': 'opaque'})
        if not generated.success or generated.output.get('status') != 'completed':
            report['generationOutcome'] = generated.output
            raise RuntimeError('Generation did not complete; inspect its durable receipt without replaying it.')
        first = generated.output
        app = AppService(folder / 'app', workspace=workspace)
        await app.dispatch('session.create', {'title': 'Isolated image acceptance'})
        sid = app._session()['id']
        original = (await app.app_bridge('dispatch', {'action': 'outputs.attachImage', 'args': {
            'title': 'Generated original', 'receiptPath': first['receiptPath']}, 'id': 'attach-original'}, sid))['result']
        initial = await app.app_bridge('outputs.image.read', {'id': original['id'], 'sha256': original['sha256']}, sid)
        report['checks']['generated_exact_snapshot'] = hashlib.sha256(base64.b64decode(initial['_image'])).hexdigest() == first['artifact']['sha256']
        edited = await tool.execute({'action': 'edit', 'request_id': 'acceptance-edit',
            'prompt': 'Change only the blue circle to solid red. Preserve its circular shape, position, size and the plain white background. No text or extra objects.',
            'images': [{'path': first['artifact']['path'], 'sha256': first['artifact']['sha256']}],
            'size': '1024x1024', 'quality': 'low', 'background': 'opaque'})
        if not edited.success or edited.output.get('status') != 'completed':
            report['editOutcome'] = edited.output
            raise RuntimeError('Edit did not complete; inspect its durable receipt without replaying it.')
        second = edited.output
        saved = (await app.app_bridge('dispatch', {'action': 'outputs.attachImage', 'args': {
            'title': 'Edited image', 'receiptPath': second['receiptPath'], 'parentId': original['id']}, 'id': 'attach-edit'}, sid))['result']
        observed = await app.app_bridge('outputs.image.read', {'id': saved['id'], 'sha256': saved['sha256']}, sid)
        report['checks']['edited_exact_snapshot'] = hashlib.sha256(base64.b64decode(observed['_image'])).hexdigest() == second['artifact']['sha256']
        report['checks']['edit_parent_retained'] = saved['parentId'] == original['id'] and saved['version'] == 2
        report['checks']['original_unchanged'] = hashlib.sha256((workspace / first['artifact']['path']).read_bytes()).hexdigest() == first['artifact']['sha256']
        report['checks']['saved_receipt_read_without_replay'] = (await tool.execute({'action': 'status', 'request_id': 'acceptance-edit'})).output == second
        report['outputs'] = [original, saved]
        report['passed'] = all(report['checks'].values())
    except Exception as exc:
        # Never persist exception text from SDKs or configured credentials.
        report['errorType'] = type(exc).__name__
    finally:
        if app is not None:
            await app.close()
        if cleanup is not None:
            await cleanup()
        (folder / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'],
                      'errorType': report.get('errorType'), 'report': str(folder / 'report.json')}))
    return report['passed']


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--image-model', required=True)
    parser.add_argument('--settings', type=Path, default=Path.home() / '.amplifier/settings.yaml')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live authorizes two paid low-quality 1024x1024 image calls')
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == '__main__':
    main()
