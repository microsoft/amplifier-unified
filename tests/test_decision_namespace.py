"""Synthetic settings boundaries: no credentials, inference, or installed hosts."""
import copy
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from amplifier_web import decision_namespace as ns
from amplifier_web import decision_settings
from amplifier_web.host.config import HostConfig
from amplifier_web.provider_catalog import connection_key
from amplifier_web.shared_settings import read_yaml


DEFINITIONS = {
    'provider-chat': ({'label': 'Chat', 'configuration_key': 'decisions'}, lambda value: value),
    'provider-only': ({'label': 'Only', 'configuration_key': 'decisions',
                       'decision_only': True}, lambda value: value),
    'provider-conditional': ({'label': 'Conditional', 'configuration_key': 'decisions',
                             'decision_only_when': {'chat_enabled': False}}, lambda value: value),
}


def provider(module='provider-only', identity='one', **config):
    return {'module': module, 'id': identity,
            'source': 'git+https://example.invalid/adapter@123',
            'config': {'api_key': '${SYNTHETIC_ONLY}', **config}}


def old_settings(*rows):
    return {'config': {'providers': list(rows)}, 'decision_models': {
        'selection': {'browser': {'mode': 'decision', 'backend': 'existing',
                                  'allow_external_state': True},
                      'computer': {'mode': 'standard', 'allow_external_state': False}}},
            'unrelated': {'preserve': ['exactly']}}


def config(settings, tmp_path):
    return HostConfig(tmp_path / 'app', tmp_path, settings, tmp_path / 'cache', tmp_path / 'shared')


@pytest.mark.parametrize('decisions', [
    {'enabled': False, 'id': 'existing', 'model': 'old'},
    {'enabled': True, 'backends': {'existing': {'model': 'old', 'enabled': False}}},
    {},
])
@pytest.mark.parametrize('module', ['provider-only', 'provider-chat'])
def test_translate_preserves_exact_logical_accounts_and_choices(tmp_path, module, decisions):
    old = old_settings(provider(module, decisions=decisions))
    before = copy.deepcopy(old)
    new = ns.translate(old, DEFINITIONS)
    assert old == before
    assert ns.translate(new, DEFINITIONS) == new
    assert ns.logical_rows(new) == old['config']['providers']
    assert new['decision_models']['selection'] == old['decision_models']['selection']
    assert new['unrelated'] == old['unrelated']
    assert all('decisions' not in row['config'] for row in new['config']['providers'])
    row = ns.logical_rows(new)[0]
    # Same implementation, account, endpoint, and decision options. Do not
    # weaken the binding recipe to call a migrated account equivalent.
    assert connection_key(tmp_path, row['module'], row['config'], row['source']) == connection_key(
        tmp_path, module, before['config']['providers'][0]['config'], row['source'])
    if module == 'provider-chat':
        assert config(new, tmp_path).providers == old['config']['providers']
        assert new['decision_models']['connections']['one']['provider'] == 'one'
    else:
        assert config(new, tmp_path).providers == []


def test_metadata_not_module_name_establishes_decision_ownership():
    ordinary = provider('provider-conditional', 'chat', chat_enabled=True, default_model='unchanged')
    only = provider('provider-conditional', 'decide', chat_enabled=False)
    unknown = provider('provider-third-party', 'third', decisions={'enabled': True})
    old = old_settings(ordinary, only, unknown)
    new = ns.translate(old, DEFINITIONS)
    assert new['config']['providers'] == [ordinary, unknown]
    assert new['decision_models']['connections'] == {'decide': only}
    assert ns.translate(old, {}) == old


def test_scope_fragments_merge_like_original_provider_rows(tmp_path):
    global_old = old_settings(provider(decisions={'enabled': True, 'backends': {'existing': {
        'model': 'global', 'enabled': True}}}))
    local_old = {'config': {'providers': [{'id': 'one', 'module': 'provider-only', 'config': {
        'base_url': 'https://local.invalid', 'decisions': {'backends': {'existing': {'model': 'local'}}}}}]},
        'configurator': {'disabled': {'providers': ['one']}}}
    old = ns.merge_settings(global_old, local_old)
    global_new = ns.translate(global_old, DEFINITIONS)
    local_new = ns.translate(local_old, DEFINITIONS, inherited=global_new)
    new = ns.merge_settings(global_new, local_new)
    assert ns.logical_rows(new) == old['config']['providers']
    assert new['configurator'] == old['configurator']
    assert config(new, tmp_path).saved_connections[0]['enabled'] is False
    assert config(new, tmp_path).configured_connections == []
    assert local_new['decision_models']['connections']['one']['config'] == local_old['config']['providers'][0]['config']
    assert 'api_key' not in repr(local_new)
    paths = [tmp_path / name for name in ('global.yaml', 'project.yaml', 'local.yaml', 'session.yaml')]
    for path, value in zip(paths, [global_old, {}, local_old, {}]):
        path.write_text(yaml.safe_dump(value))
    # Ordinary reads do not import optional metadata or fix the CLI by stealth.
    assert ns.read_scopes(paths) == old
    assert ns.migrate_paths(paths, definitions=DEFINITIONS) == new
    assert ns.read_scopes(paths) == new
    snapshot = [path.read_bytes() for path in paths]
    assert ns.migrate_paths(paths, definitions=DEFINITIONS) == new
    assert [path.read_bytes() for path in paths] == snapshot


def test_shared_scope_override_preserves_chat_and_reconstructs_original_decisions():
    base = old_settings(provider('provider-chat', decisions={'enabled': True, 'id': 'existing', 'model': 'old'}))
    local = {'config': {'providers': [{'id': 'one', 'module': 'provider-chat', 'config': {
        'default_model': 'local-chat', 'decisions': {'model': 'local-decision'}}}]}}
    first = ns.translate(base, DEFINITIONS)
    second = ns.translate(local, DEFINITIONS, inherited=first)
    combined = ns.merge_settings(first, second)
    assert ns.logical_rows(combined) == ns.merge_settings(base, local)['config']['providers']
    assert second['config']['providers'][0]['config'] == {'default_model': 'local-chat'}
    assert 'api_key' not in repr(second)


@pytest.mark.parametrize('patcher', [
    lambda old: old['config']['providers'].append(copy.deepcopy(old['config']['providers'][0])),
    lambda old: old['config']['providers'][0]['config'].update(decisions=['malformed']),
    lambda old: old.update(decision_models={'connections': {'one': provider(api_key='different')}}),
])
def test_ambiguous_or_malformed_migration_refuses_without_changing_file(tmp_path, patcher):
    value = old_settings(provider(decisions={'enabled': True}))
    patcher(value)
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(value))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        ns.migrate_paths([path], definitions=DEFINITIONS)
    assert path.read_bytes() == before


def test_per_file_atomic_failure_and_interrupted_retry(tmp_path):
    from amplifier_foundation import settings as storage
    paths = [tmp_path / name for name in ('global.yaml', 'local.yaml')]
    for path in paths:
        path.write_text(yaml.safe_dump(old_settings(provider())))
    initial = [path.read_bytes() for path in paths]
    replace = storage.os.replace
    def fail_second(source, target):
        if Path(target) == paths[1]:
            raise OSError('synthetic publication failure')
        return replace(source, target)
    with patch.object(storage.os, 'replace', fail_second), pytest.raises(OSError):
        ns.migrate_paths(paths, definitions=DEFINITIONS)
    assert paths[0].read_bytes() != initial[0]
    assert paths[1].read_bytes() == initial[1]
    assert not list(tmp_path.glob('.settings.yaml-*'))
    ns.migrate_paths(paths, definitions=DEFINITIONS)
    assert not read_yaml(paths[1])['config']['providers']


def test_runtime_uses_existing_hook_projection_not_a_new_credential_boundary(tmp_path):
    from amplifier_foundation import Bundle
    from amplifier_web.decision_accounts import DecisionAccounts, logical_plan
    old = old_settings(provider(decisions={'enabled': True, 'id': 'existing', 'model': 'old'}))
    new = ns.translate(old, DEFINITIONS)
    bundle = Bundle(name='fixture', providers=[], tools=[{'module': 'tool-ui-use'}])
    from amplifier_web.host.session import _apply_settings
    with patch('amplifier_web.decision_accounts.bind_sources'):
        _apply_settings(bundle, config(new, tmp_path))
    plan = bundle.to_mount_plan()
    accounts = DecisionAccounts(plan, None, lambda module, source: source,
                                definitions={'provider-only': 'hooks-only-decisions'})
    assert accounts.plan['providers'] == []
    assert accounts.plan['hooks'][0]['config']['api_key'] == '${SYNTHETIC_ONLY}'
    restored = logical_plan(accounts.plan, definitions={'provider-only': 'hooks-only-decisions'})
    assert restored == {**plan, 'session': {}, 'hooks': []}
    assert plan['tools'][0]['config']['selection'] == old['decision_models']['selection']


def test_noop_and_missing_scopes_do_not_create_files(tmp_path):
    path = tmp_path / 'missing.yaml'
    assert ns.migrate_paths([path], definitions=DEFINITIONS) == {}
    assert not list(tmp_path.iterdir())


def test_ordinary_read_never_discovers_adapters_or_writes(tmp_path, monkeypatch):
    path = tmp_path / 'settings.yaml'
    old = old_settings(provider())
    path.write_text(yaml.safe_dump(old))
    def forbidden(*args, **kwargs):
        raise AssertionError('ordinary settings reads must remain passive')
    monkeypatch.setattr(decision_settings, 'catalogs', forbidden)
    monkeypatch.setattr('amplifier_web.shared_settings.update_settings', forbidden)
    assert ns.read_scopes([path]) == old

def test_migration_preserves_interleaved_equal_priority_source_precedence(tmp_path):
    decision = provider('provider-conditional', 'decision', chat_enabled=False)
    decision['source'] = '/synthetic/decision'
    chat = provider('provider-conditional', 'chat', chat_enabled=True)
    chat['source'] = '/synthetic/chat'
    old = old_settings(decision, chat)
    new = ns.translate(old, DEFINITIONS)
    assert ns.logical_rows(new) == old['config']['providers']
    assert config(new, tmp_path).module_sources == config(old, tmp_path).module_sources
    from amplifier_foundation import Bundle
    from amplifier_web.host.session import _apply_settings
    with patch('amplifier_web.decision_accounts.catalogs', return_value={
            'provider-conditional': {'module': 'hooks-conditional', 'when': {'chat_enabled': False}}}):
        before = _apply_settings(Bundle(name='before'), config(old, tmp_path)).to_mount_plan()
        after = _apply_settings(Bundle(name='after'), config(new, tmp_path)).to_mount_plan()
    assert after == before


def test_unrelated_ordinary_module_overlays_keep_foundation_semantics(tmp_path):
    base = old_settings(provider('provider-third-party-a', 'chat'))
    local = {'config': {'providers': [{'id': 'chat', 'module': 'provider-third-party-b'}]}}
    assert ns.translate(local, {}, inherited=base) == local


@pytest.mark.parametrize('first,second', [(True, False), (False, True)])
def test_role_transition_refuses_migration_but_passive_read_and_files_survive(tmp_path, first, second):
    base = old_settings(provider('provider-conditional', chat_enabled=first))
    local = {'config': {'providers': [{'id': 'one', 'config': {'chat_enabled': second}}]}}
    paths = [tmp_path / 'global.yaml', tmp_path / 'local.yaml']
    for path, value in zip(paths, [base, local]):
        path.write_text(yaml.safe_dump(value))
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError, match='chat role'):
        ns.migrate_paths(paths, definitions=DEFINITIONS)
    assert [path.read_bytes() for path in paths] == before
    assert ns.read_scopes(paths) == ns.merge_settings(base, local)


def test_null_decision_options_remain_inert_not_a_migration_error():
    old = old_settings(provider(decisions=None))
    assert ns.logical_rows(ns.translate(old, DEFINITIONS)) == old['config']['providers']


@pytest.mark.parametrize('module', ['provider-only', 'provider-chat'])
def test_read_between_scope_commits_preserves_full_logical_configuration(tmp_path, module):
    base = old_settings(provider(module, decisions={'enabled': True, 'id': 'existing', 'model': 'base'}))
    local = {'config': {'providers': [{'id': 'one', 'module': module,
                                     'config': {'decisions': {'model': 'local'}}}]}}
    paths = [tmp_path / 'global.yaml', tmp_path / 'local.yaml']
    paths[0].write_text(yaml.safe_dump(ns.translate(base, DEFINITIONS)))
    paths[1].write_text(yaml.safe_dump(local))
    assert ns.logical_rows(ns.read_scopes(paths)) == ns.merge_settings(base, local)['config']['providers']


def test_saved_selected_mixed_connection_key_and_runtime_plan_survive_migration(tmp_path, monkeypatch):
    import json
    from amplifier_foundation import Bundle
    from amplifier_web.host.session import _apply_settings
    from amplifier_web.provider_catalog import configured_binding
    from amplifier_web.setup import SetupManager
    from amplifier_web.shared_settings import settings_paths
    monkeypatch.setattr(decision_settings, 'catalogs', lambda: DEFINITIONS)
    manager = SetupManager(tmp_path / 'app')
    paths = settings_paths(tmp_path, shared_home=manager.store.shared_home)
    path = paths['global']
    path.parent.mkdir(parents=True, exist_ok=True)
    old = old_settings(provider('provider-chat', decisions={
        'enabled': True, 'backends': {'existing': {'model': 'exact-model', 'enabled': True}}}))
    path.write_text(yaml.safe_dump(old))
    before_key = manager.catalog_key({'id': 'one'}, tmp_path, binding=True)
    before_config = manager.config(tmp_path)
    with patch('amplifier_web.decision_accounts.bind_sources'):
        before_plan = _apply_settings(Bundle(name='before'), before_config).to_mount_plan()
    selected = tmp_path / 'saved-selection.json'
    selected.write_text(json.dumps({'selectionRevision': 7, 'selectionBinding': before_key,
        'selectionBindingPending': None, 'provider': {'instance': 'one', 'model': 'chat-choice', 'effort': 'low'}}))
    snapshot = selected.read_bytes()
    ns.migrate_paths(paths.values(), definitions=DEFINITIONS)
    assert configured_binding(tmp_path / 'app', tmp_path, 'one', saved=before_key) == before_key
    assert manager.catalog_key({'id': 'one'}, tmp_path, binding=True) == before_key
    with patch('amplifier_web.decision_accounts.bind_sources'):
        after_plan = _apply_settings(Bundle(name='after'), manager.config(tmp_path)).to_mount_plan()
    assert after_plan == before_plan
    assert selected.read_bytes() == snapshot


@pytest.mark.parametrize('mode', [0o400, 0o444])
def test_readonly_settings_owner_is_not_replaced(tmp_path, mode):
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump(old_settings(provider())))
    path.chmod(mode)
    before = path.read_bytes()
    try:
        with pytest.raises(PermissionError):
            ns.migrate_paths([path], definitions=DEFINITIONS)
        assert path.read_bytes() == before
    finally:
        path.chmod(0o600)