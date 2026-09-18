"""Query a provider's public setup APIs without starting a conversation."""
from __future__ import annotations
import asyncio
import inspect
import json
import os
import sys

from amplifier_web.provider_environment import (
    close_provider,
    config_schema,
    construct_provider,
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


async def query(request):
    if request.get('source'):
        from amplifier_foundation import Bundle
        os.environ['AMPLIFIER_HOME']=request['registryHome']
        bundle=Bundle.from_dict({'bundle':{'name':'provider-setup'},'providers':[{'module':request['module'],'source':request['source']}]})
        await bundle.prepare(strict=True)
    cls=provider_class(request['module'])
    # Metadata discovery is deliberately configuration-free. This keeps schema
    # inspection offline and allows the materializer to decide which optional
    # references can be blank before the configured provider is constructed.
    schema_provider=construct_provider(cls,{})
    try:
        info=schema_provider.get_info()
        if inspect.isawaitable(info): info=await info
        schema=await config_schema(schema_provider, info=info)
    finally:
        await close_provider(schema_provider)
    if request['action']=='providers.schema':
        return {'info':public(info),'configSchema':public(schema)}
    config=materialize_provider_config(request.get('config',{}),schema)
    if request['module']=='provider-github-copilot' and config.get('github_token'):
        os.environ['COPILOT_AGENT_TOKEN']=config['github_token']
    provider=construct_provider(cls,config)
    async def invoke(name):
        value=getattr(provider,name)()
        return await value if inspect.isawaitable(value) else value
    try:
        info=public(await invoke('get_info'))
        result={'info':info,'configSchema':public(schema)}
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
        kind=type(exc).__name__
        hint='Check the saved credentials and endpoint, then retry.'
        if isinstance(exc,ImportError):hint='This provider module is not installed in the app runtime yet. Prepare a conversation with its bundle first.'
        result={'error':f'Provider setup failed ({kind}). {hint}'}
    output.write(json.dumps(result)+'\n');output.flush()

if __name__=='__main__':main()
