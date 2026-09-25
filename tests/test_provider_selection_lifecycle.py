"""Root selections stay consistent across admission, controls, dispatch and resume."""
import copy
import json
from types import SimpleNamespace

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
