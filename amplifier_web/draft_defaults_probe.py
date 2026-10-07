"""Inspect composed providers, without mounting hooks, tools, or a session."""
from __future__ import annotations
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()

async def query(request):
    from amplifier_web.host.config import load_config
    from amplifier_web.host.session import load_root_bundle
    from amplifier_web.provider_environment import close_provider, construct_provider, construct_schema_provider, config_schema, materialize_provider_config, provider_class
    from amplifier_web.provider_probe import public
    workspace=Path(request['workspace']).expanduser().resolve()
    existing=workspace
    while not existing.is_dir() and existing.parent!=existing:existing=existing.parent
    config=load_config(existing,home=request['home'],session_id=request.get('sessionId'),global_only=bool(request.get('globalOnly')))
    config.workspace=workspace
    identity=request.get('sessionId')
    chosen=request['bundle']
    selection=None
    if identity:
        from amplifier_web.session_files import sessions_dir, validate_id
        validate_id(identity)
        directory=sessions_dir(workspace)/identity
        metadata_path=directory/'metadata.json'
        if metadata_path.exists():
            metadata=json.loads(metadata_path.read_text())
            chosen=(metadata.get('bundle_name') or metadata.get('bundle') or chosen).removeprefix('bundle:')
        control_path=Path(request['home'])/'sessions'/identity/'control-state.json'
        if control_path.exists():selection=json.loads(control_path.read_text()).get('selection')
    _,bundle,_=await load_root_bundle(config,chosen,execution_workspace=workspace)
    if identity:
        edited=Path(request['home'])/'sessions'/identity/'configuration.json'
        if edited.exists():
            from amplifier_web.host.session import apply_runtime_plan
            bundle=apply_runtime_plan(bundle,json.loads(edited.read_text()),config,workspace)
    catalogs={}
    from amplifier_web.provider_catalog import ProviderCatalog, configuration_key, model_key
    cache=ProviderCatalog(Path(request['home'])/'cache'/'provider-catalogs.json',ttl=None)
    rows=[]
    for row in bundle.providers:
        if row.get('enabled') is False:continue
        from amplifier_web.host.session import module_source, is_snapshot
        source=module_source(config,is_snapshot(bundle),row['module'],row.get('source')) if request.get('catalog') else None
        shared=configuration_key(workspace,row['module'],row.get('config',{}),source,home=request['home']) if request.get('catalog') else None
        cached=cache.peek(model_key(shared)) if not request.get('refresh') else None
        if request.get('catalog') and cached and cached.get('providerMetadata'):
            metadata=cached['providerMetadata']
            identity=row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
            defaults=metadata.get('info',{}).get('defaults',{})
            rows.append({'id':identity,'module':row['module'],'sharedCatalogKey':shared,
                'info':{'display_name':metadata.get('info',{}).get('display_name'),'defaults':defaults},
                'effort':row.get('config',{}).get('reasoning_effort'), 'priority':row.get('config',{}).get('priority',100)})
            catalogs[shared]={'phase':'ready','models':cached.get('models',[]),'metadata':metadata,'supported':cached.get('modelsSupported',True)}
            continue
        if request.get('catalog') and source:
            from amplifier_foundation import Bundle
            preparation=Bundle.from_dict({'bundle':{'name':'conversation-provider-catalog'},'providers':[{'module':row['module'],'source':source}]})
            await preparation.prepare(strict=True)
        cls=provider_class(row['module'])
        schema_provider=construct_schema_provider(cls,row.get('config',{}))
        try:schema=await config_schema(schema_provider)
        finally:await close_provider(schema_provider)
        values=materialize_provider_config(row.get('config',{}),schema)
        provider=construct_provider(cls,values)
        try:
            info=provider.get_info()
            if inspect.isawaitable(info):info=await info
            info=public(info)
            info={key:info[key] for key in ('id','display_name','defaults') if key in info}
            schema={'fields':[field for field in schema.get('fields',[]) if field.get('id')=='reasoning_effort']}
            identity=row.get('instance_id') or row.get('id') or row['module'].removeprefix('provider-')
            priority=getattr(provider,'priority',getattr(provider,'config',{}).get('priority',100))
            row_result={'id':identity,'module':row['module'],'effort':values.get('reasoning_effort'),'info':info,'configSchema':public(schema),'priority':priority}
            if request.get('catalog'):
                metadata={'module':row['module'],'info':info,'configSchema':public(schema)}
                supported=callable(getattr(provider,'list_models',None))
                try:
                    models=provider.list_models() if supported else []
                    if inspect.isawaitable(models):models=await asyncio.wait_for(models,45)
                    catalogs[shared]={'phase':'ready','models':public(models),'metadata':metadata,'supported':supported}
                except Exception:
                    catalogs[shared]={'phase':'error','models':[],'metadata':metadata,'supported':supported}
                row_result.pop('configSchema',None)
                row_result['sharedCatalogKey']=shared
            rows.append(row_result)
        finally:await close_provider(provider)
    rows.sort(key=lambda row:row['priority'])
    first=rows[0] if rows else {}
    defaults=first.get('info',{}).get('defaults',{})
    return {**({'catalogs':catalogs,'selection':selection} if request.get('catalog') else {}),'providers':rows,'effective':{'instance':first.get('id'),'model':defaults.get('model') or defaults.get('default_model'),'effort':defaults.get('reasoning_effort') or first.get('effort')}}

def main():
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    try:result=asyncio.run(query(json.loads(sys.stdin.read())))
    except Exception as exc:result={'error':type(exc).__name__}
    output.write(json.dumps(result)+'\n');output.flush()
if __name__=='__main__':main()
