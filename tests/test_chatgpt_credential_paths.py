"""Settings edits must retain the account a ChatGPT connection already uses."""
import json

import pytest

from amplifier_web.setup import SetupManager, account_connected


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    # The provider's legacy default is under HOME, independent of AMPLIFIER_HOME.
    monkeypatch.setenv('HOME', str(tmp_path / 'user'))
    tokens = tmp_path / 'user' / '.amplifier' / 'openai-chatgpt-oauth.json'
    tokens.parent.mkdir(parents=True)
    tokens.write_text(json.dumps({'access_token': 'synthetic-access',
                                  'refresh_token': 'synthetic-refresh',
                                  'expires_at': '2099-01-01T00:00:00+00:00'}))
    return SetupManager(tmp_path / 'app'), tokens


def install(manager, workspace, rows):
    manager.store.update(workspace, 'global',
                         lambda settings: settings.update(config={'providers': rows}))


def chatgpt(identity_field=None):
    row = {'module': 'provider-openai-chatgpt', 'config': {'default_model': 'before'}}
    if identity_field:
        row[identity_field] = 'existing'
    return row


@pytest.mark.parametrize('identity_field', [None, 'id', 'instance_id'])
@pytest.mark.parametrize('scope', ['global', 'local'])
async def test_model_edit_preserves_implicit_sign_in(accounts, tmp_path, identity_field, scope):
    manager, tokens = accounts
    before = tokens.read_bytes()
    install(manager, tmp_path, [chatgpt(identity_field)])
    identity = 'existing' if identity_field else 'openai-chatgpt'
    result = await manager.perform('providers.save', {
        'workspace': str(tmp_path), 'id': identity, 'scope': scope,
        'config': {'default_model': 'after', 'priority': 12}})
    saved = manager.config(tmp_path).providers[0]['config']
    assert saved['default_model'] == 'after'
    assert saved['priority'] == 12
    assert 'token_file_path' not in saved
    assert saved['login_on_mount'] is False
    assert result['providers'][0]['accountConnected']
    assert 'synthetic-access' not in str(result) and 'synthetic-refresh' not in str(result)
    assert tokens.read_bytes() == before
    assert list(tokens.parent.glob('*oauth.json')) == [tokens]
    assert not list(manager.store.shared_home.glob('*oauth.json'))


@pytest.mark.parametrize('path_exists', [False, True])
async def test_explicit_sign_in_is_retained_even_when_missing(accounts, tmp_path, path_exists):
    manager, tokens = accounts
    explicit = tmp_path / 'separate-account.json'
    if path_exists:
        explicit.write_bytes(tokens.read_bytes())
    row = chatgpt('id')
    row['config']['token_file_path'] = str(explicit)
    install(manager, tmp_path, [row])
    result = await manager.perform('providers.save', {
        'workspace': str(tmp_path), 'id': 'existing', 'config': {'default_model': 'after'}})
    saved = manager.config(tmp_path).providers[0]['config']
    assert saved['token_file_path'] == str(explicit)
    assert result['providers'][0]['accountConnected'] is path_exists
    assert explicit.exists() is path_exists


async def test_new_connections_do_not_inherit_legacy_sign_in(accounts, tmp_path):
    manager, tokens = accounts
    for identity in ('new-one', 'new-two'):
        result = await manager.perform('providers.save', {
            'workspace': str(tmp_path), 'module': 'provider-openai-chatgpt',
            'id': identity, 'config': {}})
        row = next(row for row in result['providers'] if row['id'] == identity)
        assert row['config']['token_file_path'] == str(
            manager.store.shared_home / f'openai-chatgpt-{identity}-oauth.json')
        assert not row['accountConnected']
    assert len({row['config']['token_file_path'] for row in manager.config(tmp_path).providers}) == 2
    assert tokens.exists()


async def test_switching_provider_module_uses_separate_account(accounts, tmp_path):
    manager, tokens = accounts
    install(manager, tmp_path, [{'id': 'switched', 'module': 'provider-test',
                               'config': {'token_file_path': str(tokens)}}])
    result = await manager.perform('providers.save', {
        'workspace': str(tmp_path), 'module': 'provider-openai-chatgpt',
        'id': 'switched', 'config': {}})
    row = result['providers'][0]
    assert row['config']['token_file_path'] != str(tokens)
    assert not row['accountConnected']


async def test_reorder_preserves_existing_implicit_account(accounts, tmp_path):
    manager, _ = accounts
    install(manager, tmp_path, [chatgpt(), {'id': 'other', 'module': 'provider-test'}])
    result = await manager.perform('providers.reorder', {
        'workspace': str(tmp_path), 'ids': ['other', 'openai-chatgpt'],
        'expectedIds': ['openai-chatgpt', 'other']})
    row = next(row for row in result['providers'] if row['id'] == 'openai-chatgpt')
    assert 'token_file_path' not in row['config']
    assert row['accountConnected']


async def test_guided_model_change_preserves_legacy_account(accounts, tmp_path):
    manager, tokens = accounts
    before = tokens.read_bytes()
    install(manager, tmp_path, [chatgpt()])
    result = await manager.perform('providers.finishSetup', {
        'workspace': str(tmp_path), 'id': 'openai-chatgpt', 'model': 'after'})
    row = result['providers'][0]
    assert row['config']['default_model'] == 'after'
    assert 'token_file_path' not in row['config']
    assert row['accountConnected']
    assert tokens.read_bytes() == before


def test_status_uses_provider_default_only_when_path_is_unspecified(accounts):
    _, tokens = accounts
    assert account_connected({})
    assert account_connected({'token_file_path': None})
    assert not account_connected({'token_file_path': str(tokens.parent / 'missing.json')})
    assert not account_connected({'token_file_path': ''})
    assert not account_connected({'token_file_path': 42})
