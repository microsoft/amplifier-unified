"""Use real Foundation include composition, without installing or mounting modules."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_foundation import Bundle, BundleRegistry
from amplifier_web.profile_catalog import characteristics, save_catalog, read_catalog
from amplifier_web.bundles import offered_profiles, profile_candidates, document_metadata


def complete(name='complete', loop='loop-streaming'):
    return Bundle(name=name, session={'orchestrator': {'module': loop}, 'context': {'module': 'context-simple'}},
                  providers=[{'module': 'provider-test'}])


async def test_real_foundation_includes_determine_completeness_independent_of_paths(tmp_path):
    sources = tmp_path / 'sources'
    (sources / 'behaviors').mkdir(parents=True)
    (sources / 'behaviors' / 'pieces.yaml').write_text('bundle: {name: pieces}\nsession:\n  orchestrator: {module: loop-streaming}\n  context: {module: context-simple}\nproviders: [{module: provider-test}]\n')
    (sources / 'custom.yaml').write_text('bundle: {name: inherited}\nincludes:\n  - bundle: test-base\n')
    (sources / 'bundle.md').write_text('---\nbundle: {name: partial-root}\ntools: [{module: tool-example}]\n---\nSome instructions.\n')
    registry = BundleRegistry(home=tmp_path / 'registry', persist=False, read_persisted=False, strict=True)
    registry.register({'test-base': str(sources / 'behaviors' / 'pieces.yaml')})
    inherited = await registry.load(str(sources / 'custom.yaml'))
    nested = await registry.load(str(sources / 'behaviors' / 'pieces.yaml'))
    partial = await registry.load(str(sources / 'bundle.md'))
    assert characteristics(inherited)['complete']
    assert characteristics(nested)['complete']
    assert characteristics(partial)['missing'] == ['orchestrator', 'context', 'providers']
    assert not characteristics(partial)['complete']
    # The host's providers/behavior composition can complete an otherwise partial bundle.
    assert characteristics(partial.compose(complete()))['complete']
    assert not characteristics(complete(loop='custom-loop'))['supportedLoop']
    assert characteristics(complete(loop='custom-loop'))['complete']


def test_discovery_does_not_infer_kind_from_file_path():
    root = 'bundle: {name: example}\ntools: [{module: tool-example}]'
    assert document_metadata('---\n'+root+'\n---\n', 'bundle.md')['kind'] == 'behavior'
    session = 'bundle: {name: example}\nsession: {orchestrator: loop-live, context: context-simple}'
    assert document_metadata(session, 'behaviors/anything.yaml')['kind'] == 'standalone'
    assert document_metadata('bundle: {name: inherited}\nincludes: [{bundle: ./pieces.yaml}]', 'misc.yaml')['kind'] == 'bundle'


def test_catalog_is_generation_and_configuration_scoped_without_changing_registrations(tmp_path):
    settings = {'bundle': {'added': {'root-library': 'any/root', 'nested-profile': 'any/behaviors/file'}, 'app': []}}
    config = SimpleNamespace(home=tmp_path, registry_home=tmp_path/'foundation', settings=settings,
                             registrations={'work': 'work-source', **settings['bundle']['added']})
    before = copy.deepcopy(settings)
    candidates = profile_candidates(config)
    facts = {'root-library': characteristics(Bundle(name='partial')), 'nested-profile': characteristics(complete()),
             'work': characteristics(complete())}
    save_catalog(config, candidates, facts)
    assert offered_profiles(config) == ['nested-profile', 'work']
    assert settings == before
    facts['nested-profile'] = characteristics(complete(loop='custom-loop'))
    save_catalog(config, candidates, facts)
    assert offered_profiles(config) == ['work']
    assert settings == before
    config.settings['config'] = {'session': {'context': {'module': 'different'}}}
    assert offered_profiles(config) == candidates  # Changed configuration needs classification again.
    assert not read_catalog(config, candidates)


@pytest.mark.parametrize('kind', ['partial', 'unsupported', 'selected-unsupported', 'selected-partial', 'complete'])
async def test_preparation_skips_only_unselected_partial_bundles(tmp_path, monkeypatch, kind):
    from amplifier_web.host import session
    monkeypatch.chdir(tmp_path)
    loaded = complete(loop='custom-loop' if 'unsupported' in kind else 'loop-live') if kind in {'complete','unsupported','selected-unsupported'} else Bundle(name='partial')
    loaded.prepare = AsyncMock()
    config = SimpleNamespace(workspace=tmp_path, active_bundle='test' if kind.startswith('selected-') else 'work', registry_home=tmp_path/'foundation', module_sources={})
    monkeypatch.setattr(session, 'load_config', lambda *args, **kw: config)
    monkeypatch.setattr('amplifier_web.host.config.prepare_registry', lambda c: None)
    monkeypatch.setattr(session, 'load_root_bundle', AsyncMock(return_value=(None, loaded, 'test')))
    components = SimpleNamespace(apply=lambda bundle: bundle)
    monkeypatch.setattr(session, 'required_components', lambda: components)
    catalog = {}
    if kind in {'selected-unsupported','selected-partial'}:
        with pytest.raises(ValueError):
            await session.prepare_dependencies(tmp_path, bundle='test', profile_catalog=catalog)
    else:
        await session.prepare_dependencies(tmp_path, bundle='test', profile_catalog=catalog)
    assert loaded.prepare.await_count == (1 if kind=='complete' else 0)
    assert catalog['test']['complete'] == (kind in {'complete','unsupported','selected-unsupported'})


def test_foundation_string_module_declarations_remain_supported():
    from amplifier_web.host.session import live_plan
    original = {'session': {'orchestrator': 'loop-streaming', 'context': 'context-simple'}}
    plan, _ = live_plan(original)
    assert plan['session']['orchestrator']['module'] == 'loop-live'
    assert original['session']['orchestrator'] == 'loop-streaming'
