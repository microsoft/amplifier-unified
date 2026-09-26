"""Shared image capability preserves root choices and explicit authority."""
import copy

import pytest
from amplifier_foundation import Bundle

from amplifier_web.builtin_behaviors import IMAGEGEN_BEHAVIOR_URI, SHELL_BEHAVIOR_URI
from amplifier_web.bundles import BundleManager, SNAPSHOT_VERSION
from amplifier_web.host.components import compose_bundles, imagegen_defaults
from amplifier_web.host.config import HostConfig
from amplifier_web.host.session import compose_configured_bundle


def behavior():
    return Bundle(name='imagegen', tools=[
        {'module': 'tool-image', 'source': 'canonical-image-source',
         'config': {'backend': 'images', 'allow_paid': True}},
        {'module': 'tool-skills', 'source': 'canonical-skills-source',
         'config': {'skills': ['@imagegen:skills'], 'visibility': {'enabled': True}}},
    ])


@pytest.mark.parametrize('identity', [{}, {'id': 'custom-images'}, {'instance_id': 'custom-images'}])
def test_default_preserves_existing_image_and_skill_authority(identity):
    image = {'module': 'tool-image', **identity, 'source': 'owned-local-source',
             'config': {'backend': 'my-account', 'allow_paid': False,
                        'allowed_write_paths': [], 'denied_read_paths': ['private']}}
    root = Bundle(name='chosen', tools=[image,
        {'module': 'tool-skills', 'instance_id': 'owned-skills', 'source': 'owned-skills-source',
         'config': {'skills': ['project-skills'], 'visibility': {'enabled': False, 'visibility_token_budget': 100}}}])
    original = copy.deepcopy(root.tools)
    addition = behavior()
    base, overlay = imagegen_defaults(root, addition)
    result = compose_bundles(base, overlay)
    assert [row for row in result.tools if row['module'] == 'tool-image'] == [image]
    skill, = [row for row in result.tools if row['module'] == 'tool-skills']
    assert skill['instance_id'] == 'owned-skills' and skill['source'] == 'owned-skills-source'
    assert skill['config']['skills'] == ['project-skills', '@imagegen:skills']
    assert skill['config']['visibility'] == original[1]['config']['visibility']
    assert root.tools == original and len(addition.tools) == 2
    again = compose_bundles(*imagegen_defaults(result, addition))
    assert again.tools == result.tools


@pytest.mark.parametrize('name', ['work', 'anchors', 'custom'])
async def test_default_adds_capability_without_replacing_root(tmp_path, monkeypatch, name):
    from amplifier_web.host import session
    selected = behavior()
    async def load(registry, config, uri):
        assert uri == IMAGEGEN_BEHAVIOR_URI
        return selected, uri
    monkeypatch.setattr(session, 'load_configured_bundle', load)
    root = Bundle(name=name, instruction='Keep these instructions.',
                  session={'orchestrator': {'module': 'own-loop'}, 'context': {'module': 'own-context'}},
                  providers=[{'module': 'provider-owned', 'config': {'model': 'owned-model'}}])
    settings = {'web_bundles': {'excluded': [SHELL_BEHAVIOR_URI]}}
    config = HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry')
    result = await compose_configured_bundle(None, root, config)
    assert result.name == name and result.instruction == root.instruction
    assert result.session == root.session and result.providers == root.providers
    assert [row['module'] for row in result.tools].count('tool-image') == 1
    assert settings == {'web_bundles': {'excluded': [SHELL_BEHAVIOR_URI]}}


async def test_materialized_defaults_do_not_enable_a_root_paid_opt_out(tmp_path, monkeypatch):
    from amplifier_web.host import session
    async def load(registry, config, uri):
        assert uri == IMAGEGEN_BEHAVIOR_URI
        return behavior(), uri
    monkeypatch.setattr(session, 'load_configured_bundle', load)
    settings = {}
    entries = BundleManager.entries(settings)
    for row in entries:
        if row['uri'] == SHELL_BEHAVIOR_URI:
            row['enabled'] = False
    BundleManager.save_entries(settings, entries)
    root = Bundle(name='owned', tools=[{'module': 'tool-image', 'source': 'owned-source',
                                       'config': {'allow_paid': False}}])
    result = await compose_configured_bundle(None, root,
        HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry'))
    image, = [row for row in result.tools if row['module'] == 'tool-image']
    assert image['config']['allow_paid'] is False and image['source'] == 'owned-source'


async def test_module_settings_and_sources_remain_authoritative(tmp_path, monkeypatch):
    from amplifier_web.host import session
    async def load(registry, config, uri):
        return behavior(), uri
    monkeypatch.setattr(session, 'load_configured_bundle', load)
    settings = {'web_bundles': {'excluded': [SHELL_BEHAVIOR_URI]},
        'sources': {'modules': {'tool-image': 'local-image-source'}},
        'config': {'tools': [
            {'module': 'tool-skills', 'config': {'skills': []}},
            {'module': 'tool-image', 'config': {'allow_paid': False, 'allowed_write_paths': []}},
        ]}}
    root = Bundle(name='owned')
    result = await compose_configured_bundle(None, root,
        HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry'))
    image, = [row for row in result.tools if row['module'] == 'tool-image']
    assert image['source'] == 'local-image-source'
    assert image['config']['allow_paid'] is False and image['config']['allowed_write_paths'] == []
    skill, = [row for row in result.tools if row['module'] == 'tool-skills']
    assert skill['config']['skills'] == []


@pytest.mark.parametrize('snapshot', [False, True])
async def test_disabled_defaults_and_saved_snapshot_do_not_load_new_behavior(tmp_path, monkeypatch, snapshot):
    from amplifier_web.host import session
    async def unexpected_load(*args):
        pytest.fail('A complete snapshot or explicit empty app list must not load a new behavior.')
    monkeypatch.setattr(session, 'load_configured_bundle', unexpected_load)
    settings = {} if snapshot else {'bundle': {'app': []}}
    root = Bundle(name='saved', version=SNAPSHOT_VERSION if snapshot else '1.0.0',
                  tools=[{'module': 'tool-existing', 'source': 'saved-source'}])
    result = await compose_configured_bundle(None, root,
        HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'registry'))
    assert result.tools == root.tools
    assert not any(row['module'] in {'tool-image', 'tool-skills'} for row in result.tools)
