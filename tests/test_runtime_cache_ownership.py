"""Settings are shared; saved CLI aliases and default downloads are not."""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from amplifier_web.host.config import (
    HostConfig,
    configure_skill_cache,
    prepare_registry,
)
from amplifier_web.host.session import session_registry


def test_old_imported_registry_cannot_supply_session_aliases(tmp_path):
    owned = tmp_path / 'owned'
    shared = tmp_path / 'shared'
    owned.mkdir()
    old = {'version': 1, 'bundles': {'stale': {
        'uri': 'git+https://example.invalid/obsolete@main',
        'local_path': str(shared / 'cache/obsolete'),
    }}}
    saved = owned / 'registry.json'
    saved.write_text(json.dumps(old))
    before = saved.read_bytes()
    config = HostConfig(tmp_path, tmp_path, {'bundle': {'added': {
        'selected': 'git+https://example.invalid/current@main'}}}, owned, shared)
    prepare_registry(config)
    registry = session_registry(config)
    assert registry.list_registered() == []
    registry.register({'selected': config.registrations['selected']})
    registry.save()
    assert registry.list_registered() == ['selected']
    assert saved.read_bytes() == before


def test_old_foundation_constructor_cannot_read_or_save_aliases(tmp_path, monkeypatch):
    import amplifier_foundation

    class OlderRegistry:
        def __init__(self, home, strict, include_source_resolver):
            self.aliases = []
            self._load_persisted_state()
            self.save()

        def _load_persisted_state(self):
            pytest.fail('Older registry read shared aliases')

        def save(self):
            pytest.fail('Older registry wrote saved aliases')

    monkeypatch.setattr(amplifier_foundation, 'BundleRegistry', OlderRegistry)
    config = HostConfig(tmp_path, tmp_path, {}, tmp_path / 'owned')
    assert session_registry(config).aliases == []


def test_skills_defaults_are_owned_across_root_and_nested_agents(tmp_path, monkeypatch):
    shared = tmp_path / 'shared'
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    row = {'module': 'tool-skills', 'config': {'skills': [str(shared / 'skills')]}}
    explicit = {'module': 'tool-skills', 'config': {'cache_dir': str(tmp_path / 'explicit')}}
    bundle = SimpleNamespace(tools=[copy.deepcopy(row), {'module': 'tool-skills', 'config': {'cache_dir': None}}], agents={'worker': {
        'tools': [copy.deepcopy(row), explicit], 'agents': {'nested': {'tools': [copy.deepcopy(row)]}}}})
    configure_skill_cache(bundle, tmp_path / 'owned')
    expected = str(tmp_path / 'owned/cache/skills')
    assert bundle.tools[0]['config']['cache_dir'] == expected
    assert bundle.tools[1]['config']['cache_dir'] == expected
    assert bundle.agents['worker']['tools'][0]['config']['cache_dir'] == expected
    assert bundle.agents['worker']['agents']['nested']['tools'][0]['config']['cache_dir'] == expected
    assert explicit['config']['cache_dir'] == str(tmp_path / 'explicit')
    assert bundle.tools[0]['config']['skills'] == row['config']['skills']
    assert 'cache_dir' not in row['config']
    assert os.environ['AMPLIFIER_HOME'] == str(shared)
