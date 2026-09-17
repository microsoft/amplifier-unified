"""Query a provider's public setup APIs without starting a conversation."""
from __future__ import annotations
import asyncio
import importlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import sys


def public(value):
    if hasattr(value, 'model_dump'): value=value.model_dump(mode='json')
    if isinstance(value, dict):
        if value.get('field_type')=='secret':value={key:item for key,item in value.items() if key not in {'default','value'}}
        return {key:public(item) for key,item in value.items()
                if not any(word in key.lower() for word in ('api_key','password','access_token','refresh_token','github_token','secret'))}
    if isinstance(value,(tuple,list)): return [public(item) for item in value]
    return value


def provider_class(module_id):
    module=None
    for entry in importlib.metadata.entry_points(group='amplifier.modules'):
        if entry.name==module_id:
            loaded=entry.load()
            module=importlib.import_module(loaded.__module__)
            break
    if module is None:
        module=importlib.import_module('amplifier_module_'+module_id.replace('-','_'))
    candidates=[getattr(module,name) for name in dir(module) if name.endswith('Provider') and not name.startswith('_')]
    candidates=[cls for cls in candidates if inspect.isclass(cls) and callable(getattr(cls,'get_info',None)) and not getattr(cls,'_is_protocol',False)]
    if not candidates: raise ValueError('No public provider setup class')
    return candidates[0]


async def query(request):
    if request.get('source'):
        from amplifier_foundation import Bundle
        os.environ['AMPLIFIER_HOME']=request['registryHome']
        bundle=Bundle.from_dict({'bundle':{'name':'provider-setup'},'providers':[{'module':request['module'],'source':request['source']}]})
        await bundle.prepare(strict=True)
    cls=provider_class(request['module'])
    config=request.get('config',{})
    # The constructor contract varies between providers. Bind the real config
    # and only the supported connection arguments; never substitute fake URLs.
    parameters=inspect.signature(cls).parameters
    kwargs={key:config[key] for key in parameters if key in config}
    if 'config' in parameters:kwargs['config']=config
    provider=cls(**kwargs)
    async def invoke(name):
        value=getattr(provider,name)()
        return await value if inspect.isawaitable(value) else value
    try:
        info=public(await invoke('get_info'))
        schema=public(await invoke('get_config_schema')) if callable(getattr(provider,'get_config_schema',None)) else {'fields':info.get('config_fields',[])}
        result={'info':info,'configSchema':schema}
        if request['action']!='providers.schema':
            if not callable(getattr(provider,'list_models',None)):raise ValueError('Provider does not expose model discovery')
            result['models']=public(await invoke('list_models'))
            if request['action']=='providers.test':
                result['test']={'reachable':True,'modelCount':len(result['models']),'method':'provider.list_models'}
        return result
    finally:
        close=getattr(provider,'close',None) or getattr(provider,'aclose',None)
        if callable(close):
            try:
                value=close()
                if inspect.isawaitable(value):await asyncio.wait_for(value,3)
            except Exception:pass


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
