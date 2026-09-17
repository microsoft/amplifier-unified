"""Scoped module entries and bundle/module source overrides."""
from __future__ import annotations
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import signal
from .preferences import SettingsStore
from .host.config import load_config
from .setup import SetupManager,redact,safe_name,public_source
from .bundles import validate_uri,SECRET_KEYS

SECTIONS={'tools','hooks','providers','orchestrator','context'}

def source_uri(value,workspace):
    if not isinstance(value,str) or not value.strip():raise ValueError('A source is required.')
    value=value.strip()
    if value.startswith(('https://','git+https://')):return validate_uri(value)
    if value.startswith(('file://','/','./','../','~')):
        path=Path(value.removeprefix('file://')).expanduser()
        if not path.is_absolute():path=Path(workspace)/path
        path=path.resolve()
        if not path.exists():raise ValueError('The local source does not exist.')
        return str(path)
    raise ValueError('Use an HTTPS Git source or an explicit local path.')

class RegistryManager:
    def __init__(self,home,*,store=None,validation_command=None):
        self.home=Path(home);self.store=store or SettingsStore(home);self.validation_command=validation_command

    def rows(self,settings,scope):
        modules=[];config=settings.get('config',{})
        for section in ('tools','hooks','providers'):
            rows={}
            for row in settings.get('modules',{}).get(section,[])+config.get(section,[]):
                identity=row.get('id') or row.get('instance_id') or row['module']
                rows[identity]={**rows.get(identity,{}),**row}
            for identity,row in rows.items():
                patch=settings.get('overrides',{}).get(identity,settings.get('overrides',{}).get(row['module'],{}))
                modules.append({'id':identity,'module':row['module'],'section':section,'source':public_source(patch.get('source',row.get('source'))),
                    'config':redact(patch.get('config',row.get('config',{}))),'enabled':patch.get('enabled',row.get('enabled',True)),'scope':scope})
        for section,row in config.get('session',{}).items():
            if section in {'orchestrator','context'} and isinstance(row,dict) and row.get('module'):
                modules.append({'id':row['module'],'module':row['module'],'section':section,'source':public_source(row.get('source')),'config':redact(row.get('config',{})),'enabled':True,'scope':scope})
        sources=[{'kind':kind,'name':name,'source':public_source(source),'scope':scope} for kind,key in (('module','modules'),('bundle','bundles')) for name,source in settings.get('sources',{}).get(key,{}).items()]
        return {'modules':modules,'sources':sources,'scope':scope}

    def protect(self,value,old,identity,updates,path=()):
        if not isinstance(value,dict):raise ValueError('Module configuration must be an object.')
        result={}
        for key,item in value.items():
            normalized=key.lower().replace('-','_')
            secret=normalized in SECRET_KEYS or normalized.endswith(('_api_key','_token','_secret','_password'))
            previous=old.get(key) if isinstance(old,dict) else None
            if secret and item in ('[REDACTED]','<redacted>'):
                if previous is not None:result[key]=previous
            elif secret and item and not (isinstance(item,str) and re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}',item)):
                if not isinstance(item,str):raise ValueError('Secret configuration fields must be strings.')
                env='AMPLIFIER_'+re.sub('[^A-Za-z0-9]','_',identity+'_'+'_'.join((*path,key))).upper()
                updates[env]=item;result[key]='${'+env+'}'
            elif isinstance(item,dict):result[key]=self.protect(item,previous,identity,updates,(*path,key))
            elif isinstance(item,list):
                result[key]=[self.protect(v,previous[i] if isinstance(previous,list) and i<len(previous) else {},identity,updates,(*path,key,str(i))) if isinstance(v,dict) else v for i,v in enumerate(item)]
            else:result[key]=copy.deepcopy(item)
        return result

    async def perform(self,action,args):
        workspace=args.get('workspace') or str(Path.cwd());scope=args.get('scope','global')
        settings=self.store.read(workspace,scope)
        if action in {'modules.list','sources.list'}:return self.rows(settings,scope)
        if action=='modules.validate':return await self.validate(args,workspace,scope)
        def mutate(settings):
            if action.startswith('sources.'):
                kind=args['kind']
                if kind not in {'module','bundle'}:raise ValueError('Choose module or bundle source.')
                name=safe_name(args['name']);key='modules' if kind=='module' else 'bundles'
                values=settings.setdefault('sources',{}).setdefault(key,{})
                if action=='sources.save':values[name]=source_uri(args['source'],workspace)
                elif action=='sources.remove':values.pop(name,None)
                else:raise ValueError('Unknown source operation.')
                return
            section=args['section']
            if section not in SECTIONS:raise ValueError('Unknown module section.')
            module=safe_name(args.get('module') or args.get('id',''))
            identity=safe_name(args.get('id') or module)
            config=settings.setdefault('config',{})
            if section in {'orchestrator','context'}:
                values=config.setdefault('session',{})
                previous=values.get(section,{})
                if action=='modules.remove':values.pop(section,None);return
            else:
                values=config.setdefault(section,[])
                previous=next((row for row in values if (row.get('id') or row.get('instance_id') or row['module'])==identity),{})
                if action=='modules.remove':
                    # Tombstones also disable an inherited root-bundle module.
                    settings.setdefault('overrides',{}).setdefault(identity,{})['enabled']=False
                    values[:]=[row for row in values if (row.get('id') or row.get('instance_id') or row['module'])!=identity]
                    legacy=settings.setdefault('modules',{}).get(section,[])
                    legacy[:]=[row for row in legacy if (row.get('id') or row.get('instance_id') or row['module'])!=identity]
                    return
            if action!='modules.save':raise ValueError('Unknown module operation.')
            enabled=args.get('enabled',True)
            if type(enabled) is not bool:raise ValueError('Enabled must be true or false.')
            if section in {'orchestrator','context'} and not enabled:raise ValueError('A session needs its engine and context; remove the override to restore its bundle default.')
            updates={}
            row={'module':module,'config':self.protect(args.get('config',{}),previous.get('config',{}),identity,updates)}
            if args.get('source'):row['source']=source_uri(args['source'],workspace)
            elif previous.get('source'):row['source']=previous['source']
            if section in {'orchestrator','context'}:values[section]=row
            else:
                row['id']=identity
                index=next((i for i,item in enumerate(values) if (item.get('id') or item.get('instance_id') or item['module'])==identity),None)
                if index is None:values.append(row)
                else:values[index]=row
                settings.setdefault('overrides',{}).setdefault(identity,{})['enabled']=enabled
            if updates:SetupManager(self.home,store=self.store)._keys(updates)
        updated=self.store.update(workspace,scope,mutate)
        return {**self.rows(updated,scope),'takesEffect':'new_sessions'}

    async def validate(self,args,workspace,scope):
        settings=load_config(workspace,home=self.home).settings
        rows=self.rows(settings,'effective')['modules']
        row=next((row for row in rows if row['id']==args['id'] and row['section']==args['section']),None)
        if not row:raise ValueError('Choose a configured module to validate.')
        if not row.get('source'):row['source']=settings.get('sources',{}).get('modules',{}).get(row['module'])
        if self.validation_command:command=list(self.validation_command)
        else:
            from .runtime import RuntimeManager
            command=RuntimeManager()._command()[:-1]+[str(Path(__file__).with_name('module_validate.py'))]
            if args.get('behavioral'):command=command[:2]+['--with','pytest','--with','pytest-asyncio']+command[2:]
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,env={**os.environ,'AMPLIFIER_WEB_HOME':str(self.home)})
        try:
            # Structural validators do not need credentials from browser drafts.
            payload={k:row[k] for k in ('id','module','section','source') if k in row}
            payload['behavioral']=bool(args.get('behavioral',False))
            output,_=await asyncio.wait_for(process.communicate((json.dumps(payload)+'\n').encode()),300)
            result=None
            for line in output.decode(errors='replace').splitlines():
                try:value=json.loads(line)
                except ValueError:continue
                if isinstance(value,dict) and value.get('type')=='module.validation':result=value
            if result is None:raise ValueError('Module validation did not return a result. Check its source and dependencies.')
            return {'validation':{k:v for k,v in result.items() if k!='type'}}
        finally:
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),3)
                except TimeoutError:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.wait()
