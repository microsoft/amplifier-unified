"""Root selections stay consistent across admission, controls, dispatch and resume."""
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_core.message_models import ChatRequest

from amplifier_web.host.session import SelectedProvider
from amplifier_web.runtime_controls import RuntimeControls


SELECTION = {'instance': 'terra', 'model': 'gpt-5.6-terra', 'effort': 'xhigh'}


class Info(SimpleNamespace):
    def model_copy(self, *, update):
        return Info(**{**vars(self), **update})


class Provider:
    def __init__(self, model, effort):
        self.info = Info(defaults={'model': model, 'reasoning_effort': effort})
        self.calls = []

    def get_info(self):
        return self.info

    async def complete(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return 'done'


def mounted_controls(selection):
    providers = {'default': Provider('bundle-model', 'medium'),
                 'terra': Provider('gpt-5.6-terra', 'high')}
    # prepare_manager applies the new-chat choice before constructing controls.
    root = SelectedProvider(providers[selection['instance']], copy.deepcopy(selection)) if selection else None
    loop = SimpleNamespace(root_provider=root, max_iterations=10)
    loop._select_provider = lambda mounted: loop.root_provider or next(iter(mounted.values()))
    config = {'session': {}, 'providers': [
        {'module': 'provider-openai', 'instance_id': name,
         'config': copy.deepcopy(provider.info.defaults)} for name, provider in providers.items()]}
    context = SimpleNamespace(max_tokens=None)
    coordinator = SimpleNamespace(config=config, session_state={},
        get=lambda name: {'providers': providers, 'orchestrator': loop, 'context': context}.get(name),
        get_capability=lambda name: None, register_capability=lambda *args: None)
    controls = RuntimeControls(SimpleNamespace(session_id='fresh-selection', coordinator=coordinator, config=config),
                               SimpleNamespace(generation=None, queued_inputs=0))
    return controls, loop, providers


async def test_fresh_selection_readback_first_request_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, providers = mounted_controls(SELECTION)
    assert not controls.state_path().exists()
    await controls.restore()
    current = await controls.perform('configuration.providers')
    assert current['selection'] == SELECTION and current['pinned']
    assert current['effective'] == SELECTION
    controls.persist()
    assert json.loads(controls.state_path().read_text())['selection'] == SELECTION
    await loop.root_provider.complete(ChatRequest(messages=[]))
    request, options = providers['terra'].calls[0]
    assert request.model == SELECTION['model'] and request.reasoning_effort == 'xhigh'
    assert options == {'model': SELECTION['model'], 'reasoning_effort': 'xhigh'}
    assert providers['terra'].get_info().defaults['reasoning_effort'] == 'high'
    await controls.close()

    # Saved controls win over a different initial mount, without replaying work.
    resumed, loop, providers = mounted_controls({'instance': 'default', 'model': 'stale', 'effort': 'low'})
    await resumed.restore()
    current = await resumed.perform('configuration.providers')
    assert current['selection'] == current['effective'] == SELECTION
    assert all(not provider.calls for provider in providers.values())
    await loop.root_provider.complete(ChatRequest(messages=[]))
    assert providers['terra'].calls[0][0].reasoning_effort == 'xhigh'
    await resumed.close()


async def test_saved_reset_overrides_stale_creation_selection_and_preserves_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, _, _ = mounted_controls(SELECTION)
    await controls.perform('budget.set', {'maxOutputTokens': 321})
    await controls.perform('provider.reset')
    assert json.loads(controls.state_path().read_text())['selection'] is None
    await controls.close()

    # The host can still supply the original new-chat choice on process restart.
    resumed, loop, providers = mounted_controls(SELECTION)
    await resumed.restore()
    current = await resumed.perform('configuration.providers')
    assert current['selection'] is None and not current['pinned']
    assert current['effective'] == {'instance': 'default', 'model': 'bundle-model', 'effort': 'medium'}
    await loop.root_provider.complete(ChatRequest(messages=[]))
    request, options = providers['default'].calls[0]
    assert request.model is None and request.reasoning_effort is None
    assert request.max_output_tokens == 321 and options == {}
    assert not providers['terra'].calls
    await resumed.close()


async def test_legacy_state_without_selection_keeps_explicit_creation_choice(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, _, _ = mounted_controls(SELECTION)
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'budget': {'maxIterations': 20}}))
    await controls.restore()
    assert (await controls.perform('configuration.providers'))['selection'] == SELECTION
    await controls.close()


async def test_model_selection_revision_survives_restore_and_distinguishes_a_b_a(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, providers = mounted_controls(SELECTION)
    await controls.perform('provider.select', SELECTION)
    first = controls.transition_contract()['selectionRevision']
    other = {**SELECTION, 'model': 'other-model'}
    await controls.perform('provider.select', other)
    second = controls.transition_contract()['selectionRevision']
    await controls.perform('provider.select', SELECTION)
    third = controls.transition_contract()['selectionRevision']
    assert len({first, second, third}) == 3
    assert all(not provider.calls for provider in providers.values())
    saved = json.loads(controls.state_path().read_text())
    assert saved['selectionRevision'] == third
    await controls.close()
    resumed, loop, providers = mounted_controls(other)
    await resumed.restore()
    assert resumed.transition_contract()['selectionRevision'] == third
    assert resumed.selection == SELECTION
    assert all(not provider.calls for provider in providers.values())
    await resumed.close()


async def test_transition_foreground_needs_new_host_send_and_native_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, providers = mounted_controls(SELECTION)
    caps = {}
    controls.coordinator.get_capability = caps.get
    await controls.perform('provider.select', SELECTION)
    runtime = controls.runtime
    runtime.accepted = {'new': SimpleNamespace(kind='user')}
    runtime.generation = {'id': 'generation', 'initial_input_id': 'new', 'input_ids': ['new']}
    # Even a real generation's user input is insufficient without host provenance.
    assert controls.transition_foreground() is None
    caps['web.context_transition.input'] = lambda: {
        'inputId': 'new', 'selectionRevision': controls.selection_revision}
    assert controls.transition_foreground()['inputId'] == 'new'
    runtime.generation['input_ids'] = []
    assert controls.transition_foreground() is None
    runtime.generation['input_ids'] = ['new']
    runtime.accepted['new'].kind = 'job_complete'
    assert controls.transition_foreground() is None
    runtime.accepted['new'].kind = 'user'
    caps['web.context_transition.input'] = lambda: {
        'inputId': 'new', 'selectionRevision': 'old-selection'}
    assert controls.transition_foreground() is None
    assert all(not provider.calls for provider in providers.values())
    await controls.close()


@pytest.mark.parametrize('changed', [None, 'credential', 'source'])
async def test_namespace_migration_cold_selection_retains_binding_without_minting_intent(
        tmp_path, monkeypatch, changed):
    import yaml
    from amplifier_foundation import Bundle
    from amplifier_web import decision_accounts, decision_namespace
    from amplifier_web.host.config import HostConfig
    from amplifier_web.host.session import _apply_settings
    from amplifier_web.provider_catalog import mounted_catalog_keys

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    definitions = {
        'provider-openai': ({'configuration_key': 'decisions'}, lambda value: value),
        'provider-only': ({'decision_only': True}, lambda value: value),
    }
    hooks = {'provider-only': 'hooks-only-decisions'}
    monkeypatch.setattr(decision_accounts, 'catalogs', lambda: hooks)
    settings = {'config': {'providers': [
        {'id': 'decision', 'module': 'provider-only', 'source': '/synthetic/decision',
         'config': {'api_key': 'synthetic-decision', 'decisions': {'enabled': True}}},
        {'id': 'terra', 'module': 'provider-openai', 'source': '/synthetic/chat',
         'config': {'api_key': 'synthetic-chat', 'default_model': SELECTION['model'],
                    'decisions': {'enabled': False}}},
    ]}}
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(settings))

    def mount():
        current = decision_namespace.read_scopes([path])
        config = HostConfig(tmp_path, tmp_path, current, tmp_path / 'cache', tmp_path / 'shared')
        logical = _apply_settings(Bundle(name='fixture'), config).to_mount_plan()
        projected = decision_accounts.DecisionAccounts(
            logical, None, lambda module, source: source, definitions=hooks).plan
        assert [row['id'] for row in projected['providers']] == ['terra']
        sources = {row['id']: row['source'] for row in projected['providers']}
        controls, _, providers = mounted_controls(SELECTION)
        controls.coordinator.config = controls.session.config = projected
        caps = {'web.provider_catalog': {
            'home': str(tmp_path), 'workspace': str(tmp_path), 'sources': sources,
            'connectionKeys': mounted_catalog_keys(
                tmp_path, tmp_path, projected['providers'], sources, binding=True)}}
        controls.coordinator.get_capability = caps.get
        return controls, providers, caps

    first, providers, caps = mount()
    binding = caps['web.provider_catalog']['connectionKeys']['terra']
    await first.perform('provider.select', {**SELECTION, 'expectedConnectionKey': binding})
    contract = first.transition_contract()
    saved = first.state_path().read_bytes()
    assert first.transition_foreground() is None
    await first.close()

    decision_namespace.migrate_paths([path], definitions=definitions)
    assert 'decision' in decision_namespace.connections(decision_namespace.read_scopes([path]))
    assert first.state_path().read_bytes() == saved
    if changed:
        value = yaml.safe_load(path.read_text())
        row = value['config']['providers'][0]
        if changed == 'credential':
            row['config']['api_key'] = 'different-synthetic-chat'
        else:
            row['source'] = '/synthetic/different-chat'
        path.write_text(yaml.safe_dump(value))
    resumed, providers, caps = mount()
    await resumed.restore()
    assert resumed.selection_revision == contract['selectionRevision']
    assert resumed.selection_binding == binding
    assert resumed.transition_foreground() is None  # A cold read is never Send.
    assert all(not provider.calls for provider in providers.values())
    if changed:
        with pytest.raises(ValueError, match='mounted connection differs'):
            await resumed.perform('provider.verifyConnection', {
                'instance': 'terra', 'expectedConnectionKey': binding})
        assert resumed.state_path().read_bytes() == saved
    else:
        assert resumed.transition_contract() == contract  # Ignores new projection UUID.
        await resumed.perform('provider.verifyConnection', {
            'instance': 'terra', 'expectedConnectionKey': binding})
        runtime = resumed.runtime
        runtime.accepted = {'new': SimpleNamespace(kind='user')}
        runtime.generation = {'id': 'generation', 'initial_input_id': 'new', 'input_ids': ['new']}
        caps['web.context_transition.input'] = lambda: {
            'inputId': 'new', 'selectionRevision': resumed.selection_revision}
        assert resumed.transition_foreground()['connection'] == binding
        runtime.stop_requested = True
        assert resumed.transition_foreground() is None
    await resumed.close()


async def test_cold_selection_override_revisions_once_then_identical_mounted_ack(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first, _, _ = mounted_controls(SELECTION)
    await first.perform('provider.select', SELECTION)
    old = first.selection_revision
    await first.close()
    other = {**SELECTION, 'model': 'new-target'}
    resumed, loop, providers = mounted_controls(other)
    await resumed.restore(selection_override=other)
    target = resumed.selection_revision
    assert target and target != old
    await resumed.perform('provider.select', other)
    assert resumed.selection_revision == target
    await resumed.perform('provider.select', SELECTION)
    assert resumed.selection_revision not in {old, target}
    assert all(not provider.calls for provider in providers.values())
    await resumed.close()


async def test_cold_choice_only_override_then_bound_ack_keeps_one_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    def bind(controls, key):
        original = controls.coordinator.get_capability
        catalog = {'connectionKeys': {'terra': key}}
        controls.coordinator.get_capability = lambda name: catalog if name == 'web.provider_catalog' else original(name)
    first, _, _ = mounted_controls(SELECTION)
    bind(first, 'account-A')
    await first.perform('provider.select', {**SELECTION, 'expectedConnectionKey': 'account-A'})
    old = first.selection_revision
    await first.close()
    other = {**SELECTION, 'model': 'new-target'}
    resumed, _, _ = mounted_controls(other)
    bind(resumed, 'account-B')
    await resumed.restore(selection_override=other)
    mounted = resumed.selection_revision
    assert mounted != old
    assert json.loads(resumed.state_path().read_text())['selectionBindingPending'] is True
    await resumed.close()
    resumed, _, _ = mounted_controls(other)
    bind(resumed, 'account-B')
    await resumed.restore(selection_override=other)
    assert resumed.selection_revision == mounted
    before = json.loads(resumed.state_path().read_text())
    import pytest
    with pytest.raises(ValueError, match='mounted connection differs'):
        await resumed.perform('provider.select', {**other, 'expectedConnectionKey': 'wrong-account'})
    assert json.loads(resumed.state_path().read_text()) == before
    await resumed.perform('provider.select', {**other, 'expectedConnectionKey': 'account-B'})
    assert resumed.selection_revision == mounted
    assert resumed.selection_binding == 'account-B'
    await resumed.close()
    again, _, _ = mounted_controls(other)
    bind(again, 'account-B')
    await again.restore()
    assert again.selection_revision == mounted and again.selection_binding == 'account-B'
    await again.restore(selection_override=other)
    assert again.selection_revision == mounted and again.selection_binding == 'account-B'
    await again.close()


async def test_h4_legacy_explicit_selection_migrates_once_only_with_verified_binding(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, _, providers = mounted_controls(SELECTION)
    controls.state_path().parent.mkdir(parents=True, exist_ok=True)
    legacy = {'selection': SELECTION, 'budget': {}}
    controls.state_path().write_text(json.dumps(legacy))
    controls.coordinator.get_capability = lambda name: (
        {'connectionKeys': {'terra': 'verified-current'}} if name == 'web.provider_catalog' else None)
    await controls.restore()
    revision = controls.selection_revision
    assert revision and controls.selection_binding == 'verified-current'
    assert all(not p.calls for p in providers.values())
    await controls.close()
    restored, _, _ = mounted_controls(SELECTION)
    restored.coordinator.get_capability = controls.coordinator.get_capability
    await restored.restore()
    assert restored.selection_revision == revision
    await restored.close()
    # An explicit null marks independent fork/non-admission, not old schema.
    controls.state_path().write_text(json.dumps({**legacy, 'selectionRevision': None}))
    excluded, _, _ = mounted_controls(SELECTION)
    excluded.coordinator.get_capability = controls.coordinator.get_capability
    await excluded.restore()
    assert excluded.selection_revision is None
    await excluded.close()
    # Missing or mismatched connection evidence cannot mint model work authority.
    controls.state_path().write_text(json.dumps({**legacy, 'selectionBinding': 'stale'}))
    invalid, _, _ = mounted_controls(SELECTION)
    invalid.coordinator.get_capability = controls.coordinator.get_capability
    with pytest.raises(ValueError, match='mounted connection differs'):
        await invalid.restore()
    assert invalid.selection_revision is None
    await invalid.close()
async def test_transferred_fork_absent_revision_is_not_legacy_explicit_choice(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, _, providers = mounted_controls(SELECTION)
    controls.state_path().parent.mkdir(parents=True, exist_ok=True)
    # Old portable control schemas omit transition fields; canonical fork
    # metadata still supplies the ordinary host capability on this mount.
    controls.state_path().write_text(json.dumps({'selection': SELECTION}))
    caps = {'web.provider_catalog': {'connectionKeys': {'terra': 'verified-current'}},
            'web.context_transition.originals': True,
            'web.context_transition.input': lambda: {
                'inputId': 'fresh', 'selectionRevision': controls.selection_revision}}
    controls.coordinator.get_capability = caps.get
    try:
        await controls.restore()
        assert controls.selection_revision is None and controls.selection_binding is None
        controls.runtime.accepted = {'fresh': SimpleNamespace(kind='user')}
        controls.runtime.generation = {
            'id': 'generation', 'initial_input_id': 'fresh', 'input_ids': ['fresh']}
        assert controls.transition_foreground() is None
        assert all(not provider.calls for provider in providers.values())
        controls.runtime.generation = None
        await controls.perform('provider.select', {
            **SELECTION, 'expectedConnectionKey': 'verified-current'})
        assert controls.selection_revision and controls.selection_binding == 'verified-current'
    finally:
        await controls.close()


@pytest.mark.parametrize('case', [
    'confirmed', 'queued-selection', 'direct-control', 'inherited', 'wrong-parent', 'transferred', 'different-effort',
    'changed-mount', 'agent-send', 'retry', 'active', 'pending-input', 'duplicate',
])
async def test_original_send_recovers_only_an_applied_local_fork_choice(tmp_path, monkeypatch, case):
    from unittest.mock import AsyncMock
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.service import AppError, AppService
    from amplifier_web.management import Management
    from amplifier_web.host.storage import SessionStore
    from amplifier_web import provider_catalog

    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.management = Management(app)
    await app.dispatch('session.create', {'bundle': 'anchors'})
    parent = app._session()
    parent.update(selection=SELECTION, modelConnectionKey='parent-account')
    parent_id = parent['id']
    store = SessionStore.for_app(app.data_dir, parent['workspace'])
    store.save(parent_id, [{'role': 'user', 'content': 'Original'}], {}, preserve_system=True)
    app._message(parent, 'user', 'Original')
    await app.dispatch('session.fork', {'id': parent_id})
    session = app._session()
    assert session.get('modelConnectionKey') is None
    assert session['selection'] == SELECTION
    sid = session['id']
    controls, _, providers = mounted_controls(SELECTION)
    controls.session.session_id = sid
    runtime = controls.runtime
    runtime.accepted = {'new': SimpleNamespace(kind='user')} if case == 'duplicate' else {}
    worker = SimpleNamespace(runtime=runtime, controls=controls, context_bindings={}, stop_revision=0)
    mounted_key = 'other-account' if case == 'changed-mount' else 'current-account'
    fork = store.load(sid)[1]['fork']
    caps = {'web.context_transition.originals': True,
            'web.context_transition.fork_parent': 'other' if case == 'wrong-parent' else fork['source_session_id'],
            'web.provider_catalog': {'connectionKeys': {'terra': mounted_key}},
            'web.context_transition.input': lambda: Worker.transition_input(worker)}
    controls.coordinator.get_capability = caps.get
    controls.state_path().parent.mkdir(parents=True)
    controls.state_path().write_text(json.dumps({'selection': SELECTION, 'selectionRevision': None}))
    await controls.restore()
    runtime.generation = {'id': 'running'} if case == 'active' else None
    runtime.queued_inputs = int(case == 'pending-input')
    if case not in {'inherited', 'direct-control'}:
        selected = {**SELECTION, 'effort': 'low'} if case == 'different-effort' else SELECTION
        monkeypatch.setattr(provider_catalog, 'configured_binding', lambda *a, **kw: 'old-account-digest')
        if case == 'queued-selection':
            await app.management.perform('runtime.control', {'sessionId': sid,
                'operation': 'provider.queueSelection', 'args': selected})
        else:
            await app.dispatch('conversation.configure', {'sessionId': sid, 'selection': selected,
                'expectedRevision': 0}, command_id='save-on-fork')
        await app.on_runtime_event('runtime.nextMessageApplied', {'sessionId': sid,
            'revision': 1, 'selection': selected, 'bundle': 'anchors'})
        assert not session.get('pendingModelSelection')
    elif case == 'direct-control':
        # Use the legacy worker's normal host select and catalog readback.
        async def original_worker(sid, operation, arguments):
            if operation == 'provider.select':
                return {'selection': arguments}
            assert operation == 'configuration.providers'
            return {'providers': [{'id': 'terra', 'connectionKey': 'old-account-digest'}]}
        monkeypatch.setattr(app.management, 'ensure_runtime', AsyncMock())
        app.runtime = SimpleNamespace(control=original_worker)
        await app.management.perform('runtime.control', {'sessionId': sid,
            'operation': 'provider.select', 'args': SELECTION})
        assert session['modelConnectionKey'] == 'old-account-digest'
        assert not session.get('nextMessageRevision')
    if case == 'transferred':
        session['portabilityOrigin'] = {'host': 'other-host'}
    app._message(session, 'user', 'fresh', inputId='new', inputOrigin='agent' if case == 'agent-send' else 'ui',
                 delivery={'status': 'sending'})
    manager = RuntimeManager()
    async def prepare(captured):
        return {**captured, 'modelConnectionKey': 'current-account'}, lambda: None
    manager.bind_input_configuration(prepare)
    manager.start = AsyncMock()
    async def control(identity, operation, args, **kwargs):
        kwargs['guard']()
        assert operation == 'provider.verifyConnection'
        return await controls.perform(operation, args)
    manager.control = control
    async def request(identity, operation, **args):
        args['guard']()
        Worker.bind_input_context(worker, args['input_id'], {'op': operation, **args})
        return {'accepted': True}
    manager._request = request
    app.runtime = manager
    try:
        if case == 'changed-mount':
            with pytest.raises(AppError, match='verify the saved connection'):
                await app._send(copy.deepcopy(session), 'fresh', 'new')
            assert not worker.context_bindings
        else:
            assert (await app._send(copy.deepcopy(session), 'fresh', 'new', retry=case == 'retry'))['accepted']
        confirmed = case in {'confirmed', 'direct-control', 'queued-selection'}
        assert bool(controls.selection_revision) == confirmed
        runtime.accepted['new'] = SimpleNamespace(kind='user')
        runtime.generation = {'id': 'gen', 'initial_input_id': 'new', 'input_ids': ['new']}
        if confirmed:
            assert controls.transition_foreground()['connection'] == 'current-account'
            saved = json.loads(controls.state_path().read_text())
            assert saved['selectionRevision'] == controls.selection_revision
            assert saved['selection'] == SELECTION
        else:
            assert controls.transition_foreground() is None
        assert all(not provider.calls for provider in providers.values())
    finally:
        await app.close()
        await controls.close()


def test_selected_provider_info_reports_effort_without_mutating_bundle_defaults():
    provider = Provider('bundle-model', 'high')
    selected = SelectedProvider(provider, SELECTION)
    assert selected.get_info().defaults == {'model': 'gpt-5.6-terra', 'reasoning_effort': 'xhigh'}
    assert provider.get_info().defaults == {'model': 'bundle-model', 'reasoning_effort': 'high'}
    assert SelectedProvider(provider, {'max_output_tokens': 321}).get_info().defaults == {
        'model': 'bundle-model', 'reasoning_effort': 'high', 'max_output_tokens': 321}


async def test_shared_provider_catalog_reports_routing_without_changing_parent_choice(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    controls, loop, providers = mounted_controls(SELECTION)
    loop.config = {'inherit_effective_model':True}
    resolver = SimpleNamespace(name='personal-routing', matrix_source='user', matrix_path='/private/routing.yaml')
    controls.coordinator.get_capability = lambda name: resolver if name=='model_role_resolver' else None
    catalog = await controls.perform('configuration.providers')
    assert catalog['delegationRouting'] == {'modelInheritance':'conversation_when_unspecified',
        'crossProviderRestriction':'not_enforced','resolverActive':True,'resolverName':'personal-routing','matrixSource':'user'}
    assert catalog['effective'] == SELECTION
    assert loop.root_provider.selection == SELECTION
    assert 'matrix_path' not in catalog['delegationRouting']
