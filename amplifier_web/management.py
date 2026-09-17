"""Application management actions shared by the UI and the agent."""
from __future__ import annotations
import asyncio
import copy
import json
from pathlib import Path
import time
import uuid
from .preferences import SettingsStore
from .host.storage import SessionStore

class Management:
    def __init__(self,service):
        self.service=service
        self.settings=SettingsStore(service.data_dir)
        self.lock=asyncio.Lock()
        from .notifications import Notifications
        self.notifications=Notifications(service.data_dir)
        service.state["notificationSettings"]=self.notifications.public()
        self.setup_manager=None

    async def publish(self,**values):
        async with self.service.lock:
            self.service.state.update(values)
            self.service._publish()

    async def download(self,name,content,mime='application/json'):
        async with self.service.lock:
            effect={'id':str(uuid.uuid4()),'type':'download','filename':name,'content':content,'mimeType':mime,'createdAt':time.time()}
            self.service.state.setdefault('deviceCommands',[]).append(effect)
            self.service.state['deviceCommands']=self.service.state['deviceCommands'][-20:]
            self.service._publish()

    async def command(self,action,args,command_id=None):
        async with self.lock:
            await self.publish(management={'phase':'working','operation':action,'error':None})
            guarded=None
            try:
                if action=='bundle.save':
                    async with self.service.lock:
                        current=self.service._session(args['sessionId'])
                        if current['status'] not in {'idle','stopped','interrupted','error'} or any(w.get('status') in {'running','starting','queued'} for w in current.get('workers',[])):
                            raise ValueError('Finish active work before saving this conversation bundle')
                        current['configurationBusy']=True;guarded=current['id']
                        self.service._publish()
                await self.perform(action,args)
                await self.publish(management={'phase':'ready','operation':action,'error':None})
                await self.complete(command_id,'ready')
            except asyncio.CancelledError:raise
            except Exception as exc:
                # Host-controlled validation text only; provider/transport errors
                # are converted before reaching this boundary.
                await self.publish(management={'phase':'error','operation':action,'error':str(exc)[:1000]})
                await self.complete(command_id,'error',str(exc)[:1000])
            finally:
                if guarded:
                    async with self.service.lock:
                        for session in self.service.state['sessions']:
                            if session['id']==guarded:session['configurationBusy']=False
                        self.service._publish()

    async def complete(self,identity,phase,error=None):
        if not identity:return
        async with self.service.lock:
            results=self.service.state.setdefault('managementResults',{})
            results[identity]={'phase':phase,'error':error}
            while len(results)>100:results.pop(next(iter(results)))
            self.service._publish()

    def session(self,args):
        return copy.deepcopy(self.service._session(args.get('sessionId')))

    async def ensure_runtime(self,session):
        if not self.service.runtime:raise ValueError('Amplifier runtime is unavailable')
        if self.service.state.get('updates',{}).get('phase')=='activating':raise ValueError('An update is activating; retry shortly')
        await self.service.runtime.start(session,self.service.on_runtime_event)

    async def invalidate_configuration(self):
        async with self.service.lock:
            for session in self.service.state['sessions']:
                session['configurationPending']=True
            self.service._publish()
        for session in list(self.service.state['sessions']):
            await self.service.refresh_configuration(session['id'])

    async def perform(self,action,args):
        if action.startswith(('modules.','sources.')):
            from .registry import RegistryManager
            session=self.session(args) if self.service.state['sessions'] else {'workspace':self.service.default_workspace}
            result=await RegistryManager(self.service.data_dir,store=self.settings).perform(action,{**args,'workspace':session['workspace']})
            async with self.service.lock:
                self.service.state.setdefault('registry',{}).update(result)
                self.service._publish()
            if action.endswith(('.save','.remove')):await self.invalidate_configuration()
        elif action.startswith(('providers.','routing.')):
            from .setup import SetupManager
            session=self.session(args) if self.service.state['sessions'] else {'workspace':self.service.default_workspace}
            async def runtime_operation(operation,values):
                current=self.session(args)
                await self.ensure_runtime(current)
                return await self.service.runtime.control(current['id'],operation,values)
            async def progress(values):
                async with self.service.lock:
                    self.service.state.setdefault('setup',{}).update(values)
                    self.service._publish()
            if self.setup_manager is None:
                self.setup_manager=SetupManager(self.service.data_dir,runtime_operation=runtime_operation,progress=progress)
            else:
                self.setup_manager.runtime_operation=runtime_operation
                self.setup_manager.progress=progress
            result=await self.setup_manager.perform(action,{**args,'workspace':session['workspace']})
            async with self.service.lock:
                self.service.state.setdefault('setup',{}).update(result)
                self.service._publish()
            if action in {'providers.save','providers.remove','routing.save','routing.use'}:await self.invalidate_configuration()
        elif action.startswith(('bundle.','bundles.')):
            from .bundles import BundleManager
            manager=BundleManager(self.service.data_dir)
            kwargs={}
            if action in {'bundle.export','bundle.save'}:
                session=self.session(args)
                if action=='bundle.save' and session['status'] in {'starting','working','ready','stopping'}:raise ValueError('Finish the current turn before changing its bundle')
                await self.ensure_runtime(session)
                effective=await self.service.runtime.control(session['id'],'configuration.inspect',{})
                resources=await self.service.runtime.control(session['id'],'configuration.exportResources',{})
                kwargs={'effective_config':effective.get('plan',{}),'root_bundle':session['bundle'],'resources':resources}
            workspace=self.session(args)['workspace'] if self.service.state['sessions'] else self.service.default_workspace
            result=await manager.perform(action,{**args,'workspace':workspace},**kwargs)
            values={}
            if 'bundles' in result:values['bundles']=result['bundles']
            if 'discovery' in result:values['bundleDiscovery']=result['discovery']
            if values:await self.publish(**values)
            if action in {'bundles.add','bundles.toggle','bundles.remove','bundles.move'}:await self.invalidate_configuration()
            if result.get('content') is not None:await self.download(result.get('filename','custom-bundle.yaml'),result['content'],result.get('mimeType','text/yaml'))
            if action=='bundle.save' and result.get('saved'):
                saved=result['saved']
                async with self.service.lock:
                    self.service._session(session['id'])['bundle']=saved['name']
                    self.service._publish()
                await self.service.runtime.stop(session['id'])
        elif action in {'configuration.inspect','configuration.apply'}:
            forwarded={'sessionId':args['id'],'operation':action,'args':{'config':args['config']} if 'config' in args else {}}
            await self.perform('runtime.control',forwarded)
        elif action=='runtime.control':
            session=self.session(args)
            mutating=args['operation'] in {'configuration.apply','configuration.toggle','context.clear','provider.select'}
            if mutating:
                async with self.service.lock:
                    current=self.service._session(session['id'])
                    if current['status'] not in {'idle','stopped','interrupted','error'} or any(w.get('status') in {'running','starting','queued'} for w in current.get('workers',[])):
                        raise ValueError('Finish active work before changing this conversation configuration')
                    current['configurationBusy']=True
                    self.service._publish()
            try:
                await self.ensure_runtime(session)
                result=await self.service.runtime.control(session['id'],args['operation'],args.get('args',{}))
                if result.get('requiresRestart'):
                    await self.service.runtime.stop(session['id'])
                    await self.service.runtime.start(session,self.service.on_runtime_event)
                    inspected=await self.service.runtime.control(session['id'],'configuration.inspect',{})
                    result['configuration']=inspected
                refreshed={}
                refresh={'mode.set':'mode.list','mode.clear':'mode.list','goals.set':'goals.get','goals.clear':'goals.get','budget.set':'budget.get','provider.select':'configuration.inspect','configuration.toggle':'configuration.inspect'}.get(args['operation'])
                if refresh:refreshed[refresh]=await self.service.runtime.control(session['id'],refresh,{})
                async with self.service.lock:
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{}).update(refreshed)
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{})[args['operation']]=result
                    if args['operation'] in {'configuration.inspect','configuration.apply'}:
                        configuration=result.get('configuration',result)
                        self.service.state.setdefault('sessionConfiguration',{})[session['id']]=configuration
                        self.service._session(session['id'])['configuration']=configuration
                    self.service._publish()
            finally:
                if mutating:
                    async with self.service.lock:
                        self.service._session(session['id'])['configurationBusy']=False
                        self.service._publish()
        elif action=='notifications.get':
            await self.publish(notificationSettings=self.notifications.public())
        elif action=='notifications.save':
            await self.publish(notificationSettings=self.notifications.save(args['patch']))
        elif action=='permissions.get':
            session=self.session(args);scope=args.get('scope','global')
            config=self.settings.read(session['workspace'],scope)
            values=config.get('overrides',{}).get('tool-filesystem',{}).get('config',{})
            await self.publish(permissions={'scope':scope,'allowed':values.get('allowed_write_paths',[]),'denied':values.get('denied_write_paths',[])})
        elif action=='permissions.save':
            session=self.session(args);scope=args.get('scope','global')
            allowed=[str(Path(p).expanduser().resolve()) for p in args.get('allowed',[])]
            denied=[str(Path(p).expanduser().resolve()) for p in args.get('denied',[])]
            def edit(config):
                values=config.setdefault('overrides',{}).setdefault('tool-filesystem',{}).setdefault('config',{})
                values.update(allowed_write_paths=allowed,denied_write_paths=denied)
            self.settings.update(session['workspace'],scope,edit)
            await self.publish(permissions={'scope':scope,'allowed':allowed,'denied':denied,'detail':'Saved. Idle conversations use these settings on their next message.'})
            await self.invalidate_configuration()
        elif action=='history.list':
            await self.publish(history=await asyncio.to_thread(self.history,args.get('legacy',False)))
        elif action=='history.importFile':
            from .session_store import complete_tool_exchanges,text_content
            if args['format']=='jsonl':
                rows=[json.loads(line) for line in args['content'].splitlines() if line.strip()];metadata={}
            else:
                payload=json.loads(args['content'])
                if isinstance(payload,list):rows=payload;metadata={}
                elif isinstance(payload,dict):rows=payload.get('messages');metadata=payload.get('metadata',{})
                else:raise ValueError('Use a JSON message array or a session export')
            if not isinstance(rows,list) or not isinstance(metadata,dict) or not rows:raise ValueError('The transcript needs messages and valid metadata')
            if any(not isinstance(r,dict) or r.get('role') not in {'user','assistant','system','developer','tool'} for r in rows):raise ValueError('Unsupported transcript message role')
            rows=complete_tool_exchanges(rows)
            async with self.service.lock:
                session=self.service._new_session({'title':args.get('title') or 'Imported transcript','workspace':self.service.default_workspace,'bundle':args.get('bundle') or metadata.get('bundle_name') or 'anchors'})
                session['status']='stopped'
                store=SessionStore(self.service.data_dir/'sessions')
                metadata={**metadata,'parent_id':None,'working_dir':session['workspace'],'bundle_name':session['bundle'],'imported_file':True,'jobs_replayed':False,'preserve_system':True}
                store.save(session['id'],rows,metadata,preserve_system=True)
                for row in rows:
                    text=text_content(row)
                    if row['role'] in {'user','assistant'} and text:self.service._message(session,row['role'],text,source='import')
                self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=session['id']
                self.service._publish()
        elif action=='history.import':
            identity=args['id'];store=SessionStore(self.service.data_dir/'sessions',legacy_home=Path.home()/'.amplifier')
            saved=store.load(identity) or store.import_cli(identity)
            if not saved:raise ValueError('The requested session was not found')
            rows,meta=saved
            async with self.service.lock:
                existing=next((s for s in self.service.state['sessions'] if s['id']==identity),None)
                if existing:self.service.state['selectedSessionId']=identity
                else:
                    workspace=meta.get('working_dir') or meta.get('workspace') or self.service.default_workspace
                    if not Path(workspace).is_dir():workspace=self.service.default_workspace
                    session=self.service._new_session({'title':meta.get('name') or meta.get('title') or 'Imported conversation','workspace':workspace,'bundle':meta.get('bundle_name') or 'anchors'})
                    session['id']=identity;session['status']='stopped'
                    for row in rows:
                        if row.get('role') in {'user','assistant'} and isinstance(row.get('content'),str):
                            self.service._message(session,row['role'],row['content'],source='import')
                    self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=identity
                self.service._publish()
        elif action=='history.export':
            session=self.session(args);store=SessionStore(self.service.data_dir/'sessions')
            saved=store.load(session['id'])
            if not saved:raise ValueError('This conversation has no runtime transcript yet')
            rows,metadata=saved
            if args.get('format')=='jsonl':
                await self.download('amplifier-transcript.jsonl',''.join(json.dumps(m)+'\n' for m in rows),'application/x-ndjson')
            else:await self.download('amplifier-session.json',json.dumps({'messages':rows,'metadata':metadata},indent=2))
        elif action=='history.cleanup':
            cutoff=time.time()-args.get('days',30)*86400
            async with self.service.lock:
                eligible=[s for s in self.service.state['sessions'] if s['id']!=self.service.state['selectedSessionId'] and s['status'] in {'idle','stopped','interrupted','error'} and max([s.get('createdAt',0)]+[m.get('createdAt',0) for m in s['messages']])<cutoff]
                if args.get('apply'):
                    identities={s['id'] for s in eligible}
                    self.service.state['sessions']=[s for s in self.service.state['sessions'] if s['id'] not in identities]
                    if args.get('purge'):
                        import shutil
                        store=SessionStore(self.service.data_dir/'sessions')
                        for identity in identities:
                            path=store.directory(identity)
                            if path.exists():shutil.rmtree(path)
                self.service.state['cleanupPreview']={'sessions':[{'id':s['id'],'title':s['title']} for s in eligible],'applied':bool(args.get('apply')),'detail':'Selected old records removed.' if args.get('purge') else 'Conversation list cleaned. Runtime transcripts are retained for recovery.'}
                self.service._publish()
        elif action=='maintenance.backup':
            from .recovery import backup
            async with self.service.lock:result=backup(self.service)
            await self.publish(maintenance=result)
        elif action=='maintenance.reset':
            from .recovery import reset
            await reset(self,args)
        elif action=='maintenance.repair':
            if self.service.update_manager and self.service.update_manager.busy():raise ValueError('Finish active work before repairing the runtime')
            await self.service.runtime.close()
            from .runtime import RuntimeManager
            from .updates import process
            command=RuntimeManager()._command()
            await process(command[0],'sync','--project',command[3],'--python','3.13','--reinstall',timeout=900)
            await self.publish(maintenance={'detail':'Runtime dependencies repaired. Conversations will resume when you next send a message.'})
        else:
            raise ValueError('Management action is not implemented: '+action)

    def history(self,legacy=False):
        rows=[];store=SessionStore(self.service.data_dir/'sessions')
        for path in store.base_dir.glob('*/metadata.json'):
            try:
                meta=json.loads(path.read_text())
                rows.append({'id':path.parent.name,'title':meta.get('name') or meta.get('title') or meta.get('bundle_name') or path.parent.name,'workspace':meta.get('working_dir') or meta.get('workspace'),'updatedAt':meta.get('updated_at'),'legacy':False,'parentId':meta.get('parent_id')})
            except (OSError,ValueError):continue
        if legacy:
            seen={r['id'] for r in rows}
            for path in (Path.home()/'.amplifier/projects').glob('*/sessions/*/metadata.json'):
                if path.parent.name in seen:continue
                try:
                    meta=json.loads(path.read_text())
                    rows.append({'id':path.parent.name,'title':meta.get('name') or meta.get('title') or path.parent.name,'workspace':meta.get('working_dir'),'legacy':True,'parentId':meta.get('parent_id')})
                except (OSError,ValueError):continue
        return rows
