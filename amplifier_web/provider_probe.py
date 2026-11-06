"""Query a provider's public setup APIs without starting a conversation."""
from __future__ import annotations
import asyncio
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


async def discovered_models(provider):
    """Optional versioned adapter seam; list_models may legally be static.

    The independently released provider owns API access and provenance. Unified
    never infers discovery from a module name or repairs it with credential HTTP.
    """
    method = getattr(provider, 'discover_models', None)
    if not callable(method):
        return {'models': [], 'modelsSupported': False, 'modelsProvenance': 'unsupported'}
    result = method()
    if inspect.isawaitable(result):
        result = await result
    if (not isinstance(result, dict) or result.get('version') != 1
            or result.get('source') not in {'provider-api', 'unsupported'}
            or not isinstance(result.get('models'), list)):
        raise ValueError('Invalid provider discovery provenance')
    models = public(result['models'])
    if result['source'] == 'unsupported' and models:
        raise ValueError('Unsupported discovery cannot supply model choices')
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] for row in models):
        raise ValueError('Invalid provider model IDs')
    return {'models': models, 'modelsSupported': result['source'] == 'provider-api',
            'modelsProvenance': result['source']}


class ProbeFailure(ValueError):
    def __init__(self, module, stage, cause):
        self.diagnostic = {'module': module, 'stage': stage, 'category': type(cause).__name__[:80]}
        super().__init__(f'Connection discovery failed at {stage} ({type(cause).__name__[:80]}). Check this connection and refresh to retry.')


async def query(request):
    progress = {'stage': 'import'}
    try:
        return await _query(request, progress)
    except Exception as exc:
        raise ProbeFailure(request.get('module'), progress['stage'], exc) from None


async def _query(request, progress):
    source=request.get('source')
    if not source:
        try:provider_class(request['module'])
        except ImportError:source=request.get('fallbackSource')
    if source:
        progress['stage'] = 'preparation'
        from amplifier_foundation import Bundle
        os.environ['AMPLIFIER_HOME']=request['registryHome']
        bundle=Bundle.from_dict({'bundle':{'name':'provider-setup'},'providers':[{'module':request['module'],'source':source}]})
        await bundle.prepare(strict=True)
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
    config=materialize_provider_config(request.get('config',{}),schema)
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
        if request['action'] == 'providers.models':
            return {**result, **await discovered_models(provider)}
        supported=callable(getattr(provider,'list_models',None))
        result['modelsSupported']=supported
        if not supported and request['action']=='providers.test':raise ValueError('Provider does not expose model discovery')
        result['models']=public(await invoke('list_models')) if supported else []
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
        request=json.loads(sys.stdin.read())
        result=asyncio.run(query(request))
    except Exception as exc:
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
