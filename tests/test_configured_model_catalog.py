"""Shared selection inventory uses real host cache/action paths, no cached_probe shim."""
import asyncio
from collections import Counter
from copy import deepcopy

import pytest
import yaml

from amplifier_web.conversation_models import browse
from amplifier_web.host.config import write_private
from amplifier_web.management import Management
from amplifier_web.next_message_configuration import validate_selection
from amplifier_web.provider_catalog import ProviderCatalog, model_key
from amplifier_web.service import AppService
from amplifier_web.session_files import amplifier_home
from amplifier_web.setup import SetupManager


@pytest.fixture
async def catalog_app(tmp_path, monkeypatch):
    rows = [
        {'id': 'one', 'module': 'provider-fixture', 'config': {'base_url': 'https://one.test', 'default_model': 'not-discovered'}},
        {'id': 'two', 'module': 'provider-fixture', 'config': {'base_url': 'https://two.test', 'default_model': 'also-not-discovered'}},
        {'id': 'broken', 'module': 'provider-fixture', 'config': {'base_url': 'https://broken.test'}},
    ]
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': rows}}))
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.management = Management(service)
    for title in ('first', 'second'):
        await service.dispatch('session.create', {'title': title})
        service._session()['selection']={'instance':'one','model':'one-real'}
    calls = Counter()
    state = {'failure': False, 'empty': False}
    async def probe(self, action, args, workspace):
        calls[args['id']] += 1
        await asyncio.sleep(.01)
        if args['id'] == 'broken' or state['failure']:
            raise ValueError('PRIVATE credential must not appear')
        return {'models': [] if state['empty'] else [{'id': args['id'] + '-real'}],
                'modelsSupported': True, 'modelsProvenance': 'provider-api',
                'providerMetadata': {'module': 'provider-fixture', 'info': {}, 'configSchema': {'fields': []}}}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    yield service, calls, state, rows
    await service.close()


async def test_two_chats_and_settings_share_one_probe_and_no_fallback_inventory(catalog_app):
    service, calls, _, _ = catalog_app
    chats = list(service.state['sessions'])[-2:]
    chats[0]['selection'] = {'instance': 'one', 'model': 'saved-pin'}
    chats[1]['selection'] = {'instance': 'two', 'model': 'other-pin'}
    before = [deepcopy(row) for row in chats]
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    results = await asyncio.gather(browse(service.management, chats[0]), browse(service.management, chats[1]),
                                   manager.discover_catalog(service.default_workspace))
    assert calls == {'one': 1, 'two': 1, 'broken': 1}
    controls = [service.state['runtimeControl'][row['id']]['configuration.catalog'] for row in chats]
    assert controls[0]['providers'] == controls[1]['providers'] == results[2]['providers']
    assert [row['id'] for row in controls[0]['providers']] == ['one', 'two', 'broken']
    entries = results[2]['catalogs']
    by_id = {row['id']: entries[row['sharedCatalogKey']] for row in results[2]['providers']}
    assert by_id['one']['models'] == [{'id': 'one-real'}]
    assert by_id['two']['models'] == [{'id': 'two-real'}]
    assert by_id['broken']['phase'] == 'error'
    assert 'PRIVATE' not in str(by_id)
    assert [row['effective'] for row in controls] == [chat['selection'] for chat in chats]
    assert chats == before
    assert service.runtime is None


async def test_restart_age_reopen_failure_and_explicit_refresh(catalog_app):
    service, calls, state, _ = catalog_app
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    original = await manager.discover_catalog(service.default_workspace)
    key = model_key(original['providers'][0]['sharedCatalogKey'])
    now = service.management.provider_catalog.clock() + 10 * 86400
    restarted = ProviderCatalog(service.management.provider_catalog.path, clock=lambda: now)
    service.management.provider_catalog = restarted
    manager.catalog = restarted
    await manager.discover_catalog(service.default_workspace)
    assert calls == {'one': 1, 'two': 1, 'broken': 1}, 'Neither age nor restart retries discovery'
    state['failure'] = True
    failed = await manager.discover_catalog(service.default_workspace, refresh=True, instance='one')
    entry = failed['catalogs'][key[1]]
    assert entry['phase'] == 'error' and entry['models'] == [{'id': 'one-real'}]
    assert entry['loadedAt'] < entry['failedAt']
    await manager.discover_catalog(service.default_workspace)
    assert calls == {'one': 2, 'two': 1, 'broken': 1}
    state.update(failure=False, empty=True)
    fresh = await manager.discover_catalog(service.default_workspace, refresh=True, instance='one')
    assert fresh['catalogs'][key[1]]['phase'] == 'ready'
    assert fresh['catalogs'][key[1]]['models'] == []
    assert calls == {'one': 3, 'two': 1, 'broken': 1}


async def test_late_identity_change_discards_old_models(catalog_app, monkeypatch):
    service, _, _, rows = catalog_app
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(self, action, args, workspace):
        entered.set(); await release.wait()
        return {'models': [{'id': 'old-account'}], 'modelsSupported': True}
    monkeypatch.setattr(SetupManager, 'probe', held)
    old = model_key(manager.catalog_key({'id':'one'}, service.default_workspace))
    pending = asyncio.create_task(manager.discover_catalog(service.default_workspace))
    await entered.wait()
    rows[0]['config']['base_url'] = 'https://replacement.test'
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': rows}}))
    release.set()
    with pytest.raises(ValueError, match='changed during discovery'):
        await pending
    assert manager.catalog.peek(old) is None
    assert manager.catalog.peek(model_key(manager.catalog_key({'id':'one'}, service.default_workspace))) is None


@pytest.mark.parametrize('entry', [{'phase':'error'}, {'phase':'ready','supported':False}, {}, {'metadata':{}}])
def test_manual_model_does_not_need_invented_option_or_clear_unknown_effort(entry):
    catalog={'providers':[{'id':'one','sharedCatalogKey':'key'}], 'catalogs':{'key':entry}}
    selected={'instance':'one','model':'explicit-deployment','effort':'saved-effort'}
    before=deepcopy(selected)
    validate_selection(selected,catalog)
    assert selected==before
    with pytest.raises(ValueError,match='provider is unavailable'):
        validate_selection({**selected,'instance':'not-configured'},catalog)


def test_authentic_list_does_not_add_configured_default():
    row={'id':'one','sharedCatalogKey':'key','info':{'defaults':{'model':'default'}}}
    entry={'phase':'ready','supported':True,'models':[{'id':'real'}],'metadata':{'configSchema':{'fields':[]}}}
    validate_selection({'instance':'one','model':'default'}, {'providers':[row],'catalogs':{'key':entry}})
    assert entry['models']==[{'id':'real'}], 'Manual selection does not invent a catalog row'


async def test_cache_cancelled_waiter_does_not_cancel_shared_load_and_refresh_coalesces():
    cache=ProviderCatalog(); entered,release=asyncio.Event(),asyncio.Event(); calls=0
    async def load():
        nonlocal calls
        calls+=1;entered.set();await release.wait();return {'models':[]}
    first=asyncio.create_task(cache.get(('providers.models','same'),load,refresh=True))
    await entered.wait()
    second=asyncio.create_task(cache.get(('providers.models','same'),load,refresh=True))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    release.set();assert await second=={'models':[]};assert calls==1
    await cache.close()


async def test_cancelled_reader_cannot_persist_changed_identity(catalog_app, monkeypatch):
    service, _, _, rows = catalog_app
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    entered, release=asyncio.Event(),asyncio.Event()
    async def probe(self,action,args,workspace):
        entered.set();await release.wait()
        return {'models':[{'id':'wrong-account'}]}
    monkeypatch.setattr(SetupManager,'probe',probe)
    key=model_key(manager.catalog_key({'id':'one'},service.default_workspace))
    reader=asyncio.create_task(manager.cached_probe('providers.models',{'id':'one'},service.default_workspace))
    await entered.wait()
    load=manager.catalog.pending[key]
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):await reader
    rows[0]['config']['base_url']='https://changed.test'
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows}}))
    release.set()
    from amplifier_web.provider_catalog import CatalogInvalidated
    with pytest.raises(CatalogInvalidated):await load
    assert manager.catalog.peek(key) is None
    assert ProviderCatalog(manager.catalog.path).peek(key) is None


async def test_queued_probe_checks_identity_before_capturing_request(catalog_app, monkeypatch):
    service, _, _, rows=catalog_app
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    manager.catalog.load_limit=asyncio.Semaphore(0)
    calls=[]
    async def probe(*args,**kwargs):calls.append(1);return {'models':[{'id':'wrong'}]}
    monkeypatch.setattr(SetupManager,'probe',probe)
    key=model_key(manager.catalog_key({'id':'one'},service.default_workspace))
    task=asyncio.create_task(manager.cached_probe('providers.models',{'id':'one'},service.default_workspace))
    await asyncio.sleep(0)
    rows[0]['config']['base_url']='https://different.test'
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows}}))
    manager.catalog.load_limit.release()
    from amplifier_web.provider_catalog import CatalogInvalidated
    with pytest.raises(CatalogInvalidated):await task
    assert calls==[] and manager.catalog.peek(key) is None


async def test_cache_never_retains_raw_provider_failure_body(tmp_path):
    cache=ProviderCatalog(tmp_path/'catalog.json')
    async def bad():raise RuntimeError('https://secret-user:secret-password@private.test')
    with pytest.raises(RuntimeError):await cache.get(('providers.models','key'),bad)
    assert 'secret' not in cache.path.read_text()
    assert 'RuntimeError' in cache.entry(('providers.models','key'))['error']


async def test_shared_roster_marks_conversation_override_ineligible(catalog_app):
    import json
    service, _, _, rows=catalog_app
    chat=service._session()
    override=service.data_dir/'sessions'/chat['id']/'configuration.json'
    changed=deepcopy(rows);changed[0]['config']['base_url']='https://private-conversation.test'
    write_private(override,json.dumps({'providers':changed}))
    await browse(service.management,chat)
    result=service.state['runtimeControl'][chat['id']]['configuration.catalog']
    assert result['connectionBindings']['one']['eligible'] is False
    assert 'two' not in result['connectionBindings']
    assert [row['id'] for row in result['providers']]==['one','two','broken']
    assert 'private-conversation' not in str(result)
    assert json.loads(override.read_text())['providers']==changed


async def test_runtime_rejects_same_id_different_account_before_selecting():
    from types import SimpleNamespace
    from amplifier_web.runtime_controls import RuntimeControls
    loop=SimpleNamespace(root_provider=None)
    mounted={'one':object()}
    capabilities={'web.provider_catalog':{'connectionKeys':{'one':'mounted-account'}}}
    co=SimpleNamespace(config={},get=lambda name: {'providers':mounted,'orchestrator':loop}.get(name),
        get_capability=capabilities.get,register_capability=capabilities.__setitem__)
    controls=RuntimeControls(SimpleNamespace(coordinator=co,session_id='chat',config={}),
        SimpleNamespace(generation=None,queued_inputs=0))
    try:
        with pytest.raises(ValueError,match='mounted connection differs'):
            await controls.perform('provider.select',{'instance':'one','model':'same-model','expectedConnectionKey':'configured-account'})
        assert loop.root_provider is None
        with pytest.raises(ValueError,match='mounted connection differs'):
            await controls.perform('provider.verifyConnection',{'instance':'one','expectedConnectionKey':'configured-account'})
    finally:await controls.close()


async def test_discovery_applies_effective_overrides_without_adding_connections(catalog_app, monkeypatch):
    service, _, _, rows=catalog_app
    write_private(amplifier_home()/'settings.yaml',yaml.safe_dump({'config':{'providers':rows},
        'overrides':{'provider-fixture':{'config':{'extra':'discovery-option'}},
                     'one':{'config':{'base_url':'https://override.test'}},
                     'two':{'enabled':False},
                     'not-configured':{'config':{'base_url':'https://not-an-option.test'}}}}))
    manager=SetupManager(service.data_dir,catalog=service.management.provider_catalog)
    snapshots=[]
    async def probe(self,action,args,workspace):
        snapshots.append(self.probe_snapshot(action,args,workspace))
        return {'models':[],'modelsSupported':True}
    monkeypatch.setattr(SetupManager,'probe',probe)
    result=await manager.discover_catalog(service.default_workspace)
    assert [row['id'] for row in result['providers']]==['one','broken']
    assert {row['config']['base_url'] for row in snapshots}=={'https://override.test','https://broken.test'}
    assert all(row['config']['extra']=='discovery-option' for row in snapshots)
    assert result['providers'][0]['sharedCatalogKey']==manager.catalog_key({'id':'one'},service.default_workspace)


async def test_queued_selection_updates_configured_binding(catalog_app):
    from amplifier_web.conversation_models import select
    from amplifier_web.provider_catalog import configured_binding
    service, _, _, _=catalog_app
    chat=service._session()
    await select(service.management,chat,{'instance':'one','model':'one-real'})
    first=chat['modelConnectionKey']
    await select(service.management,chat,{'instance':'two','model':'two-real'})
    assert chat['modelConnectionKey']!=first
    assert chat['modelConnectionKey']==configured_binding(service.data_dir,chat['workspace'],'two')


@pytest.mark.parametrize('listing', ['recommendations','empty','failed'])
async def test_hidden_exact_pending_selection_survives_display_and_input_preparation(catalog_app, listing):
    from amplifier_web.conversation_models import select
    from amplifier_web.next_message_configuration import prepare
    service, _, state, _=catalog_app
    state.update(empty=listing=='empty',failure=listing=='failed')
    chat=service._session()
    chosen={'instance':'one','model':'not-recommended'}
    await select(service.management,chat,chosen)
    binding=chat['modelConnectionKey']
    revision=chat['nextMessageRevision']
    await browse(service.management,chat,refresh=True)
    prepared,guard=await prepare(service,chat)
    guard()
    assert chat['selection']==chat['pendingModelSelection']==prepared['selection']==chosen
    assert chat['modelConnectionKey']==binding==prepared['modelConnectionKey']
    assert chat['nextMessageRevision']==revision
    assert all(chosen['model'] not in [row['id'] for row in entry['models']]
               for entry in service.state['modelCatalogs'].values())


@pytest.mark.parametrize('flag', ['hide_dated_models', 'filtered'])
async def test_legacy_visibility_edit_preserves_saved_exact_choice_and_only_refetches_changed_catalog(
        catalog_app, flag):
    import json
    from types import SimpleNamespace
    from amplifier_web.conversation_models import select
    from amplifier_web.next_message_configuration import prepare
    from amplifier_web.provider_catalog import mounted_catalog_keys, require_mounted_binding

    service, calls, _, rows = catalog_app
    chat = service._session()
    rows[0].update(module='provider-openai', source='fixture-source')
    rows[0]['config'][flag] = False
    path = amplifier_home() / 'settings.yaml'
    write_private(path, yaml.safe_dump({'config': {'providers': rows}}))
    chosen = {'instance': 'one', 'model': 'hidden-exact-2026-01-01'}
    await select(service.management, chat, chosen)
    binding, revision = chat['modelConnectionKey'], chat['nextMessageRevision']
    saved = service.data_dir / 'sessions' / chat['id'] / 'configuration.json'
    original_plan = {'providers': deepcopy(rows)}
    write_private(saved, json.dumps(original_plan))
    # Capture a real mounted identity before the settings-only edit.
    sources = {'one': 'fixture-source'}
    mounted = deepcopy(rows[:1])
    coordinator = SimpleNamespace(config={'providers': mounted}, get_capability=lambda _: {
        'home': service.data_dir, 'workspace': chat['workspace'], 'sources': sources,
        'connectionKeys': mounted_catalog_keys(service.data_dir, chat['workspace'], mounted, sources, binding=True)})
    await browse(service.management, chat)
    before = service.state['runtimeControl'][chat['id']]['configuration.catalog']
    old_key = before['providers'][0]['sharedCatalogKey']
    assert calls == {'one': 1, 'two': 1, 'broken': 1}

    rows[0]['config'][flag] = True
    write_private(path, yaml.safe_dump({'config': {'providers': rows}}))
    await browse(service.management, chat)  # No forced refresh; identity must invalidate.
    after = service.state['runtimeControl'][chat['id']]['configuration.catalog']
    assert after['providers'][0]['sharedCatalogKey'] != old_key
    assert after['providers'][0]['connectionKey'] == binding
    assert after['connectionBindings'] == {}
    assert calls == {'one': 2, 'two': 1, 'broken': 1}
    for _ in range(3):
        await browse(service.management, chat)
    assert calls == {'one': 2, 'two': 1, 'broken': 1}, 'Warm reads must not repeat discovery'
    prepared, guard = await prepare(service, chat)
    guard()
    require_mounted_binding(coordinator, 'one', prepared['modelConnectionKey'])
    assert chat['selection'] == chat['pendingModelSelection'] == prepared['selection'] == chosen
    assert chat['modelConnectionKey'] == prepared['modelConnectionKey'] == binding
    assert chat['nextMessageRevision'] == revision
    assert json.loads(saved.read_text()) == original_plan
    assert all(chosen['model'] not in [row['id'] for row in entry['models']]
               for entry in service.state['modelCatalogs'].values())


@pytest.mark.parametrize('filters', [None, {}])
@pytest.mark.parametrize('field', ['hide_dated_models', 'filtered'])
async def test_unresolved_legacy_visibility_cache_error_and_fixed_inputs_stay_separate(
        catalog_app, monkeypatch, filters, field):
    from amplifier_web.provider_environment import materialize_provider_config
    service, _, _, rows = catalog_app
    rows[0].update(module='provider-openai', source='fixture-source')
    rows[0]['config'].update(hide_dated_models=True, model_filters=filters)
    rows[0]['config'][field] = '${UNSET_LEGACY_VISIBILITY}'
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': rows}}))
    monkeypatch.delenv('UNSET_LEGACY_VISIBILITY', raising=False)
    calls = []
    async def probe(self, action, args, workspace):
        calls.append(1)
        # Real pre-construction materialization, even for a shadowed alias.
        materialize_provider_config(self.probe_snapshot(action, args, workspace)['config'], {'fields': []})
        return {'models': [{'id': 'real'}], 'modelsSupported': True}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    manager = SetupManager(service.data_dir, catalog=service.management.provider_catalog)
    failed = await manager.discover_catalog(service.default_workspace, instance='one', selected_only=True)
    failed_key = failed['providers'][0]['sharedCatalogKey']
    assert failed['catalogs'][failed_key]['phase'] == 'error'
    monkeypatch.setenv('UNSET_LEGACY_VISIBILITY', 'true')
    fixed = await manager.discover_catalog(service.default_workspace, instance='one', selected_only=True)
    fixed_key = fixed['providers'][0]['sharedCatalogKey']
    assert fixed_key != failed_key
    assert fixed['catalogs'][fixed_key]['phase'] == 'ready'
    monkeypatch.delenv('UNSET_LEGACY_VISIBILITY')
    again = await manager.discover_catalog(service.default_workspace, instance='one', selected_only=True)
    assert again['providers'][0]['sharedCatalogKey'] == failed_key
    assert again['catalogs'][failed_key]['phase'] == 'error'
    assert calls == [1, 1], 'Reuse only the matching error/success, never the other construction state'


@pytest.mark.parametrize('flag', ['hide_dated_models', 'filtered'])
@pytest.mark.parametrize('intent', ['pending', 'applied', 'initial'])
async def test_pre_fix_persisted_choice_adopts_only_proven_current_connection(catalog_app, monkeypatch, flag, intent):
    import json
    from types import SimpleNamespace
    from amplifier_web.next_message_configuration import bind, applied
    from amplifier_web.provider_catalog import fingerprint, mounted_catalog_keys, require_mounted_binding
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    from amplifier_web.setup import PROVIDER_ENV

    service, _, _, rows = catalog_app
    chat = service._session()
    for name in PROVIDER_ENV['provider-openai']:
        monkeypatch.delenv(name, raising=False)
    source = 'git+https://fixture.invalid/openai@' + 'a' * 40
    row = {'id': 'one', 'module': 'provider-openai', 'source': source,
           'config': {'api_key': 'synthetic-account', 'base_url': 'https://fixture.invalid', flag: False}}
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': [row]}}))
    # Persist an independently calculated PRE-FIX binding and exact choice, then
    # read those bytes back as the startup snapshot under the candidate source.
    previous = fingerprint(['provider-connection-v1', None, row['module'], source, row['config'],
                            {name: None for name in PROVIDER_ENV['provider-openai']}, {}])
    chosen = {'instance': 'one', 'model': 'hidden-exact-2026-01-01'}
    chat.update(selection=chosen, modelConnectionKey=previous)
    chat.pop('pendingModelSelection', None)
    chat.pop('nextMessageRevision', None)
    if intent != 'initial':
        chat['nextMessageRevision'] = 1
    if intent == 'pending':
        chat['pendingModelSelection'] = chosen
    if intent == 'applied':
        chat['appliedConfigurationRevision'] = 1
    saved = service.data_dir / 'sessions' / chat['id'] / 'configuration.json'
    write_private(saved, json.dumps({'providers': [row]}))
    saved_bytes = saved.read_bytes()
    startup_path = service.data_dir / 'pre-fix-choice.json'
    startup = {key: deepcopy(chat[key]) for key in (
        'id', 'workspace', 'selection', 'modelConnectionKey', 'nextMessageRevision', 'pendingModelSelection') if key in chat}
    write_private(startup_path, json.dumps(startup))
    runtime = RuntimeManager()
    service.runtime = runtime
    bind(service, runtime)
    starts, controls, events = [], [], []
    sources = {'one': source}
    mounted = {'providers': [deepcopy(row)]}
    captured = {'home': service.data_dir, 'workspace': chat['workspace'], 'sources': sources,
                'connectionKeys': mounted_catalog_keys(service.data_dir, chat['workspace'], mounted['providers'], sources, binding=True)}
    coordinator = SimpleNamespace(config=mounted, get_capability=lambda _: captured)
    async def start(snapshot, emit, **kwargs):
        kwargs['guard']()
        starts.append(deepcopy(snapshot))
    async def control(sid, operation, args, **kwargs):
        kwargs['guard']()
        require_mounted_binding(coordinator, args['instance'], args['expectedConnectionKey'])
        controls.append(operation)
    async def emit(kind, payload):
        events.append(kind)
        if kind == 'runtime.nextMessageApplied':
            if 'bindingTransition' in payload:
                assert chat['modelConnectionKey'] == previous
            applied(chat, payload)
    monkeypatch.setattr(runtime, 'start', start)
    monkeypatch.setattr(runtime, 'control', control)
    await runtime._start_for_input(json.loads(startup_path.read_text()), emit)
    current = captured['connectionKeys']['one']
    assert current != previous and starts[0]['modelConnectionKey'] == current
    assert starts[0]['selection'] == chosen
    assert controls == ['provider.verifyConnection'] + (['provider.select'] if intent == 'pending' else [])
    assert 'runtime.nextMessageApplied' in events
    assert chat['modelConnectionKey'] == current and saved.read_bytes() == saved_bytes
    assert startup_path.read_text() == json.dumps(startup)
    assert chat.get('nextMessageRevision', 0) == (0 if intent == 'initial' else 1)
    # A live old-format capture is not relabelled by the host. It must retire
    # normally before fresh mounting; the attempted input remains unsent.
    captured['connectionKeys']['one'] = previous
    with pytest.raises(RuntimeStartupError, match='No input was sent'):
        await runtime._start_for_input(json.loads(startup_path.read_text()), emit)
    assert captured['connectionKeys']['one'] == previous
    assert saved.read_bytes() == saved_bytes
    # Once the verified receipt promotes the digest, a later visibility edit
    # works without reselecting the model or inventing historical configuration.
    captured['connectionKeys']['one'] = current
    row['config'][flag] = True
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': [row]}}))
    events.clear()
    await runtime._start_for_input(deepcopy(chat), emit)
    assert chat['modelConnectionKey'] == current and saved.read_bytes() == saved_bytes
    assert not events or events == ['runtime.nextMessageApplied']


@pytest.mark.parametrize('change', [
    {'config': {'api_key': 'different-account'}},
    {'config': {'base_url': 'https://different.invalid'}},
    {'config': {'organization': 'different-organization'}},
    {'source': 'git+https://fixture.invalid/openai@' + 'b' * 40},
    {'config': {'filtered': True}},  # No proof of prior scope: do not guess it.
    {'enabled': False},
])
async def test_pre_fix_binding_cannot_adopt_changed_account_or_unproven_scope(catalog_app, monkeypatch, change):
    from amplifier_web.next_message_configuration import prepare
    from amplifier_web.provider_catalog import fingerprint
    from amplifier_web.runtime import RuntimeStartupError
    from amplifier_web.setup import PROVIDER_ENV
    service, _, _, _ = catalog_app
    chat = service._session()
    for name in PROVIDER_ENV['provider-openai']:
        monkeypatch.delenv(name, raising=False)
    source = 'git+https://fixture.invalid/openai@' + 'a' * 40
    row = {'id': 'one', 'module': 'provider-openai', 'source': source,
           'config': {'api_key': 'synthetic-account', 'filtered': False}}
    previous = fingerprint(['provider-connection-v1', None, row['module'], source, row['config'],
                            {name: None for name in PROVIDER_ENV['provider-openai']}, {}])
    chosen = {'instance': 'one', 'model': 'hidden-exact'}
    chat.update(selection=chosen, pendingModelSelection=chosen, modelConnectionKey=previous, nextMessageRevision=1)
    changed = {**deepcopy(row), **{key: value for key, value in change.items() if key != 'config'}}
    changed['config'].update(change.get('config', {}))
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': [changed]}}))
    with pytest.raises(RuntimeStartupError, match='No input was sent'):
        await prepare(service, deepcopy(chat))
    assert chat['modelConnectionKey'] == previous
    assert chat['selection'] == chat['pendingModelSelection'] == chosen


@pytest.mark.parametrize('change', ['new-choice', 'new-revision'])
def test_verified_binding_transition_never_overwrites_newer_choice(change):
    from amplifier_web.next_message_configuration import applied
    session = {'nextMessageRevision': 2, 'modelConnectionKey': 'previous',
               'bundle': 'newer-bundle',
               'selection': {'instance': 'one', 'model': 'exact'}}
    payload = {'revision': 2, 'bindingOnly': True, 'bundle': 'older-bundle',
               'bindingTransition': {'previous': 'previous', 'current': 'current', 'instance': 'one'}}
    if change == 'new-choice':session['modelConnectionKey'] = 'newer-owner-key'
    else:session['nextMessageRevision'] = 3
    before = deepcopy(session)
    applied(session, payload)
    assert session == before


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('module', ['provider-openai', 'provider-fixture'])
async def test_established_conversation_override_keeps_actual_binding_not_shared_roster(
        catalog_app, monkeypatch, legacy, module):
    import json
    from types import SimpleNamespace
    from amplifier_web.next_message_configuration import bind, applied
    from amplifier_web.provider_catalog import connection_key, mounted_catalog_keys, require_mounted_binding
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    service, _, _, _ = catalog_app
    chat = service._session()
    source = 'git+https://fixture.invalid/provider@' + 'a' * 40
    shared = {'id': 'one', 'module': module, 'source': source,
              'config': {'api_key': 'shared-account-a', 'base_url': 'https://shared.invalid', 'filtered': False}}
    override = deepcopy(shared)
    override['config'].update(api_key='saved-account-b', base_url='https://saved.invalid')
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': [shared]}}))
    saved = service.data_dir / 'sessions' / chat['id'] / 'configuration.json'
    write_private(saved, json.dumps({'providers': [override]}))
    saved_bytes = saved.read_bytes()
    chosen = {'instance': 'one', 'model': 'hidden-exact'}
    expected = connection_key(chat['workspace'], module, override['config'], source, home=service.data_dir,
                              _legacy_visibility=legacy)
    chat.update(selection=chosen, modelConnectionKey=expected, nextMessageRevision=1, appliedConfigurationRevision=1)
    chat.pop('pendingModelSelection', None)
    runtime = RuntimeManager()
    service.runtime = runtime
    bind(service, runtime)
    sources = {'one': source}
    plan = {'providers': [deepcopy(override)]}
    captured = {'home': service.data_dir, 'workspace': chat['workspace'], 'sources': sources,
                'connectionKeys': mounted_catalog_keys(service.data_dir, chat['workspace'], plan['providers'], sources, binding=True)}
    coordinator = SimpleNamespace(config=plan, get_capability=lambda _: captured)
    operations = []
    async def start(snapshot, emit, **kwargs):
        kwargs['guard']()
    async def control(sid, operation, args, **kwargs):
        kwargs['guard']()
        require_mounted_binding(coordinator, 'one', args['expectedConnectionKey'])
        operations.append(operation)
    async def emit(kind, payload):
        if kind == 'runtime.nextMessageApplied':applied(chat, payload)
    async def no_discovery(*args, **kwargs):
        pytest.fail('An established override must not query the shared catalog')
    from amplifier_web import draft_defaults
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', no_discovery)
    monkeypatch.setattr(runtime, 'start', start)
    monkeypatch.setattr(runtime, 'control', control)
    await runtime._start_for_input(deepcopy(chat), emit)
    assert operations == ['provider.verifyConnection']
    assert chat['modelConnectionKey'] == captured['connectionKeys']['one']
    assert chat['selection'] == chosen and saved.read_bytes() == saved_bytes
    # Real mounted credential/source drift is still refused, including when
    # shared settings have never matched this deliberate conversation override.
    for field, value in [('api_key', 'changed-account'), ('base_url', 'https://changed.invalid')]:
        plan['providers'][0]['config'][field] = value
        with pytest.raises(RuntimeStartupError, match='No input was sent'):
            await runtime._start_for_input(deepcopy(chat), emit)
        plan['providers'][0]['config'] = deepcopy(override['config'])
    sources['one'] = 'git+https://fixture.invalid/changed@' + 'b' * 40
    with pytest.raises(RuntimeStartupError, match='No input was sent'):
        await runtime._start_for_input(deepcopy(chat), emit)
    assert saved.read_bytes() == saved_bytes


@pytest.mark.parametrize('intent', ['pending', 'established'])
@pytest.mark.parametrize('mutation', ['file', 'environment'])
async def test_legacy_adoption_never_joins_old_account_proof_to_new_credential_capture(
        catalog_app, monkeypatch, intent, mutation):
    import json
    from types import SimpleNamespace
    from amplifier_web import provider_catalog
    from amplifier_web.next_message_configuration import bind, applied
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    service, _, _, _ = catalog_app
    chat = service._session()
    credential = service.data_dir / 'synthetic-credential.json'
    credential.write_text('account-a')
    monkeypatch.setenv('SYNTHETIC_ADOPTION_KEY', 'account-a')
    source = 'git+https://fixture.invalid/openai@' + 'a' * 40
    row = {'id': 'one', 'module': 'provider-openai', 'source': source,
           'config': {'api_key': '${SYNTHETIC_ADOPTION_KEY}', 'credential_file': str(credential), 'filtered': False}}
    write_private(amplifier_home() / 'settings.yaml', yaml.safe_dump({'config': {'providers': [row]}}))
    saved = service.data_dir / 'sessions' / chat['id'] / 'configuration.json'
    write_private(saved, json.dumps({'providers': [row]}))
    previous = provider_catalog.connection_key(chat['workspace'], row['module'], row['config'], source,
                                               home=service.data_dir, _legacy_visibility=True)
    current_a = provider_catalog.connection_key(chat['workspace'], row['module'], row['config'], source, home=service.data_dir)
    chosen = {'instance': 'one', 'model': 'hidden-exact'}
    chat.update(selection=chosen, modelConnectionKey=previous, nextMessageRevision=1)
    chat.pop('pendingModelSelection', None)
    if intent == 'pending':chat['pendingModelSelection'] = chosen
    else:chat['appliedConfigurationRevision'] = 1
    original_fingerprint = provider_catalog.fingerprint
    changed = []
    def replace_after_proof(value):
        result = original_fingerprint(value)
        if (not changed and isinstance(value, list) and value[0] == 'provider-connection-v1'
                and value[4].get('filtered') is False):
            assert result == previous  # Exact account-A proof just captured.
            changed.append(True)
            if mutation == 'file':credential.write_text('account-b')
            else:monkeypatch.setenv('SYNTHETIC_ADOPTION_KEY', 'account-b')
        return result
    monkeypatch.setattr(provider_catalog, 'fingerprint', replace_after_proof)
    runtime = RuntimeManager()
    service.runtime = runtime
    bind(service, runtime)
    starts, verified, events = [], [], []
    async def start(snapshot, emit, **kwargs):
        kwargs['guard']()
        starts.append(deepcopy(snapshot))
    async def control(sid, operation, args, **kwargs):
        kwargs['guard']()
        mounted_b = provider_catalog.connection_key(chat['workspace'], row['module'], row['config'], source, home=service.data_dir)
        coordinator = SimpleNamespace(config={'providers': [row]}, get_capability=lambda _: {
            'home': service.data_dir, 'workspace': chat['workspace'], 'sources': {'one': source},
            'connectionKeys': {'one': mounted_b}})
        provider_catalog.require_mounted_binding(coordinator, 'one', args['expectedConnectionKey'])
        verified.append(operation)
    async def emit(kind, payload):
        events.append(kind)
        if kind == 'runtime.nextMessageApplied':applied(chat, payload)
    monkeypatch.setattr(runtime, 'start', start)
    monkeypatch.setattr(runtime, 'control', control)
    with pytest.raises(RuntimeStartupError, match='No input was sent'):
        await runtime._start_for_input(deepcopy(chat), emit)
    assert changed == [True]
    if intent == 'established':
        assert starts[0]['modelConnectionKey'] == current_a, 'Projection must use account A from the proof capture'
    else:
        assert not starts, 'Pending selection must fail its catalog binding recheck before mounting'
    assert not verified and not events
    assert chat['modelConnectionKey'] == previous and chat['selection'] == chosen
