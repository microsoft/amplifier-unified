"""Query a provider's public setup APIs without starting a conversation."""
from __future__ import annotations
import asyncio
from collections.abc import Mapping
import inspect
import json
import os
import sys

# This file is launched by path in the isolated runtime, where the host package
# is not installed. Expose only app code, never the host's site-packages.
if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()

from amplifier_web.provider_environment import (
    close_provider,
    config_schema,
    construct_provider,
    construct_schema_provider,
    materialize_provider_config,
    provider_class,
)


def public(value):
    if hasattr(value, 'model_dump'): value=value.model_dump(mode='json')
    if isinstance(value, dict):
        if value.get('field_type')=='secret': return {key:public(item) for key,item in value.items() if key not in {'default','value'}}
        return {key:public(item) for key,item in value.items()
                if not any(word in key.lower() for word in ('api_key','password','access_token','refresh_token','github_token','secret'))}
    if isinstance(value,(tuple,list)): return [public(item) for item in value]
    return value


async def model_list_info(provider):
    """Read optional facts about the canonical list_models API, never list twice.

    Legacy list_models implementations may be static or fall back on errors.
    Neither their name nor selector support establishes strict API provenance.
    Unknown metadata versions remain unverified, without guessing new semantics.
    """
    method = getattr(provider, 'get_model_list_info', None)
    if not callable(method) or not callable(getattr(provider, 'list_models', None)):
        return None
    result = method()
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise ValueError('Invalid provider model-list metadata')
    if type(result.get('version')) is not int or result['version'] != 1:
        return None
    if result.get('source') not in {'provider-api', 'configured', 'static', 'simulated', 'unsupported'}:
        return None
    return public(dict(result))


async def listed_models(provider, *, filters=None, require_api=False):
    """Consume canonical listing with provider-owned defaults or explicit scope.

    No-argument calls preserve provider configuration/defaults. Only advertised
    selectors receive a keyword; an explicit empty mapping is not replaced by
    defaults. Unified owns caching, not provider recommendation algorithms.
    """
    info = await model_list_info(provider)
    if require_api and (info is None or info['source'] != 'provider-api'):
        from amplifier_web.probe_diagnostics import HostProbeFailure
        raise HostProbeFailure('not-verifiable')
    if info is None or info['source'] == 'unsupported':
        return {'models': [], 'modelsSupported': False, 'modelsProvenance': 'unsupported'}
    kwargs = {}
    if filters is not None:
        if not isinstance(filters, Mapping) or info.get('filters_keyword') != 'filters':
            raise ValueError('Provider does not advertise the requested model filters')
        kwargs['filters'] = dict(filters)
    result = provider.list_models(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, list):
        raise ValueError('Invalid provider model list')
    models = public(result)
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] for row in models):
        raise ValueError('Invalid provider model IDs')
    return {'models': models, 'modelsSupported': True,
            'modelsProvenance': info['source'], 'modelListInfo': info}


class ProbeFailure(ValueError):
    def __init__(self, module, stage, cause):
        self.diagnostic = {'module': module, 'stage': stage, 'category': type(cause).__name__[:80]}
        super().__init__(f'Connection discovery failed at {stage} ({type(cause).__name__[:80]}). Check this connection and refresh to retry.')


async def query(request):
    progress = {'stage': 'import'}
    from amplifier_web.probe_diagnostics import HostProbeFailure
    try:
        return await _query(request, progress)
    except HostProbeFailure:
        raise
    except Exception as exc:
        if request.get('action') == 'suggestion.complete':
            from amplifier_web.suggestion_provider import failure, FAILURE_STAGES
            stage = progress['stage']
            return {'suggestion': failure(exc, stage if stage in FAILURE_STAGES else 'construction')}
        raise ProbeFailure(request.get('module'), progress['stage'], exc) from None


async def _query(request, progress):
    # Preparation below points AMPLIFIER_HOME at the registry for this process.
    # Read the provider's state-file paths first: a test message is a model
    # call, and its files belong in the shared home, as in a chat.
    from amplifier_web.provider_state_files import state_file_defaults
    state_files=state_file_defaults(request['module'])
    source=request.get('source')
    if request['action'] == 'suggestion.complete':
        # Utility requests use installed runtime providers only. Never clone,
        # install or resolve a different source merely because a viewer polls.
        if source and source != request.get('fallbackSource'):
            return {'suggestion': {'status': 'unsupported', 'suggestion': None,
                                   'usage': None, 'cost': None,
                                   'reason': 'custom_source_not_prepared'}}
        source = None
    if not source:
        try:provider_class(request['module'])
        except ImportError:
            if request['action'] == 'suggestion.complete':
                return {'suggestion': {'status': 'unsupported', 'suggestion': None,
                                       'usage': None, 'cost': None}}
            source=request.get('fallbackSource')
    if source:
        progress['stage'] = 'preparation'
        from amplifier_foundation import Bundle
        os.environ['AMPLIFIER_HOME']=request['registryHome']
        from amplifier_web.source_imports import prepare
        bundle=Bundle.from_dict({'bundle':{'name':'provider-setup'},'providers':[{'module':request['module'],'source':source}]})
        await prepare(bundle,strict=True)
    progress['stage'] = 'import'
    cls=provider_class(request['module'])
    # Metadata gets no credentials/config, except the saved endpoint when the
    # public constructor requires it. No model-list or inference call is made.
    progress['stage'] = 'schema'
    schema_provider=construct_schema_provider(cls,request.get('config',{}))
    try:
        info=schema_provider.get_info()
        if inspect.isawaitable(info): info=await info
        schema=await config_schema(schema_provider, info=info)
        image_info = None
        if callable(getattr(schema_provider, 'get_image_generation_info', None)):
            image_info = schema_provider.get_image_generation_info()
            if inspect.isawaitable(image_info): image_info = await image_info
    finally:
        await close_provider(schema_provider)
    if request['action']=='providers.schema':
        return {'info':public(info),'configSchema':public(schema),'imageGeneration':public(image_info)}
    progress['stage'] = 'configuration'
    config = request.get('config', {})
    if request['action'] == 'suggestion.complete':
        from amplifier_web.suggestion_provider import utility_config
        config = utility_config(config, request.get('model'), request.get('effort'))
    config=materialize_provider_config(config,schema)
    config={**state_files,**config}
    if request['module']=='provider-github-copilot' and config.get('github_token'):
        os.environ['COPILOT_AGENT_TOKEN']=config['github_token']
    progress['stage'] = 'construction'
    provider=construct_provider(cls,config)
    async def invoke(name):
        value=getattr(provider,name)()
        return await value if inspect.isawaitable(value) else value
    try:
        progress['stage'] = 'metadata'
        info=public(await invoke('get_info'))
        result={'info':info,'configSchema':public(schema),'imageGeneration':public(image_info)}
        if request['action'] == 'suggestion.complete':
            from amplifier_web.suggestion_provider import complete
            # The host rechecks private client/source/config authority after all
            # construction awaits and before this one completion dispatch.
            if not callable(request.get('_admit')) or not await request['_admit']():
                return {'suggestion': {'status': 'stale', 'suggestion': None, 'usage': None, 'cost': None}}
            return {'suggestion': await complete(provider, config, request.get('model'),
                                                 request['transcript'], info, progress=progress,
                                                 effort=request.get('effort'),
                                                 **({'prompt': request['prompt']} if 'prompt' in request else {}))}
        if request['action'] == 'providers.imageModels':
            supported = callable(getattr(provider, 'list_image_models', None)) and isinstance(image_info, dict)
            result['imageModelsSupported'] = supported
            result['imageModels'] = public(await invoke('list_image_models')) if supported else []
            return result
        if request['action'] == 'naming.complete':
            from amplifier_web.provider_test import naming_completion
            result['naming'] = await naming_completion(provider, config, request.get('model'), request['prompt'])
            return result
        if request['action'] == 'providers.testMessage':
            from amplifier_web.provider_test import test_message
            result['messageTest'] = await test_message(provider, config, request.get('model'), info)
            return result
        progress['stage'] = 'models'
        result.update(await listed_models(provider, require_api=request['action']=='providers.test'))
        result['supports'] = {'test': result.get('modelsProvenance') == 'provider-api'}
        if request['action']=='providers.test':
            result['test']={'reachable':True,'modelCount':len(result['models']),'method':'provider.list_models'}
        return result
    finally:
        await close_provider(provider)


def main():
    # Protect the protocol from provider prints, including native SDK stdout.
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    try:
        request=json.loads(sys.stdin.readline())
        if request.get('action') == 'suggestion.complete':
            async def admit():
                output.write('{"suggestionReady":true}\n')
                output.flush()
                raw = await asyncio.to_thread(sys.stdin.readline, 1024)
                return json.loads(raw) == {'admitted': True}
            request['_admit'] = admit
        from amplifier_web.probe_git import query as owned_query
        result=asyncio.run(owned_query(query(request)))
    except Exception as exc:
        from amplifier_web.probe_diagnostics import HostProbeFailure
        if isinstance(exc, HostProbeFailure):
            output.write(json.dumps({'error':exc.public_message, 'publicFailureCode':exc.code,
                                     'diagnostic':exc.diagnostic})+'\n');output.flush()
            return
        if isinstance(exc, ProbeFailure):
            result={'error':str(exc),'diagnostic':exc.diagnostic,
                    'authenticationRequired':exc.diagnostic['category']=='AuthenticationError'}
            output.write(json.dumps(result)+'\n');output.flush()
            return
        kind=type(exc).__name__
        hint='Check the saved credentials and endpoint, then retry.'
        if isinstance(exc,ImportError):hint='This provider module is not installed in the app runtime yet. Prepare a conversation with its bundle first.'
        result={'error':f'Provider setup failed ({kind}). {hint}',
                'authenticationRequired':kind == 'AuthenticationError'}
    output.write(json.dumps(result)+'\n');output.flush()

if __name__=='__main__':main()
