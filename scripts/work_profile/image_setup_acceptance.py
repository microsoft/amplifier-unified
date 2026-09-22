"""Isolated ordinary-Work setup and fresh-session mount; no provider requests.

Run prepare and mount in separate processes so dependency installation cannot
mix old and newly installed modules in one Python interpreter. Synthetic API
credentials deliberately cannot make model/image calls. Source overrides are
review inputs, never changes to production or maintained branch-tracking URIs.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path


async def run(args):
    folder = args.output.expanduser().resolve()
    os.environ.update(AMPLIFIER_HOME=str(folder / 'shared'), AMPLIFIER_WEB_HOME=str(folder / 'app'),
                      AMPLIFIER_SESSION_STATE_HOME=str(folder / 'ownership'), WORK_IMAGE_FIXTURE_KEY='fixture-not-a-real-api-key')
    workspace = folder / 'workspace'
    from amplifier_web.setup import SetupManager
    from amplifier_web.bundles import BundleManager
    from amplifier_web.host.config import load_config
    from amplifier_web.host.session import prepare_dependencies, prepare_manager
    if args.phase == 'prepare':
        folder.mkdir(mode=0o700, parents=True, exist_ok=False)
        workspace.mkdir(mode=0o700)
        setup = SetupManager(folder / 'app')
        configuration = {'default_model': 'gpt-5.5', 'base_url': 'https://api.openai.com/v1',
            'image_generation': {'enabled': True, 'id': 'images', 'model': 'gpt-image-1-mini'}}
        result = await setup.perform('providers.save', {'workspace': str(workspace),
            'id': 'kept-chat-provider', 'module': 'provider-openai', 'config': configuration,
            'apiKeyEnv': 'WORK_IMAGE_FIXTURE_KEY', 'scope': 'global'})
        saved = result['providers'][0]
        assert saved['id'] == 'kept-chat-provider' and saved['config']['default_model'] == 'gpt-5.5'
        assert saved['config']['image_generation'] == configuration['image_generation']
        def override(settings):
            settings.setdefault('sources', {}).update(bundles={'work': str(args.work_source.resolve())},
                modules={'tool-image': str(args.tool_source.resolve()), 'provider-openai': str(args.provider_source.resolve())})
            # A newly saved provider may have no source declaration. Make the
            # reviewed local source explicit so prepare activates it instead of
            # accepting an already installed entry point from another revision.
            settings.setdefault('overrides', {})['provider-openai'] = {'source': str(args.provider_source.resolve())}
            return settings
        setup.store.update(workspace, 'global', override)
        bundles = BundleManager(folder / 'app', store=setup.store)
        await bundles.perform('bundles.add', {'workspace': str(workspace),
            'uri': 'work:behaviors/work-images.yaml', 'name': 'Work image tools', 'role': 'behavior'})
        config = load_config(workspace)
        assert config.active_bundle == 'work'
        assert 'work:behaviors/work-images.yaml' in config.app_bundles
        await prepare_dependencies(workspace, bundle='work')
        report = {'passed': True, 'providerJsonRoundTrip': True, 'ordinaryRoot': config.active_bundle,
                  'behaviorOverlay': True, 'providerRequests': 0, 'phase': 'prepare'}
    else:
        if not (folder / 'prepare.json').exists():
            raise ValueError('Run the prepare phase first.')
        selection = {'instance': 'kept-chat-provider', 'model': 'gpt-5.5'}
        session, runtime, mounted = await prepare_manager(workspace, bundle='work', selection=selection,
            report_dir=folder / 'mount-evidence', application_host='Isolated image setup acceptance')
        try:
            tools = session.coordinator.get('tools')
            capability = await tools['image_generate'].execute({'action': 'capabilities'})
            assert capability.success and capability.output['ready']
            assert capability.output['backendStatus']['model'] == 'gpt-image-1-mini'
            assert mounted['effective_selection'] == selection and mounted['root_bundle'] == 'work'
            assert 'load_skill' in tools
            skill = await tools['load_skill'].execute({'skill_name': 'imagegen'})
            assert skill.success
            report = {'passed': True, 'phase': 'mount', 'ordinaryRoot': mounted['root_bundle'],
                      'effectiveSelection': mounted['effective_selection'], 'imageSkillLoaded': True,
                      'capabilities': capability.output, 'providerRequests': 0,
                      'sessionId': mounted['session_id'], 'resumed': mounted['resumed']}
        finally:
            await session.cleanup()
    (folder / (args.phase + '.json')).write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['prepare', 'mount'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-source', type=Path, required=True)
    parser.add_argument('--provider-source', type=Path, required=True)
    parser.add_argument('--tool-source', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
