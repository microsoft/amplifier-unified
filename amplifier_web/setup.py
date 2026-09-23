"""Provider and routing editors for shared Amplifier configuration."""
from __future__ import annotations
import copy
import asyncio
import json
import signal
import uuid
import time
from urllib.parse import urlsplit,urlunsplit
import os
from pathlib import Path
import re
import shlex
import yaml
from filelock import FileLock
from .preferences import SettingsStore
from .shared_settings import atomic_write, routing_dirs, overlay
from .host.config import load_config, write_private
from .bundles import SECRET_KEYS, validate_uri

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')

ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
PROVIDER_ENV = {
    'provider-openai': ('OPENAI_API_KEY',),
    'provider-anthropic': ('ANTHROPIC_API_KEY',),
    'provider-gemini': ('GOOGLE_API_KEY','GEMINI_API_KEY'),
    'provider-github-copilot': ('GITHUB_TOKEN','COPILOT_AGENT_TOKEN','COPILOT_GITHUB_TOKEN','GH_TOKEN'),
}

def credential_field(module):
    return 'github_token' if module=='provider-github-copilot' else 'api_key'

def environment_credential(module,raw=None,env_var=None):
    raw=raw or {}
    defaults=PROVIDER_ENV.get(module,())
    value=raw.get(credential_field(module))
    reference=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value) if isinstance(value,str) else None
    if env_var is not None and (not isinstance(env_var,str) or not ENV_NAME.fullmatch(env_var)):
        raise ValueError('Use an environment variable name containing letters, numbers and underscores, starting with a letter or underscore.')
    chosen=env_var or (reference.group(1) if reference else next((name for name in defaults if os.environ.get(name)),defaults[0] if defaults else ''))
    return {'module':module,'field':credential_field(module),'defaultEnvVar':defaults[0] if defaults else '',
            'alternatives':list(defaults[1:]),'envVar':chosen,'available':bool(chosen and os.environ.get(chosen)),
            'explicit':bool(reference),'hasStoredKey':bool(value and not reference),
            'supported':module!='provider-openai-chatgpt'}

def safe_name(value):
    if not isinstance(value,str) or not NAME.fullmatch(value) or '..' in value:
        raise ValueError('Use a name containing letters, numbers, dots, dashes, or underscores.')
    return value

def redact(value):
    if isinstance(value,dict):
        return {k: ('[REDACTED]' if k.lower().replace('-','_') in SECRET_KEYS or k.lower().endswith(('_key','_token','_secret','_password')) else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    return value

def public_source(value):
    if isinstance(value,str) and value.startswith(('https://','http://','git+https://')):
        parsed=urlsplit(value.removeprefix('git+'))
        if parsed.username or parsed.password or parsed.query:
            return ('git+' if value.startswith('git+') else '')+urlunsplit((parsed.scheme,parsed.hostname or '',parsed.path,'',parsed.fragment))
    return value


def validate_matrix(value):
    if not isinstance(value,dict) or not isinstance(value.get('roles'),dict):
        raise ValueError('The routing matrix must contain a roles mapping.')
    for required in ('general','fast'):
        if required not in value['roles']: raise ValueError('The routing matrix requires the '+required+' role.')
    for role,row in value['roles'].items():
        safe_name(role)
        if not isinstance(row,dict) or not isinstance(row.get('description'),str) or not isinstance(row.get('candidates'),list) or not row['candidates']:
            raise ValueError('Each role needs a description and at least one candidate.')
        for candidate in row['candidates']:
            if not isinstance(candidate,dict) or not isinstance(candidate.get('provider'),str) or not isinstance(candidate.get('model'),str):
                raise ValueError('Every candidate needs a provider and model; base is only valid in overrides.')
            if not candidate['provider'] or not candidate['model']: raise ValueError('Provider and model cannot be blank.')
            if 'config' in candidate and not isinstance(candidate['config'],dict): raise ValueError('Candidate config must be a mapping.')
    return copy.deepcopy(value)

class SetupManager:
    def __init__(self,home,*,store=None,runtime_operation=None,progress=None,auth_command=None,probe_command=None,catalog=None,allow_missing_workspace=False,global_only=False):
        from .provider_catalog import ProviderCatalog
        self.catalog=catalog or ProviderCatalog()
        self.global_only=global_only
        self.allow_missing_workspace=allow_missing_workspace
        self.home=Path(home); self.store=store or SettingsStore(home)
        self.runtime_operation=runtime_operation
        self.progress=progress; self.auth_command=auth_command; self.probe_command=probe_command; self.logins={}

    def config(self,workspace):
        if self.allow_missing_workspace:
            workspace=Path(workspace).expanduser().resolve()
            while not workspace.is_dir() and workspace.parent!=workspace:
                workspace=workspace.parent
        return load_config(workspace,home=self.home,global_only=self.global_only)

    def provider_rows(self,workspace):
        config=self.config(workspace)
        rows=[]
        for value in config.providers:
            identity=value.get('id') or value.get('instance_id') or value['module'].removeprefix('provider-')
            raw=value.get('config',{})
            refs=[]
            def scan(node):
                if isinstance(node,dict):
                    for key,item in node.items():
                        if key.lower().replace('-','_') in SECRET_KEYS or key.lower().endswith(('_api_key','_token','_secret','_password')):
                            refs.append(item)
                        else: scan(item)
            scan(raw)
            credential=environment_credential(value['module'],raw)
            configured=bool(refs) and all(isinstance(v,str) and bool(v) and (not v.startswith('${') or bool(os.environ.get(v[2:-1]))) for v in refs)
            if not refs:configured=credential['available']
            rows.append({'credential':credential,'id':identity,'module':value['module'],'source':public_source(value.get('source')),'config':redact(raw),
                'credentialsConfigured':configured,'keySource':'environment' if (not refs and credential['available']) or any(isinstance(v,str) and v.startswith('${') for v in refs) else ('configuration' if refs else 'provider-managed'), 'enabled':value.get('enabled',True) and identity not in config.settings.get('configurator',{}).get('disabled',{}).get('providers',[])})
        return rows

    def catalog_key(self,args,workspace):
        from .provider_catalog import fingerprint
        config=self.config(workspace)
        row=next((row for row in config.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args.get('id')),None)
        module=args.get('module') or (row or {}).get('module')
        # Priority is Unified-owned ordering, not provider catalog identity.
        row=copy.deepcopy(row) if row else None
        if row:row.get('config',{}).pop('priority',None)
        raw=(row or {}).get('config',{})
        credential=environment_credential(module,raw)
        environment={name:os.environ.get(name) for name in (*PROVIDER_ENV.get(module,()),credential.get('envVar')) if name}
        environment.update({name:os.environ.get(name) for name in re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)',json.dumps(raw))})
        # Include credential-file contents by digest, never in browser-visible data.
        files={}
        for name,value in raw.items():
            if ('token' in name or 'credential' in name) and isinstance(value,str) and Path(value).expanduser().is_file():
                path=Path(value).expanduser()
                files[name]=fingerprint(path.read_bytes()) if path.stat().st_size<1_000_000 else str(path.stat().st_mtime_ns)
        return fingerprint([str(Path(workspace).resolve()),row,module,raw,environment,files,getattr(config,'module_sources',{}).get(module)])

    async def cached_probe(self,action,args,workspace):
        key=(action,self.catalog_key(args,workspace))
        result=await self.catalog.get(key,lambda:self.probe(action,args,workspace),refresh=args.get('refresh',False))
        if key[1]!=self.catalog_key(args,workspace):
            self.catalog.discard(key)
            raise ValueError('Provider configuration changed during discovery. The new configuration is being refreshed.')
        return result

    async def probe(self,action,args,workspace):
        from .runtime import RuntimeManager
        configured=self.config(workspace)
        row=next((row for row in configured.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args.get('id')),None)
        module=args.get('module') if action=='providers.schema' else (row or {}).get('module')
        if not module:raise ValueError('Save this provider before discovering models or testing it.')
        safe_name(module)
        raw=(row or {}).get('config',{}) if row and row['module']==module else {}
        # The isolated probe discovers metadata with config={}; its shared
        # materializer then expands this in-memory copy before provider mount.
        config={} if action=='providers.schema' else copy.deepcopy(raw)
        if action!='providers.schema':
            credential=environment_credential(module,raw)
            field=credential['field']
            if not config.get(field) and credential['available']:config[field]='${'+credential['envVar']+'}'
        command=self.probe_command or RuntimeManager()._command()[:-1]+[str(Path(__file__).with_name('provider_probe.py'))]
        env={**os.environ,'AMPLIFIER_WEB_HOME':str(self.home)}
        if module=='provider-github-copilot' and config.get('github_token'):env['COPILOT_AGENT_TOKEN']=config['github_token']
        probe_workspace=Path(workspace)
        while not probe_workspace.is_dir() and probe_workspace.parent!=probe_workspace:
            probe_workspace=probe_workspace.parent
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,env=env,cwd=probe_workspace)
        try:
            try:
                output,_=await asyncio.wait_for(process.communicate(json.dumps({'action':action,'module':module,'config':config,'source':getattr(configured,'module_sources',{}).get(module) or (row or {}).get('source'),'registryHome':str(getattr(configured,'registry_home',self.home/'foundation'))}).encode()),90)
            except TimeoutError:
                raise ValueError('Provider check timed out after 90 seconds. Check connectivity and credentials, then retry.') from None
            try:result=json.loads(output)
            except (ValueError,UnicodeError):
                raise ValueError(
                    f'The provider check ended without a valid result (exit code {process.returncode}). '
                    'Check the runtime installation or update the app, then retry.'
                ) from None
            if result.get('error'):raise ValueError(result['error'])
            if process.returncode:raise ValueError('The provider check could not finish. Please retry.')
            metadata={'module':module,'info':result['info'],'configSchema':result['configSchema']}
            response={'providerMetadata':metadata}
            if action=='providers.models':response.update(models=result.get('models',[]),modelsProviderId=args['id'],modelsSupported=result.get('modelsSupported',True))
            if action=='providers.test':response['test']={**result['test'],'providerId':args['id']}
            return response
        finally:
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()

    def _keys(self,updates):
        path=self.store.shared_home/'keys.env'
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with FileLock(str(path)+'.lock',timeout=10):
            lines=path.read_text().splitlines() if path.exists() else []
            names=set(updates)
            lines=[line for line in lines if line.removeprefix('export ').split('=',1)[0].strip() not in names]
            lines.extend(name+'='+shlex.quote(value) for name,value in updates.items())
            atomic_write(path,'\n'.join(lines)+'\n',private=True)
        from .host.config import _KEY_FILE_VALUES
        _KEY_FILE_VALUES.update(updates)
        # A long-running backend must see edited keys immediately. Child host
        # generations inherit these values; no browser state includes them.
        os.environ.update(updates)

    def _provider_mutation(self,args,workspace,scope,remove=False):
        identity=safe_name(args.get('id') or args.get('module','').removeprefix('provider-'))
        effective=self.config(workspace)
        existing=next((row for row in effective.providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==identity),None)
        def mutate(settings):
            rows=settings.setdefault('config',{}).setdefault('providers',[])
            index=next((i for i,row in enumerate(rows) if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==identity),None)
            if remove:
                if not existing: raise ValueError('Provider instance does not exist.')
                row={'id':identity,'module':existing['module']}
            else:
                module=safe_name(args.get('module') or (existing or {}).get('module',''))
                if not module.startswith('provider-'): raise ValueError('Choose a provider module.')
                config=copy.deepcopy(args.get('config',{}))
                if not isinstance(config,dict): raise ValueError('Provider config must be a mapping.')
                old=(existing or {}).get('config',{})
                updates={}
                def private(node,path=()):
                    result={}
                    for key,value in node.items():
                        normalized=key.lower().replace('-','_')
                        secret=normalized in SECRET_KEYS or normalized.endswith(('_api_key','_token','_secret','_password'))
                        if secret and value in ('[REDACTED]','<redacted>'):
                            original=old
                            for part in (*path,key): original=original.get(part) if isinstance(original,dict) else None
                            if original is not None: result[key]=original
                        elif secret and value is not None and value!='' and not (isinstance(value,str) and re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}',value)):
                            if not isinstance(value,str): raise ValueError('Credentials must be strings or environment references.')
                            env='AMPLIFIER_'+re.sub('[^A-Za-z0-9]','_',identity+'_'+ '_'.join((*path,key))).upper()
                            updates[env]=value; result[key]='${'+env+'}'
                        elif isinstance(value,dict): result[key]=private(value,(*path,key))
                        else: result[key]=value
                    return result
                field=credential_field(module)
                if args.get('apiKeyEnv') is not None:
                    if args.get('apiKey'):raise ValueError('Choose an environment variable or enter a private key, not both.')
                    credential=environment_credential(module,env_var=args['apiKeyEnv'])
                    if not credential['supported']:raise ValueError('This provider uses account sign-in instead of an API key.')
                    config[field]='${'+credential['envVar']+'}'
                elif args.get('apiKey') is not None:config[field]=args['apiKey']
                elif not config.get(field):
                    if old.get(field):config[field]=old[field]
                    else:
                        credential=environment_credential(module)
                        if credential['supported'] and credential['available']:config[field]='${'+credential['envVar']+'}' 
                if module=='provider-openai-chatgpt':
                    config['token_file_path']=config.get('token_file_path') or old.get('token_file_path') or str(self.store.shared_home/('openai-chatgpt-'+identity+'-oauth.json'))
                    config['login_on_mount']=False
                row={'id':identity,'module':module,'config':private(config)}
                source=args.get('source') or (existing or {}).get('source')
                if source: row['source']=validate_uri(source)
                if updates:self._keys(updates)
            if index is None:rows.append(row)
            else:rows[index]=row
            disabled=set(overlay(effective.settings,settings).get('configurator',{}).get('disabled',{}).get('providers',[]))
            if remove:disabled.add(identity)
            else:disabled.discard(identity)
            settings.setdefault('configurator',{}).setdefault('disabled',{})['providers']=sorted(disabled)
            # Clear the older Unified-only flag when editing an existing entry.
            settings.get('overrides',{}).get(identity,{}).pop('enabled',None)
        self.store.update(workspace,scope,mutate)
        return {'providers':self.provider_rows(workspace),'takesEffect':'new_sessions','scope':scope}

    def _routing_dirs(self,workspace):
        # Same first-hit precedence as the mounted routing hook.
        registry=getattr(self.config(workspace),'registry_home',self.home/'foundation')
        dirs=routing_dirs(workspace,shared_home=self.store.shared_home,global_only=self.global_only)
        dirs.extend(sorted((registry/'cache').glob('amplifier-bundle-routing-matrix-*/routing')))
        return dirs

    def routing(self,workspace):
        active=self.config(workspace).settings.get('routing',{}).get('matrix','balanced')
        rows=[]; seen=set(); roles=set()
        directories=self._routing_dirs(workspace)
        for directory in directories:
            for path in sorted(directory.glob('*.yaml')):
                if path.is_symlink() or path.stat().st_size>256*1024:continue
                name=path.stem
                if name in seen:continue
                try:
                    value=yaml.safe_load(path.read_text());validate_matrix(value)
                except (ValueError,yaml.YAMLError):continue
                seen.add(name);roles.update(value['roles'])
                rows.append({'name':name,'description':value.get('description',''),'source':'custom' if directory in directories[:3] else 'bundle','active':active==name})
        return {'matrices':rows,'active':active,'roles':sorted(roles)}

    def matrix(self,workspace,name):
        safe_name(name)
        for directory in self._routing_dirs(workspace):
            path=directory/(name+'.yaml')
            if path.exists() and not path.is_symlink() and path.stat().st_size<=256*1024:
                return {**validate_matrix(yaml.safe_load(path.read_text())),'name':name}
        raise ValueError('The routing matrix does not exist.')

    async def perform(self,action,args):
        workspace=args.get('workspace') or str(Path.cwd());scope=args.get('scope','global')
        self.store.path(workspace,scope) # Validate scope even on reads.
        if action=='providers.credentials':
            self.config(workspace) # Load app-owned keys as well as the launch environment.
            return {'credentialCheck':{**environment_credential(args['module'],env_var=args.get('envVar')), 'requestedEnvVar':args.get('envVar',''), 'checkedAt':time.time()}}
        if action=='providers.list':return {'providers':self.provider_rows(workspace),'providersWorkspace':str(workspace),'providersLoadedAt':time.time()}
        if action in {'providers.schema','providers.models','providers.test'}:
            return await (self.probe(action,args,workspace) if action=='providers.test' else self.cached_probe(action,args,workspace))
        if action in {'providers.move','providers.reorder'}:
            current=self.config(workspace)
            rows=current.providers
            ids=[row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-') for row in rows]
            if action=='providers.reorder':
                ordered=args['ids']
                if args.get('expectedIds')!=ids or len(ordered)!=len(ids) or set(ordered)!=set(ids):
                    raise ValueError('Provider connections changed. Refresh the list before saving order.')
                changed=ordered!=ids
                ids=ordered
            else:
                identity=args['id'];before=args.get('beforeId')
                if identity not in ids or before is not None and before not in ids:raise ValueError('Refresh the provider list before reordering.')
                changed=identity!=before
                if changed:
                    ids.remove(identity);ids.insert(ids.index(before) if before else len(ids),identity)
            if changed:
                def reorder(settings):
                    scoped=settings.setdefault('config',{}).setdefault('providers',[])
                    for priority, key in enumerate(ids,1):
                        source=next(row for row in rows if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==key)
                        row=next((row for row in scoped if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==key),None)
                        if row is None:
                            row={k:source[k] for k in ('id','module') if k in source};scoped.append(row)
                        row.setdefault('config',{})['priority']=priority
                    settings.pop('provider_order',None)
                self.store.update(workspace,scope,reorder)
            return {'providers':self.provider_rows(workspace),'scope':scope}
        if action=='providers.save':return self._provider_mutation(args,workspace,scope)
        if action=='providers.finishSetup':
            identity=safe_name(args['id']);model=args['model'].strip()
            if not model:raise ValueError('Choose a model before finishing setup.')
            effective=self.config(workspace)
            row=next((value for value in effective.providers if (value.get('id') or value.get('instance_id') or value['module'].removeprefix('provider-'))==identity),None)
            if not row:raise ValueError('The connection changed. Refresh your connections and try again.')
            # A guided edit changes only the model. Preserve opaque provider fields,
            # credential references and custom routing, including a custom balanced file.
            routing=self.routing(workspace)
            initialize=bool(args.get('initializeRouting') and not effective.settings.get('routing',{}).get('matrix')
                and len([p for p in self.provider_rows(workspace) if p.get('enabled',True)])==1
                and not any(p['name']==routing['active'] and p['source']=='custom' for p in routing['matrices']))
            result=self._provider_mutation({**args,'module':row['module'],'config':{**row.get('config',{}),'default_model':model}},workspace,scope)
            if initialize:
                name='my-ai-'+uuid.uuid4().hex[:8]
                matrix={'name':name,'description':'Models selected during AI connection setup','roles':{
                    role:{'description':description,'candidates':[{'provider':identity,'model':model}]}
                    for role,description in [('general','General conversation and work'),('fast','Quick and lightweight work')]}}
                result.update(await self.perform('routing.save',{'workspace':workspace,'scope':scope,'name':name,'matrix':matrix,'activate':True}))
            result['setupCompletion']={'id':identity,'model':model,'routingCreated':initialize,'takesEffect':'new_sessions'}
            return result
        if action=='providers.remove':return self._provider_mutation(args,workspace,scope,remove=True)
        if action=='providers.loginCancel':return await self.cancel_login(args['id'])
        if action=='providers.loginStatus':return {'providerId':args['id'],'login':self.login_state(args['id'])}
        if action=='providers.login':
            row=next((row for row in self.config(workspace).providers if (row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-'))==args['id']),None)
            if row and row['module']=='provider-openai-chatgpt':return await self.start_login(row,args,workspace,scope)
        if action=='providers.login':
            if not self.runtime_operation:raise ValueError('This provider does not expose a standalone sign-in flow.')
            result=await self.runtime_operation('configuration.providerLogin',{'provider':args['id'],'sessionId':args.get('sessionId')})
            return {'providerId':args['id'],'login':{**result,'providerId':args['id']}}
        if action=='routing.list':return self.routing(workspace)
        if action=='routing.show':return {'matrix':self.matrix(workspace,args['name'])}
        if action in {'routing.save','routing.use'}:
            name=safe_name(args['name'])
            if action=='routing.save':
                value=args['matrix']
                if isinstance(value,str):value=yaml.safe_load(value)
                value=validate_matrix(value);value['name']=name
                if redact(value)!=value:raise ValueError('Routing matrices must not contain credentials; configure them on the provider.')
                directory=self.store.shared_home/'routing' if scope=='global' else Path(workspace)/'.amplifier'/('routing.local' if scope=='local' else 'routing')
                def save(settings):
                    atomic_write(directory/(name+'.yaml'),yaml.safe_dump(value,sort_keys=False))
                    if args.get('activate'):settings.setdefault('routing',{})['matrix']=name
                self.store.update(workspace,scope,save)
            else:
                self.matrix(workspace,name)
                def activate(settings):settings.setdefault('routing',{})['matrix']=name
                self.store.update(workspace,scope,activate)
            return {**self.routing(workspace),'matrix':self.matrix(workspace,name),'takesEffect':'new_sessions','scope':scope}
        raise ValueError('Unknown setup operation.')

    def login_state(self,identity):
        row=self.logins.get(identity)
        if not row:return {"providerId":identity,"status":"idle"}
        return {key:value for key,value in row.items() if key not in {"task","process"}}

    async def publish_login(self,identity):
        result={"providerId":identity,"login":self.login_state(identity)}
        if self.progress:await self.progress(result)
        return result

    async def start_login(self,provider,args,workspace,scope):
        identity=args['id']; previous=self.logins.get(identity)
        if previous and not previous['task'].done():return await self.publish_login(identity)
        path=self.home/'config'/('openai-chatgpt-'+safe_name(identity)+'-oauth.json')
        config={**provider.get('config',{}),'token_file_path':str(path),'login_on_mount':False}
        self._provider_mutation({**args,'module':provider['module'],'config':config},workspace,scope)
        row={'providerId':identity,'loginId':uuid.uuid4().hex,'status':'starting','instructions':[]}
        self.logins[identity]=row
        async def run():
            process=None
            try:
                if self.auth_command:command=list(self.auth_command)
                else:
                    from .runtime import RuntimeManager
                    command=RuntimeManager()._command()[:-1]+[str(Path(__file__).with_name('provider_auth.py'))]
                process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,env={**os.environ,'AMPLIFIER_WEB_HOME':str(self.home)})
                row['process']=process
                process.stdin.write((json.dumps({'module':provider['module'],'tokenFile':str(path)})+'\n').encode());await process.stdin.drain();process.stdin.close()
                async with asyncio.timeout(900):
                    while line:=await process.stdout.readline():
                        try:event=json.loads(line)
                        except (ValueError,UnicodeError):continue
                        if event.get('status') not in {'waiting','completed','failed'}:continue
                        row['status']=event['status']
                        if event.get('instruction'):
                            text=str(event['instruction'])[:2000]
                            row['instructions']=(row['instructions']+[text])[-20:]
                            for url in re.findall(r'https://[^\s<>]+',text):
                                if urlsplit(url).hostname in {'auth.openai.com','chatgpt.com','platform.openai.com'}:row['url']=url
                        if event.get('error'):row['error']=str(event['error'])[:300]
                        await self.publish_login(identity)
                    code=await process.wait()
                    if code or row['status'] not in {'completed','failed'}:
                        row.update(status='failed',error='The provider login ended before authentication completed.')
            except asyncio.CancelledError:row['status']='cancelled';raise
            except TimeoutError:row.update(status='expired',error='Device login expired. Start again.')
            except Exception as exc:row.update(status='failed',error='Unable to run provider login ('+type(exc).__name__+').')
            finally:
                if process and process.returncode is None:
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:await asyncio.wait_for(process.wait(),3)
                    except TimeoutError:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        await process.wait()
                await self.publish_login(identity)
        row['task']=asyncio.create_task(run())
        return await self.publish_login(identity)

    async def cancel_login(self,identity):
        row=self.logins.get(identity)
        if row and not row['task'].done():
            row['status']='cancelled'
            row['task'].cancel()
            await asyncio.gather(row['task'],return_exceptions=True)
        return await self.publish_login(identity)

    async def close(self):
        await asyncio.gather(*(self.cancel_login(identity) for identity in list(self.logins)),return_exceptions=True)
