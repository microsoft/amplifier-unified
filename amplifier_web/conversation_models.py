"""Conversation model browsing never starts a session or reads its transcript."""
import asyncio
import copy


def bind_runtime(service):
    bind = getattr(service.runtime, 'bind_configuration_revision', None)
    if callable(bind):
        bind(lambda: service.state.get('configurationRevision', 0))


def reported_selection(service, session):
    """Only a report prepared from current settings can update next-input defaults."""
    if (session.get('runtimeReportConfigurationRevision', 0) != service.state.get('configurationRevision', 0)
            or session.get('configurationPending') or session.get('configurationBusy')
            or session.get('pendingBundleSelection')):
        return None
    return session.get('runtimeReport', {}).get('effective_selection')


def accept_report(service, session, payload):
    """Reduce a host-observed report under the service lock, including warmup."""
    owner = payload.get('runtimeOwnerId')
    if owner is not None:
        row = getattr(service.runtime, 'workers', {}).get(session['id'], {})
        if (row.get('runtimeOwnerId') != owner or row.get('closing')
                or row['process'].returncode is not None):
            return
    session['runtimeReport'] = payload['report']
    session['runtimeReportConfigurationRevision'] = payload.get('configurationRevision', 0)
    if payload['report'].get('root_bundle'):
        session['bundle'] = payload['report']['root_bundle']
    effective = reported_selection(service, session)
    if effective:
        selected = session.get('pendingModelSelection') or session.get('selection')
        for key in ('configuration.catalog', 'configuration.providers'):
            catalog = service.state.get('runtimeControl', {}).get(session['id'], {}).get(key)
            if (catalog is not None
                    and catalog.get('configurationRevision', 0) == service.state.get('configurationRevision', 0)
                    and catalog.get('nextMessageRevision', 0) == session.get('nextMessageRevision', 0)):
                catalog['effective'] = copy.deepcopy(selected or effective)


def publish_catalogs(management, entries):
    from .model_catalogs import retain
    target = management.service.state.setdefault('modelCatalogs', {})
    keep = retain(management.service, entries)
    changed = any(target.get(key) != value for key, value in entries.items())
    target.update(entries)
    for key in list(target):
        if key not in keep:
            target.pop(key)
            changed = True
    return changed


async def browse(management, session, *, refresh=False):
    from .draft_defaults import resolve_defaults
    from .provider_catalog import model_key
    service = management.service
    sid = session['id']
    revision = service.state.get('configurationRevision', 0)
    desired_revision = session.get('nextMessageRevision', 0)
    pending_bundle = copy.deepcopy(session.get('pendingBundleSelection'))
    # The resolver only composes configuration and queries provider metadata.
    # It doesn't mount context, hooks, tools, load history, or acquire ownership.
    # Fresh workers publish connection identities with their effective selection.
    # Reuse those identities directly; browsing needs no separate per-chat probe.
    report=session.get('runtimeReport',{})
    previous=service.state.get('runtimeControl',{}).get(sid,{}).get('configuration.catalog',{})
    same_revision=(previous.get('configurationRevision',0)==revision
                   and previous.get('nextMessageRevision',0)==desired_revision)
    current_catalog=same_revision and previous.get('phase')=='ready'
    # A prior result at the current revision owns its identities. Never stamp an
    # old runtime report with the revision of a newer configuration discovery.
    choices=previous.get('providers',[]) if current_catalog else report.get('provider_choices',[]) if revision==0 and not previous and not pending_bundle else []
    cached={row.get('sharedCatalogKey'):management.provider_catalog.peek(model_key(row.get('sharedCatalogKey')))
            for row in choices if row.get('sharedCatalogKey')}
    if same_revision and not refresh and not session.get('configurationPending') and choices and all((cached.get(row.get('sharedCatalogKey')) or {}).get('providerMetadata') for row in choices):
        result={'providers':copy.deepcopy(choices) if current_catalog else [{'id':row['id'],'sharedCatalogKey':row['sharedCatalogKey'],
                   'info':{'display_name':row.get('display_name'),'defaults':{'model':row.get('model'),'reasoning_effort':row.get('effort')}}}
                  for row in choices], 'effective':previous.get('effective',{}) if current_catalog else report.get('effective_selection',{}),
                'catalogs':{key:{'phase':'ready','models':value.get('models',[]),'metadata':value['providerMetadata'],
                                'supported':value.get('modelsSupported',True)}
                            for key,value in cached.items()}}
    else:
        result = await resolve_defaults(service.data_dir, session['workspace'], pending_bundle['bundle'] if pending_bundle else session.get('bundle'),
        service.state['settings'].get('appBundle'), session_id=session.get('runtimeSessionId') or session.get('nativeIdentity') or sid,
            catalog=True, refresh=refresh, desired_bundle=bool(pending_bundle),
            global_only=session.get('location',{}).get('kind')=='managed')
    entries = result.pop('catalogs', {})
    async with service.lock:
        if (revision != service.state.get('configurationRevision', 0)
                or desired_revision != service._session(sid, hydrate=False).get('nextMessageRevision', 0)):
            return
        for identity, entry in entries.items():
            if entry.get('phase') == 'ready':
                value = {'models': entry['models'], 'providerMetadata': entry['metadata'],
                         'modelsSupported': entry.get('supported', True)}
                if refresh or management.provider_catalog.peek(model_key(identity)) != value:
                    management.provider_catalog.put(model_key(identity), value)
        current = service._session(sid)
        selection = current.get('selection') if 'selection' in current else result.get('selection')
        result.update(effective=current.get('pendingModelSelection') or selection or reported_selection(service, current) or result.get('effective', {}),
                      pinned=bool(selection), phase='ready', configurationRevision=revision,
                      nextMessageRevision=desired_revision)
        control = service.state.setdefault('runtimeControl', {}).setdefault(sid, {})
        changed = control.get('configuration.catalog') != result
        control['configuration.catalog'] = result
        catalog_changed = publish_catalogs(management, entries)
        if changed or catalog_changed:
            service._publish_changes(sessions={sid} if changed else set(),
                                     globals={'modelCatalogs'} if catalog_changed else set())


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
        current['nextMessageRevision'] = current.get('nextMessageRevision', 0) + 1
        current['nextMessageStatus'] = 'saved'
        current.pop('nextMessageError', None)
        current.pop('nextMessageFailure', None)
        if current.get('pendingBundleSelection'):
            current['pendingBundleSelection']['resetModel'] = False
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
    service=management.service
    requested=getattr(management,'conversation_catalog_demand',None)
    if requested is None:
        requested=management.conversation_catalog_demand={}
    demand=(service.state.get('configurationRevision',0),refresh,session.get('nextMessageRevision',0))
    if sid in pending:
        if requested.get(sid)!=(demand[0],False,demand[2]) or refresh:
            requested[sid]=(demand[0],refresh or requested.get(sid,(None,False))[1],demand[2])
        return
    requested[sid]=demand
    async def run():
        try:
            while True:
                observed=requested[sid]
                try:
                    await browse(management,service._session(sid),refresh=observed[1])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A failing obsolete read cannot poison a newer request.
                    if requested[sid]!=observed:
                        continue
                    async with service.lock:
                        if (observed[0]==service.state.get('configurationRevision',0)
                                and observed[2]==service._session(sid, hydrate=False).get('nextMessageRevision',0)):
                            control=service.state.setdefault('runtimeControl',{}).setdefault(sid,{})
                            control.setdefault('configuration.catalog',{}).update(phase='error',error='Could not load model settings. Your selection is preserved. Refresh models to retry.')
                            service._publish_changes(sessions={sid})
                if requested[sid]==observed:
                    break
        finally:
            pending.pop(sid,None)
            requested.pop(sid,None)
    task=asyncio.create_task(run());pending[sid]=task
    service.tasks.add(task);task.add_done_callback(service.tasks.discard)
