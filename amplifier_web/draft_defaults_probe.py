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
    from amplifier_web.provider_environment import close_provider, construct_provider, config_schema, materialize_provider_config, provider_class
    from amplifier_web.provider_probe import public
    workspace=Path(request['workspace']).expanduser().resolve()
    existing=workspace
    while not existing.is_dir() and existing.parent!=existing:existing=existing.parent
    config=load_config(existing,home=request['home'],global_only=bool(request.get('globalOnly')))
    config.workspace=workspace
    _,bundle,_=await load_root_bundle(config,request['bundle'],execution_workspace=workspace)
    rows=[]
    for row in bundle.providers:
        if row.get('enabled') is False:continue
        cls=provider_class(row['module'])
        schema_provider=construct_provider(cls,{})
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
            rows.append({'id':identity,'module':row['module'],'effort':values.get('reasoning_effort'),'info':info,'configSchema':public(schema),'priority':priority})
        finally:await close_provider(provider)
    rows.sort(key=lambda row:row['priority'])
    first=rows[0] if rows else {}
    defaults=first.get('info',{}).get('defaults',{})
    return {'providers':rows,'effective':{'instance':first.get('id'),'model':defaults.get('model') or defaults.get('default_model'),'effort':defaults.get('reasoning_effort') or first.get('effort')}}

def main():
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    try:result=asyncio.run(query(json.loads(sys.stdin.read())))
    except Exception as exc:result={'error':type(exc).__name__}
    output.write(json.dumps(result)+'\n');output.flush()
if __name__=='__main__':main()
