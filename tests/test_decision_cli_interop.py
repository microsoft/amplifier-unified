"""Run with the actual CLI on sys.path; no CLI source change or live settings.

This optional cross-owner check uses its real loader and composition. Only
bundle fetching/module preparation/credential execution are inert transports.
"""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from amplifier_foundation import Bundle

from amplifier_web.decision_namespace import migrate_paths
from test_decision_namespace import DEFINITIONS, old_settings, provider


@pytest.mark.parametrize('bundle_providers', [False, True])
async def test_actual_cli_ignores_decision_namespace_through_runtime_composition(tmp_path, monkeypatch, bundle_providers):
    pytest.importorskip('amplifier_app_cli', reason='Requires explicitly selected CLI checkout/package')
    from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
    from amplifier_app_cli.lib.bundle_loader import prepare
    from amplifier_app_cli.provider_manager import resolve_provider_entry
    from amplifier_app_cli.runtime import config as runtime
    chat = provider('provider-chat', 'chat', priority=1, default_model='explicit-chat')
    specialist = provider(decisions={'enabled': True, 'id': 'existing', 'model': 'explicit-decision'})
    paths = [tmp_path / name for name in ('global.yaml', 'project.yaml', 'local.yaml', 'session.yaml')]
    for path, value in zip(paths, [old_settings(specialist, chat), {}, {}, {}]):
        path.write_text(yaml.safe_dump(value))
    settings = AppSettings(SettingsPaths(*paths))
    # Actual legacy conflict: the CLI has no decision-only classifier.
    assert settings.get_provider_overrides()[0]['module'] == 'provider-only'
    migrate_paths(paths, definitions=DEFINITIONS)
    assert settings.get_provider_overrides() == [chat]
    assert resolve_provider_entry(settings.get_provider_overrides(), 'one') is None
    assert resolve_provider_entry(settings.get_provider_overrides(), 'chat') == chat
    assert resolve_provider_entry(settings.get_provider_overrides(), 'provider-only') is None

    root = Bundle(name='synthetic', providers=([{'module': 'provider-chat', 'id': 'chat',
                  'config': {'default_model': 'bundle-default'}}] if bundle_providers else []),
                  session={'orchestrator': {'module': 'loop-fixture'},
                           'context': {'module': 'context-fixture'}}, base_path=tmp_path)
    async def load(uri, **kwargs):
        return copy.deepcopy(root) if uri == 'file:///synthetic-root' else Bundle(name='')
    prepared_sources = []
    async def inert_prepare(bundle, **kwargs):
        prepared_sources.append(kwargs)
        return SimpleNamespace(bundle=bundle, mount_plan=bundle.to_mount_plan(), resolver=MagicMock())
    monkeypatch.setattr('amplifier_app_cli.lib.bundle_loader.AppBundleDiscovery',
                        lambda **kwargs: SimpleNamespace(find=lambda name: 'file:///synthetic-root', registry=MagicMock()))
    monkeypatch.setattr('amplifier_app_cli.paths.get_bundle_search_paths', lambda: [])
    monkeypatch.setattr(prepare, 'load_bundle', load)
    monkeypatch.setattr(Bundle, 'prepare', inert_prepare)
    validation = AsyncMock()
    monkeypatch.setattr(runtime, '_validate_provider_credentials', validation)
    monkeypatch.setenv('SYNTHETIC_ONLY', 'synthetic-fixture-not-a-key')
    monkeypatch.chdir(tmp_path)
    plan, prepared = await runtime.resolve_bundle_config('synthetic', settings)
    for rows in [plan['providers'], prepared.bundle.providers]:
        assert len(rows) == 1
        assert rows[0]['module'] == 'provider-chat'
        assert rows[0].get('instance_id', rows[0].get('id')) == 'chat'
        assert rows[0]['config']['default_model'] == 'explicit-chat'
        assert 'decisions' not in rows[0]['config']
    assert 'provider-only' not in repr(prepared_sources)
    assert 'provider-only' not in repr(validation.call_args)