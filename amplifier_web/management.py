"""Application management actions shared by the UI and the agent."""
from __future__ import annotations
import asyncio
import copy
from contextlib import nullcontext
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
        from .provider_catalog import ProviderCatalog
        self.provider_catalog=ProviderCatalog(service.data_dir / "cache" / "provider-catalogs.json")
        self.catalog_limit=asyncio.Semaphore(3)
        self.setup_manager=None
        self.pending_config_tasks={}
        for session in service.state.get('sessions',[]):
            if session.get('pendingConfiguration',{}).get('phase') in {'queued','applying'} and self.queued_path(session['id']).exists():
                session['pendingConfiguration']['phase']='queued'
                task=asyncio.create_task(self.apply_queued(session['id']))
                self.pending_config_tasks[session['id']]=task
                service.tasks.add(task);task.add_done_callback(service.tasks.discard)

    def background(self,operation):
        task=asyncio.create_task(operation)
        self.service.tasks.add(task)
        task.add_done_callback(self.service.tasks.discard)

    async def warm_providers(self,manager,workspace):
        rows=manager.provider_rows(workspace)
        async with self.service.lock:
            setup=self.service.state.setdefault('setup',{})
            if setup.get('providersWorkspace')!=workspace:return
            valid={row['id'] for row in rows if row.get('enabled',True)}
            if setup.get('modelsProviderId') not in valid:setup.update(models=[],modelsProviderId=None)
            for field in ('modelCatalogs','providerCatalogs'):
                setup[field]={key:value for key,value in setup.get(field,{}).items() if key in valid}
            self.service._publish()
        async def load(row):
            identity=row['id'];args={'id':identity};key=manager.catalog_key(args,workspace)
            cache_key=('providers.models',key)
            cached=self.provider_catalog.peek(cache_key)
            def catalog_entry(result, **values):
                return {'models':(result or {}).get('models',[]),'supported':(result or {}).get('modelsSupported',True),'metadata':(result or {}).get('providerMetadata'), 'loadedAt':self.provider_catalog.loaded_at.get(cache_key), **values}
            async with self.service.lock:
                setup=self.service.state.setdefault('setup',{})
                if setup.get('providersWorkspace')!=workspace:return
                # Only the exact configuration key may supply cached values.
                entry=catalog_entry(cached,phase='ready' if self.provider_catalog.fresh(cache_key) else 'working')
                setup.setdefault('modelCatalogs',{})[identity]=entry['models']
                setup.setdefault('providerCatalogs',{})[identity]=entry
                self.service._publish()
            async with self.catalog_limit:
                try:
                    result=await manager.perform('providers.models',{'id':identity,'workspace':workspace})
                    entry=catalog_entry(result,phase='ready')
                except Exception:
                    entry=catalog_entry(self.provider_catalog.peek(cache_key),phase='error',error='Could not refresh models. Saved models remain available; retry or enter a model ID.')
            # An old request must never overwrite a newer config or workspace.
            if manager.catalog_key(args,workspace)!=key:return
            async with self.service.lock:
                setup=self.service.state.setdefault('setup',{})
                if setup.get('providersWorkspace')!=workspace:return
                setup.setdefault('providerCatalogs',{})[identity]=entry
                setup.setdefault('modelCatalogs',{})[identity]=entry['models']
                if entry.get('metadata'):setup.setdefault('metadata',{})[row['module']]=entry['metadata']
                self.service._publish()
        await asyncio.gather(*(load(row) for row in rows if row.get('enabled',True)))

    async def warm_runtime_models(self,session_id,providers,revision=None):
        async with self.service.lock:
            control=self.service.state.setdefault('runtimeControl',{}).setdefault(session_id,{})
            current_revision=control.get('configuration.providers',{}).get('catalogRevision')
            if revision and current_revision and current_revision!=revision:return
            if revision and control.get('modelCatalogRevision')!=revision:
                control.update(modelCatalogRevision=revision,modelCatalogs={})
            valid={row['id'] for row in providers}
            control['modelCatalogs']={key:value for key,value in control.get('modelCatalogs',{}).items() if key in valid}
            self.service._publish()
        async def load(row):
            identity=row['id']
            async with self.service.lock:
                control=self.service.state.setdefault('runtimeControl',{}).setdefault(session_id,{})
                if revision and control.get('modelCatalogRevision')!=revision:return
                if control.get('modelCatalogs',{}).get(identity,{}).get('phase') in {'working','ready','error'}:return
                control.setdefault('modelCatalogs',{})[identity]={'phase':'working','models':[]}
                self.service._publish()
            try:
                async with self.catalog_limit:
                    loader=lambda:self.service.runtime.control(session_id,'configuration.providerModels',{'instance':identity})
                    result=await self.provider_catalog.get(('mounted',row['catalogKey']),loader) if row.get('catalogKey') else await loader()
                entry={'phase':'ready','models':result.get('models',[]),'supported':result.get('supported',True)}
            except Exception:
                entry={'phase':'error','models':[],'error':'Could not load models from this mounted provider. Retry to check the connection.'}
            async with self.service.lock:
                control=self.service.state.setdefault('runtimeControl',{}).setdefault(session_id,{})
                if revision and control.get('modelCatalogRevision')!=revision:return
                control.setdefault('modelCatalogs',{})[identity]=entry
                self.service._publish()
        await asyncio.gather(*(load(row) for row in providers))

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

    async def provider_status(self,action,args,command_id,phase,error=None):
        async with self.service.lock:
            statuses=self.service.state.setdefault('actionStatus',{})
            previous=statuses.get(action,{})
            if phase in {'queued','working'} or previous.get('commandId')==command_id:
                statuses[action]={'phase':phase,'error':error,'commandId':command_id,'updatedAt':time.time(),'target':{key:args[key] for key in ('id','section','name','controlId','sessionId','operation') if key in args}}
                self.service._publish()
        if not action.startswith('providers.'):return
        key=action+':'+(args.get('module') if action in {'providers.credentials','providers.schema'} else args.get('id','') or '')
        async with self.service.lock:
            operations=self.service.state.setdefault('setup',{}).setdefault('operations',{})
            previous=operations.get(key,{})
            if phase not in {'queued','working'} and previous.get('commandId')!=command_id:return
            operations[key]={'phase':phase,'error':error,'commandId':command_id,'envVar':args.get('envVar',''),'updatedAt':time.time()}
            self.service._publish()

    async def command(self,action,args,command_id=None):
        if action == 'bundles.list':
            return await self.list_bundles(args, command_id)
        await self.provider_status(action,args,command_id,'queued')
        independent=action in {'configuration.defaults','providers.list','providers.credentials','providers.schema','providers.models','providers.test','routing.list','routing.show'}
        async with (nullcontext() if independent else self.lock):
            await self.provider_status(action,args,command_id,'working')
            await self.publish(management={'phase':'working','operation':action,'error':None})
            guarded=None
            try:
                if action=='bundle.save':
                    async with self.service.lock:
                        current=self.service._session(args['sessionId'])
                        if not self.configuration_idle(current):
                            raise ValueError('Finish active work before saving this conversation bundle')
                        current['configurationBusy']=True;guarded=current['id']
                        self.service._publish()
                await self.perform(action,args,command_id=command_id)
                await self.publish(management={'phase':'ready','operation':action,'error':None})
                await self.complete(command_id,'ready')
                await self.provider_status(action,args,command_id,'ready')
            except asyncio.CancelledError:raise
            except Exception as exc:
                # Host-controlled validation text only; provider/transport errors
                # are converted before reaching this boundary.
                await self.publish(management={'phase':'error','operation':action,'error':str(exc)[:1000]})
                await self.complete(command_id,'error',str(exc)[:1000])
                await self.provider_status(action,args,command_id,'error',str(exc)[:1000])
            finally:
                if guarded:
                    async with self.service.lock:
                        for session in self.service.state['sessions']:
                            if session['id']==guarded:session['configurationBusy']=False
                        self.service._publish()

    async def list_bundles(self, args, command_id):
        """Read the catalog with one start and one atomic completion snapshot.

        Keep the mutation lock: a refresh must not race bundle edits. Publishing
        each bookkeeping field separately made this inexpensive read block all
        HTTP clients while rebuilding the same navigation seven times.
        """
        action = 'bundles.list'
        try:
            if self.lock.locked():
                await self.provider_status(action, args, command_id, 'queued')
            await self.lock.acquire()
        except asyncio.CancelledError:
            await self._bundle_list_transition(args, command_id, 'error',
                error='Bundle refresh cancelled.', active=False)
            raise
        try:
            await self._bundle_list_transition(args, command_id, 'working')
            # Uncontended async locks do not yield. Let ready clients consume
            # the start snapshot before the final catalog is published.
            await asyncio.sleep(0)
            from .bundles import BundleManager
            workspace = self.configuration_session(args)['workspace']
            result = await BundleManager(self.service.data_dir).perform(
                action, {**args, 'workspace': workspace})
            await self._bundle_list_transition(args, command_id, 'ready',
                values={key: result[key] for key in ('bundles', 'registeredBundles') if key in result})
        except asyncio.CancelledError:
            await self._bundle_list_transition(args, command_id, 'error', error='Bundle refresh cancelled.')
            raise
        except Exception as exc:
            await self._bundle_list_transition(args, command_id, 'error', error=str(exc)[:1000])
        finally:
            self.lock.release()

    async def _bundle_list_transition(self, args, command_id, phase, *, error=None, values=None, active=True):
        async with self.service.lock:
            state = self.service.state
            state.update(values or {})
            statuses = state.setdefault('actionStatus', {})
            previous = statuses.get('bundles.list', {})
            if phase == 'working' or previous.get('commandId') == command_id:
                statuses['bundles.list'] = {'phase': phase, 'error': error,
                    'commandId': command_id, 'updatedAt': time.time(),
                    'target': {key: args[key] for key in ('id', 'section', 'name', 'controlId') if key in args}}
            if active:
                state['management'] = {'phase': phase, 'operation': 'bundles.list', 'error': error}
            if phase in {'ready', 'error'}:
                self._record_completion(command_id, phase, error)
            self.service._publish()

    def _record_completion(self, identity, phase, error=None):
        if identity:
            results = self.service.state.setdefault('managementResults', {})
            results[identity] = {'phase': phase, 'error': error}
            while len(results) > 100:
                results.pop(next(iter(results)))

    async def complete(self,identity,phase,error=None):
        if not identity:return
        async with self.service.lock:
            self._record_completion(identity, phase, error)
            self.service._publish()

    def session(self,args):
        return copy.deepcopy(self.service._session(args.get('sessionId')))

    def configuration_session(self, args):
        identity = args.get('sessionId') or self.service.state.get('selectedSessionId')
        session = self.session({'sessionId': identity}) if identity else {}
        if session.get('nativeProject') and args.get('scope', 'global') != 'global':
            if not session.get('workspace') or not Path(session['workspace']).is_dir():
                raise ValueError('Choose an existing workspace before changing project or local settings.')
        return {**session, 'workspace': session.get('workspace') or self.service.state['settings'].get('workspace') or self.service.default_workspace}

    def configuration_idle(self,session):
        return session['status'] in {'idle','ready','stopped','interrupted','error'} and not session.get('configurationBusy') and not any(w.get('status') in {'running','working','starting','queued'} or w.get('persistent') and w.get('status')=='idle' for w in session.get('workers',[]))

    def queued_path(self,identity):
        return self.service.data_dir/'sessions'/identity/'pending-configuration.json'

    async def apply_queued(self,identity):
        try:
            while not self.service.closed:
                session=self.session({'sessionId':identity})
                if not self.queued_path(identity).exists():return
                if self.configuration_idle(session):
                    config=json.loads(self.queued_path(identity).read_text())
                    async with self.service.lock:
                        self.service._session(identity)['pendingConfiguration']={'phase':'applying'}
                        self.service._publish()
                    await self.command('configuration.apply',{'id':identity,'config':config,'whenIdle':True},'queued-config-'+str(uuid.uuid4()))
                    if self.service._session(identity).get('pendingConfiguration',{}).get('phase')=='queued':continue
                    return
                await asyncio.sleep(.5)
        except asyncio.CancelledError:raise
        except Exception:
            async with self.service.lock:
                try:self.service._session(identity)['pendingConfiguration']={'phase':'error','error':'Queued changes could not be applied. Reload the mount plan and retry.'}
                except Exception:pass
                self.service._publish()
        finally:
            if self.pending_config_tasks.get(identity) is asyncio.current_task():self.pending_config_tasks.pop(identity,None)

    async def ensure_runtime(self,session):
        if session.get('nativeProject'):
            if session.get('historyReadOnlyReason'):
                raise ValueError(session['historyReadOnlyReason'])
            if not session.get('workspace') or not Path(session['workspace']).is_dir():
                raise ValueError('Restore this project folder before continuing its chat.')
        if not self.service.runtime:raise ValueError('Amplifier runtime is unavailable')
        from .updates import work_paused
        if work_paused(self.service.state):raise ValueError('An update is activating; retry shortly')
        # Explicitly using runtime controls makes this a web-owned presentation.
        if session.get('historyManaged') and not session.get('historyLoaded'):
            await self.service.history.ensure_loaded(session['id'])
        async with self.service.lock:
            current = self.service._session(session['id'])
            current['historyManaged'] = False
            session.update(copy.deepcopy(current))
        await self.service.runtime.start(session,self.service.on_runtime_event)

    async def invalidate_configuration(self):
        from .setup import SetupManager
        workspace=self.service.state.get('setup',{}).get('providersWorkspace')
        if workspace:self.background(self.warm_providers(SetupManager(self.service.data_dir,catalog=self.provider_catalog),workspace))
        async with self.service.lock:
            for session in self.service.state['sessions']:
                if not session.get('historyManaged'):
                    session['configurationPending']=True
            self.service._publish()
        for session in list(self.service.state['sessions']):
            if not session.get('historyManaged'):
                await self.service.refresh_configuration(session['id'])

    async def perform(self,action,args,command_id=None):
        if action=='locations.list':
            path=Path(args.get('path') or self.service.default_workspace).expanduser()
            if not path.is_absolute():path=Path(self.service.default_workspace)/path
            path=path.resolve()
            if path.is_file():path=path.parent
            if not path.is_dir():raise ValueError('This folder does not exist. Enter a different location.')
            try:
                entries=[]
                for child in path.iterdir():
                    if child.name.startswith('.'):continue
                    try:
                        directory=child.is_dir()
                        if not directory and (args.get('directoriesOnly') or not child.is_file()):continue
                        entries.append({'name':child.name,'path':str(child),'directory':directory})
                    except OSError:continue
                entries.sort(key=lambda row:(not row['directory'],row['name'].casefold()))
            except PermissionError:raise ValueError('This folder is not readable. Choose another location.') from None
            await self.publish(locationListing={'controlId':args['controlId'],'path':str(path),'parent':str(path.parent),'entries':entries[:300],'truncated':len(entries)>300})
        elif action.startswith(('modules.','sources.')):
            from .registry import RegistryManager
            session=self.configuration_session(args)
            result=await RegistryManager(self.service.data_dir,store=self.settings).perform(action,{**args,'workspace':session['workspace']})
            async with self.service.lock:
                self.service.state.setdefault('registry',{}).update(result)
                self.service._publish()
            if action.endswith(('.save','.remove')) and result.get('takesEffect'):await self.invalidate_configuration()
        elif action=='configuration.defaults':
            from .draft_defaults import resolve_defaults
            key=json.dumps([args['workspace'],args.get('bundle') or ''],separators=(',',':'),ensure_ascii=False)
            try:
                result=await resolve_defaults(self.service.data_dir,args['workspace'],args.get('bundle'),self.service.state['settings'].get('appBundle'))
                result['phase']='ready'
            except Exception:
                result={'phase':'error','error':'Could not resolve this bundle’s model. Open model settings to choose a provider, or check the bundle configuration.'}
            async with self.service.lock:
                entries=self.service.state.setdefault('draftDefaults',{})
                entries[key]=result
                while len(entries)>32:entries.pop(next(iter(entries)))
                self.service._publish()
        elif action.startswith(('providers.','routing.')):
            from .setup import SetupManager
            session=({'workspace':str(Path(args['workspace']).expanduser().resolve())}
                     if action in {'providers.list','providers.models'} and args.get('workspace')
                     else self.configuration_session(args))
            async def runtime_operation(operation,values):
                current=self.session(args)
                await self.ensure_runtime(current)
                return await self.service.runtime.control(current['id'],operation,values)
            async def progress(values):
                async with self.service.lock:
                    self.service.state.setdefault('setup',{}).update(values)
                    self.service._publish()
            if self.setup_manager is None:
                self.setup_manager=SetupManager(self.service.data_dir,runtime_operation=runtime_operation,progress=progress,catalog=self.provider_catalog)
            else:
                self.setup_manager.runtime_operation=runtime_operation
                self.setup_manager.progress=progress
            manager=SetupManager(self.service.data_dir,catalog=self.provider_catalog,allow_missing_workspace=bool(args.get('workspace'))) if action in {'providers.list','providers.credentials','providers.schema','providers.models','providers.test','routing.list','routing.show'} else self.setup_manager
            probe_key=manager.catalog_key(args,session['workspace']) if action in {'providers.models','providers.schema'} else None
            result=await manager.perform(action,{**args,'workspace':session['workspace']})
            if action=='providers.list':result['providersRequestedWorkspace']=args.get('workspace',session['workspace'])
            if probe_key and probe_key!=manager.catalog_key(args,session['workspace']):return
            async with self.service.lock:
                setup=self.service.state.setdefault('setup',{})
                if action in {'providers.models','providers.schema'} and setup.get('providersWorkspace') not in {None,session['workspace']}:return
                if command_id and action.startswith('providers.'):
                    key=action+':'+(args.get('module') if action in {'providers.credentials','providers.schema'} else args.get('id','') or '')
                    if setup.get('operations',{}).get(key,{}).get('commandId')!=command_id:return
                if action=='providers.list' and setup.get('providersWorkspace')!=result.get('providersWorkspace'):
                    setup.update(modelCatalogs={},providerCatalogs={},metadata={},models=[],modelsProviderId=None)
                setup.update(result)
                if result.get('providerMetadata'):
                    setup.setdefault('metadata',{})[result['providerMetadata']['module']]=result['providerMetadata']
                if 'models' in result:
                    setup.setdefault('modelCatalogs',{})[result['modelsProviderId']]=result['models']
                    setup.setdefault('providerCatalogs',{})[result['modelsProviderId']]={'phase':'ready','models':result['models'],'supported':result.get('modelsSupported',True),'metadata':result.get('providerMetadata')}
                self.service._publish()
            if action in {'providers.list','providers.save','providers.remove','providers.move','providers.reorder','providers.loginStatus'}:
                async with self.service.lock:
                    self.service.state.setdefault('setup',{}).update(providers=manager.provider_rows(session['workspace']),providersWorkspace=session['workspace'],providersLoadedAt=time.time())
                    self.service._publish()
                self.background(self.warm_providers(manager,session['workspace']))
            if action in {'providers.save','providers.remove','providers.move','providers.reorder','routing.save','routing.use'}:await self.invalidate_configuration()
        elif action in {'bundle.preview', 'bundle.switch', 'bundle.fork'}:
            from .bundle_actions import perform
            await perform(self, action, args)
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
            workspace=self.configuration_session(args)['workspace']
            result=await manager.perform(action,{**args,'workspace':workspace},**kwargs)
            values={}
            if 'bundles' in result:values['bundles']=result['bundles']
            if 'registeredBundles' in result:values['registeredBundles']=result['registeredBundles']
            if 'discovery' in result:values['bundleDiscovery']=result['discovery']
            if values:await self.publish(**values)
            if action in {'bundles.add','bundles.toggle','bundles.remove','bundles.move','bundles.reorder'}:await self.invalidate_configuration()
            if result.get('content') is not None:await self.download(result.get('filename','custom-bundle.yaml'),result['content'],result.get('mimeType','text/yaml'))
            if action=='bundle.save' and result.get('saved'):
                saved=result['saved']
                async with self.service.lock:
                    self.service._session(session['id'])['bundle']=saved['name']
                    self.service._publish()
                await self.service.runtime.stop(session['id'])
        elif action=='configuration.cancel':
            identity=self.session({'sessionId':args['id']})['id']
            task=self.pending_config_tasks.get(identity)
            if self.service._session(identity).get('pendingConfiguration',{}).get('phase')=='applying':raise ValueError('These changes are already applying.')
            if task:task.cancel()
            self.queued_path(identity).unlink(missing_ok=True)
            async with self.service.lock:
                self.service._session(identity).pop('pendingConfiguration',None);self.service._publish()
        elif action in {'configuration.inspect','configuration.apply'}:
            session=self.session({'sessionId':args['id']})
            if action=='configuration.apply' and args.get('whenIdle') and not self.configuration_idle(session):
                from .runtime_controls import validate_plan
                from .host.config import write_private
                validate_plan(args['config'])
                write_private(self.queued_path(session['id']),json.dumps(args['config']))
                async with self.service.lock:
                    self.service._session(session['id'])['pendingConfiguration']={'phase':'queued','detail':'Waiting for the current turn and worker lanes to finish.'}
                    self.service._publish()
                if session['id'] not in self.pending_config_tasks:
                    task=asyncio.create_task(self.apply_queued(session['id']))
                    self.pending_config_tasks[session['id']]=task
                    self.service.tasks.add(task);task.add_done_callback(self.service.tasks.discard)
                return
            forwarded={'sessionId':args['id'],'operation':action,'args':{'config':args['config']} if 'config' in args else {}}
            try:
                await self.perform('runtime.control',forwarded)
            except Exception as exc:
                if action=='configuration.apply':
                    async with self.service.lock:
                        self.service._session(session['id'])['pendingConfiguration']={'phase':'error','error':str(exc)[:1000]}
                        self.service._publish()
                raise
            if action=='configuration.apply':
                self.queued_path(session['id']).unlink(missing_ok=True)
                async with self.service.lock:
                    self.service._session(session['id'])['pendingConfiguration']={'phase':'ready','detail':'Changes are applied to the loaded session.','appliedAt':time.time()}
                    self.service._publish()
        elif action=='runtime.control':
            if args['operation'].startswith('bundle.'):
                raise ValueError('Use the bundle actions to preview, switch, or fork a root bundle.')
            session=self.session(args)
            mutating=args['operation'] in {'configuration.apply','configuration.toggle','context.clear','provider.select','provider.reset','native.compact'}
            if mutating:
                async with self.service.lock:
                    current=self.service._session(session['id'])
                    if not self.configuration_idle(current):
                        raise ValueError('Finish active work before changing this conversation configuration')
                    current['configurationBusy']=True
                    self.service._publish()
            try:
                await self.ensure_runtime(session)
                result=await self.service.runtime.control(session['id'],args['operation'],args.get('args',{}))
                if args['operation']=='configuration.providerModels' and args.get('args',{}).get('refresh'):
                    provider_id=args['args'].get('instance') or args['args'].get('provider')
                    rows=self.service.state.get('runtimeControl',{}).get(session['id'],{}).get('configuration.providers',{}).get('providers',[])
                    row=next((row for row in rows if row['id']==provider_id),{})
                    if row.get('catalogKey'):self.provider_catalog.entries[('mounted',row['catalogKey'])]=(copy.deepcopy(result),None)
                if result.get('requiresRestart'):
                    await self.service.runtime.stop(session['id'])
                    await self.service.runtime.start(session,self.service.on_runtime_event)
                    inspected=await self.service.runtime.control(session['id'],'configuration.inspect',{})
                    result['configuration']=inspected
                refreshed={}
                if result.get('requiresRestart'):refreshed['configuration.providers']=await self.service.runtime.control(session['id'],'configuration.providers',{})
                refresh={'native.compact':'native.status','mode.set':'mode.list','mode.clear':'mode.list','goals.set':'goals.get','goals.clear':'goals.get','budget.set':'budget.get','provider.select':'configuration.providers','provider.reset':'configuration.providers','configuration.toggle':'configuration.inspect'}.get(args['operation'])
                if refresh:refreshed[refresh]=await self.service.runtime.control(session['id'],refresh,{})
                async with self.service.lock:
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{}).update(refreshed)
                    self.service.state.setdefault('runtimeControl',{}).setdefault(session['id'],{})[args['operation']]=result
                    if args['operation'] in {'configuration.inspect','configuration.apply','configuration.toggle'}:
                        configuration=result.get('configuration',result)
                        self.service._session(session['id'])['configuration']=configuration
                    if args['operation']=='configuration.providerModels':
                        identity=args.get('args',{}).get('instance') or args.get('args',{}).get('provider')
                        self.service.state['runtimeControl'][session['id']].setdefault('modelCatalogs',{})[identity]={'phase':'ready','models':result.get('models',[]),'supported':result.get('supported',True)}
                    self.service._publish()
                provider_info=result if args['operation']=='configuration.providers' else refreshed.get('configuration.providers')
                if provider_info is not None:self.background(self.warm_runtime_models(session['id'],provider_info.get('providers',[]),provider_info.get('catalogRevision')))
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
            session=self.configuration_session(args);scope=args.get('scope','global')
            config=self.settings.read(session['workspace'],scope)
            values=config.get('overrides',{}).get('tool-filesystem',{}).get('config',{})
            await self.publish(permissions={'scope':scope,'allowed':values.get('allowed_write_paths',[]),'denied':values.get('denied_write_paths',[])})
        elif action=='permissions.save':
            session=self.configuration_session(args);scope=args.get('scope','global')
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
        elif action == 'history.shared.open':
            if not self.service.runtime:
                raise ValueError('The isolated runtime is unavailable.')
            result = await self.service.runtime.shared_state_probe({
                'version': 1, 'op': 'open', 'sessionId': args['id'],
                'workspace': args['workspace'], 'limit': 100})
            async with self.service.lock:
                session = next((s for s in self.service.state['sessions']
                                if s['id'] == result['id']), None)
                if session and Path(session['workspace']).resolve() != Path(result['workspace']):
                    raise ValueError('This session ID is already open for a different workspace.')
                if session is None:
                    session = self.service._new_session({
                        'title': result.get('name') or 'Shared conversation ' + result['id'][:8],
                        'workspace': result['workspace'], 'bundle': result['bundle']})
                    session['id'] = result['id']
                    self.service.state['sessions'].insert(0, session)
                if session['status'] not in {'starting', 'working', 'stopping'}:
                    session.update(status='idle', bundle=result['bundle'], messages=[],
                                   shared=True, sharedHistoryOffset=result['offset'],
                                   sharedHistoryTotal=result['totalMessages'])
                    for index, row in enumerate(result['messages'], result['offset']):
                        session['messages'].append({'id': uuid.uuid5(uuid.NAMESPACE_URL, f"shared:{result['workspace']}:{result['id']}:{index}").hex,
                            'role': row['role'], 'text': row['text'], 'via': 'chat', 'source': 'shared', 'createdAt': session['createdAt']})
                self.service.state['selectedSessionId'] = session['id']
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.service.state, session)
                # Only UI presentation is saved here. The next worker admission
                # locks and reads the complete common checkpoint, never bubbles.
                self.service._publish()
        elif action in {'history.shared.list','history.shared.view'}:
            if not self.service.runtime:
                raise ValueError('The isolated runtime is unavailable.')
            request={'version':1,'op':'list' if action.endswith('.list') else 'view',
                     'workspace':args.get('workspace') or self.service.default_workspace}
            if action.endswith('.view'):
                request.update(sessionId=args['id'],offset=args.get('offset',0),limit=args.get('limit',50))
            result=await self.service.runtime.shared_state_probe(request)
            await self.publish(sharedHistory=result)
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
                store=SessionStore.for_app(self.service.data_dir,session['workspace'])
                metadata={**metadata,'parent_id':None,'working_dir':session['workspace'],'bundle_name':session['bundle'],'imported_file':True,'jobs_replayed':False,'preserve_system':True}
                store.save(session['id'],rows,metadata,preserve_system=True)
                from .automatic_history import display_message
                for index,row in enumerate(rows):
                    visible=display_message(row,index,session)
                    if visible:self.service._message(session,visible['role'],visible['text'],source='import',nativeIndex=index)
                self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=session['id']
                self.service._publish()
        elif action=='history.import':
            identity=args['id']
            saved=SessionStore.find(self.service.data_dir,identity,self.service.default_workspace)
            if not saved:raise ValueError('The requested session was not found')
            rows,meta=saved
            async with self.service.lock:
                existing=next((s for s in self.service.state['sessions'] if s['id']==identity),None)
                if existing:self.service.state['selectedSessionId']=identity
                else:
                    workspace=meta.get('working_dir') or meta.get('workspace') or self.service.default_workspace
                    if not Path(workspace).is_dir():raise ValueError('Restore this session’s original workspace before opening it.')
                    session=self.service._new_session({'title':meta.get('name') or meta.get('title') or 'Imported conversation','workspace':workspace,'bundle':(meta.get('bundle_name') or meta.get('bundle') or 'anchors').removeprefix('bundle:')})
                    session['id']=identity;session['status']='stopped'
                    from .automatic_history import display_message
                    for index,row in enumerate(rows):
                        visible=display_message(row,index,session)
                        if visible:self.service._message(session,visible['role'],visible['text'],source='import',nativeIndex=index)
                    self.service.state['sessions'].insert(0,session);self.service.state['selectedSessionId']=identity
                self.service._publish()
        elif action=='history.export':
            session=self.session(args)
            if session.get('nativeProject'):
                from .automatic_history import directory
                path=directory(session)
                from amplifier_foundation.session.history import SessionHistoryStore
                history=SessionHistoryStore(path).load(include_events=False)
                saved=(history.messages,history.metadata)
                await self.publish(historyDiagnostics=[{'code':d.code,'source':d.source,'line':d.line,'severity':d.severity} for d in history.diagnostics])
            else:
                store=SessionStore.for_app(self.service.data_dir,session['workspace'])
                saved=store.load(session.get('runtimeSessionId') or session['id'])
            if not saved:raise ValueError('This conversation has no runtime transcript yet')
            rows,metadata=saved
            if args.get('format')=='jsonl':
                await self.download('amplifier-transcript.jsonl',''.join(json.dumps(m)+'\n' for m in rows),'application/x-ndjson')
            else:await self.download('amplifier-session.json',json.dumps({'messages':rows,'metadata':metadata},indent=2))
        elif action=='history.cleanup':
            cutoff=time.time()-args.get('days',30)*86400
            async with self.service.lock:
                eligible=[s for s in self.service.state['sessions'] if s['id']!=self.service.state['selectedSessionId'] and s['status'] in {'idle','stopped','interrupted','error'} and max([s.get('createdAt',0),s.get('updatedAt',0)]+[m.get('createdAt',0) for m in s['messages']])<cutoff]
                if args.get('apply'):
                    identities={s['id'] for s in eligible}
                    for session in eligible:
                        self.service.history.hide_session(session)
                    self.service.state['sessions']=[s for s in self.service.state['sessions'] if s['id'] not in identities]
                    if args.get('purge'):
                        import shutil
                        store=SessionStore(self.service.data_dir/'sessions')
                        for identity in identities:
                            path=store.directory(identity)
                            if path.exists():shutil.rmtree(path)
                self.service.state['cleanupPreview']={'sessions':[{'id':s['id'],'title':s['title']} for s in eligible],'applied':bool(args.get('apply')),'detail':'Conversation list cleaned. Shared CLI transcripts and event files are retained.'}
                self.service._publish()
        elif action=='maintenance.restoreResource':
            from .resource_files import restore
            async with self.service.lock:
                roots=[self.service._state,*self.service.clients.records.values()]
                result=restore(self.service.db,roots,args['id'],args['value'])
                self.service.state.setdefault('maintenance',{})['resourceRecovery']=result
                self.service._publish()
        elif action=='maintenance.backup':
            from .recovery import backup
            result=await backup(self.service)
            await self.publish(maintenance=result)
        elif action=='maintenance.reset':
            from .recovery import reset
            await reset(self,args)
        elif action=='maintenance.repair':
            updates = self.service.update_manager
            async with self.service.runtime_lifecycle():
                candidate = previous_phase = None
                try:
                    async with self.service.lock:
                        if self.service.closed:
                            raise RuntimeError('The runtime host is closing.')
                        if updates and updates.lock.locked():
                            raise ValueError('Finish active updates before repairing the runtime')
                        if updates and updates.busy():
                            raise ValueError('Finish active work before repairing the runtime')
                        candidate = self.service.runtime_candidate()
                        previous_phase = self.service.state.setdefault('updates', {}).get('phase', 'idle')
                        self.service.state['updates'].update(phase='activating',
                            detail='Repairing runtime dependencies…')
                        self.service._publish()
                    from .updates import process
                    from .runtime import RuntimeManager
                    command=RuntimeManager()._command()
                    project=RuntimeManager.project_path(command)
                    await process(command[0],'sync',*(('--locked',) if '--locked' in command else ()),
                                  '--project',project,'--python','3.13','--reinstall',timeout=900)
                    await self.service.replace_runtime(candidate)
                except BaseException:
                    await self.service.discard_runtime(candidate)
                    if previous_phase is not None:
                        async with self.service.lock:
                            self.service.state['updates']['phase'] = previous_phase
                            self.service._publish()
                    raise
                async with self.service.lock:
                    self.service.state['updates']['phase'] = previous_phase
                    self.service._publish()
            await self.publish(maintenance={'detail':'Runtime dependencies repaired. Conversations will resume when you next send a message.'})
        else:
            raise ValueError('Management action is not implemented: '+action)

    def history(self,legacy=False):
        from .session_files import amplifier_home
        rows=[]
        from amplifier_foundation.session.history import SessionHistoryStore
        paths=sorted({path.parent/'metadata.json' for name in ('metadata.json','metadata.json.backup')
                      for path in (amplifier_home()/'projects').glob('*/sessions/*/'+name)})
        seen={path.parent.name for path in paths}
        paths.extend(path for path in (self.service.data_dir/'sessions').glob('*/metadata.json') if path.parent.name not in seen)
        for path in paths:
            try:
                meta=SessionHistoryStore(path.parent).load_metadata()
                rows.append({'id':path.parent.name,'title':meta.get('name') or meta.get('title') or meta.get('bundle_name') or path.parent.name,'workspace':meta.get('working_dir') or meta.get('workspace'),'updatedAt':meta.get('updated_at'),'legacy':False,'parentId':meta.get('parent_id')})
            except (OSError,ValueError):continue
        return rows
