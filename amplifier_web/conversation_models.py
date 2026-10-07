"""Conversation model browsing never starts a session or reads its transcript."""
import asyncio
import copy


def publish_catalogs(management, entries):
    target = management.service.state.setdefault('modelCatalogs', {})
    target.update(entries)
    while len(target) > 128:
        target.pop(next(iter(target)))


async def browse(management, session, *, refresh=False):
    from .draft_defaults import resolve_defaults
    from .provider_catalog import model_key
    service = management.service
    sid = session['id']
    revision = service.state.get('configurationRevision', 0)
    # The resolver only composes configuration and queries provider metadata.
    # It doesn't mount context, hooks, tools, load history, or acquire ownership.
    # Fresh workers publish connection identities with their effective selection.
    # Reuse those identities directly; browsing needs no separate per-chat probe.
    report=session.get('runtimeReport',{})
    choices=report.get('provider_choices',[])
    cached={row.get('sharedCatalogKey'):management.provider_catalog.peek(model_key(row.get('sharedCatalogKey')))
            for row in choices if row.get('sharedCatalogKey')}
    previous=service.state.get('runtimeControl',{}).get(sid,{}).get('configuration.catalog',{})
    same_revision=previous.get('configurationRevision',0)==revision
    if same_revision and not refresh and not session.get('configurationPending') and choices and all((cached.get(row.get('sharedCatalogKey')) or {}).get('providerMetadata') for row in choices):
        result={'providers':[{'id':row['id'],'sharedCatalogKey':row['sharedCatalogKey'],
                   'info':{'display_name':row.get('display_name'),'defaults':{'model':row.get('model'),'reasoning_effort':row.get('effort')}}}
                  for row in choices], 'effective':report.get('effective_selection',{}),
                'catalogs':{key:{'phase':'ready','models':value.get('models',[]),'metadata':value['providerMetadata']}
                            for key,value in cached.items()}}
    else:
        result = await resolve_defaults(service.data_dir, session['workspace'], session.get('bundle'),
        service.state['settings'].get('appBundle'), session_id=session.get('runtimeSessionId') or session.get('nativeIdentity') or sid,
            catalog=True, refresh=refresh, global_only=session.get('location',{}).get('kind')=='managed')
    entries = result.pop('catalogs', {})
    for identity, entry in entries.items():
        if entry.get('phase') == 'ready':
            value = {'models': entry['models'], 'providerMetadata': entry['metadata'],
                     'modelsSupported': entry.get('supported', True)}
            if refresh or management.provider_catalog.peek(model_key(identity)) != value:
                management.provider_catalog.put(model_key(identity), value)
    async with service.lock:
        if revision != service.state.get('configurationRevision', 0):
            return
        current = service._session(sid)
        selection = current.get('selection') or result.get('selection')
        result.update(effective=selection or result.get('effective', {}), pinned=bool(selection), phase='ready', configurationRevision=revision)
        service.state.setdefault('runtimeControl', {}).setdefault(sid, {})['configuration.catalog'] = result
        publish_catalogs(management, entries)
        service._publish_changes(sessions={sid}, globals={'modelCatalogs'})


async def select(management, session, requested):
    from .new_chat import selection
    service = management.service
    selected = selection(requested)
    if not selected:
        raise ValueError('Choose a provider and model')
    async with service.lock:
        current = service._session(session['id'])
        if not management.configuration_idle(current):
            raise ValueError('Finish active work before changing this conversation model')
        # Validation of mounted capabilities and compaction happens on Send.
        # Persist the explicit intent before touching any worker.
        current['selection'] = copy.deepcopy(selected)
        current['pendingModelSelection'] = copy.deepcopy(selected)
        control = service.state.setdefault('runtimeControl', {}).setdefault(current['id'], {})
        for key in ('configuration.catalog', 'configuration.providers'):
            if key in control:
                control[key].update(effective=copy.deepcopy(selected), selection=copy.deepcopy(selected), pinned=True)
        service._publish_changes(sessions={current['id']})


async def request_browse(management, session, *, refresh=False):
    pending=getattr(management,'conversation_catalog_requests',None)
    if pending is None:
        pending=management.conversation_catalog_requests={}
    sid=session['id']
    if sid in pending:return
    service=management.service
    async def run():
        try:
            await browse(management,session,refresh=refresh)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with service.lock:
                control=service.state.setdefault('runtimeControl',{}).setdefault(sid,{})
                control.setdefault('configuration.catalog',{}).update(phase='error',error='Could not load model settings. Your selection is preserved. Refresh models to retry.')
                service._publish_changes(sessions={sid})
        finally:pending.pop(sid,None)
    task=asyncio.create_task(run());pending[sid]=task
    service.tasks.add(task);task.add_done_callback(service.tasks.discard)
