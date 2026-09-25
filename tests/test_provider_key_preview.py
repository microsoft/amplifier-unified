"""Credential identity is safe public state, using the service's effective config."""
import json

import pytest

from amplifier_web.host.config import worker_environment
from amplifier_web.setup import SetupManager, environment_credential


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # The autouse fixture isolates shared settings; preserve global loader state.
    monkeypatch.setattr('amplifier_web.host.config._KEY_FILE_VALUES', {})
    for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'TEAM_KEY'):
        monkeypatch.delenv(name, raising=False)
    return SetupManager(tmp_path / 'app')


def configure(manager, tmp_path, rows):
    manager.store.update(tmp_path, 'global', lambda s: s.update(config={'providers': rows}))


def test_effective_key_prefers_instance_config_and_reference_over_default_env(manager, tmp_path, monkeypatch):
    default = 'launch-not-the-selected-key-0001'
    team = 'team01-private-middle-value-2222'
    literal = 'saved1-private-middle-value-3333'
    monkeypatch.setenv('OPENAI_API_KEY', default)
    monkeypatch.setenv('TEAM_KEY', team)
    configure(manager, tmp_path, [
        {'module': 'provider-openai', 'id': 'default', 'config': {}},
        {'module': 'provider-openai', 'id': 'team', 'config': {'api_key': '${TEAM_KEY}'}},
        {'module': 'provider-openai', 'id': 'literal', 'config': {'api_key': literal}},
        {'module': 'provider-openai', 'id': 'missing', 'config': {'api_key': '${UNSET_PREVIEW_KEY}'}},
    ])
    monkeypatch.delenv('UNSET_PREVIEW_KEY', raising=False)
    rows = manager.provider_rows(tmp_path)
    previews = {r['id']: r['credential']['preview'] for r in rows}
    assert previews['default'] == {'status': 'available', 'source': 'environment', 'envVar': 'OPENAI_API_KEY', 'masked': 'launch…0001'}
    assert previews['team']['masked'] == 'team01…2222'
    assert previews['team']['envVar'] == 'TEAM_KEY'
    assert previews['literal'] == {'status': 'available', 'source': 'configuration', 'envVar': '', 'masked': 'saved1…3333'}
    assert previews['missing']['status'] == 'missing'
    assert previews['missing']['masked'] is None
    for secret in (default, team, literal):
        assert secret not in json.dumps(rows)
    assert rows[2]['config']['api_key'] == '[REDACTED]'


@pytest.mark.asyncio
async def test_file_rotation_refreshes_but_launch_environment_wins(manager, tmp_path, monkeypatch):
    keyfile = manager.store.shared_home / 'keys.env'
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.write_text('TEAM_KEY=file01-private-middle-value-0001\n')
    configure(manager, tmp_path, [{'module': 'provider-openai', 'config': {'api_key': '${TEAM_KEY}'}}])
    first = await manager.perform('providers.list', {'workspace': str(tmp_path)})
    preview = first['providers'][0]['credential']['preview']
    assert preview['masked'] == 'file01…0001'
    assert preview['source'] == 'key-file'
    assert 'TEAM_KEY' not in worker_environment()
    keyfile.write_text('TEAM_KEY=file02-private-middle-value-0002\n')
    refreshed = await manager.perform('providers.list', {'workspace': str(tmp_path)})
    assert refreshed['providers'][0]['credential']['preview']['masked'] == 'file02…0002'
    monkeypatch.setenv('TEAM_KEY', 'launch-private-middle-value-3333')
    refreshed = await manager.perform('providers.list', {'workspace': str(tmp_path)})
    preview = refreshed['providers'][0]['credential']['preview']
    assert preview['masked'] == 'launch…3333' and preview['source'] == 'environment'
    assert worker_environment()['TEAM_KEY'] == 'launch-private-middle-value-3333'
    assert 'private-middle-value' not in json.dumps(refreshed)
    assert keyfile.read_text() == 'TEAM_KEY=file02-private-middle-value-0002\n'


@pytest.mark.asyncio
async def test_private_save_and_agent_credential_check_return_only_masked_identity(manager, tmp_path):
    secret = 'saved2-secret-fixture-material-4444'
    result = await manager.perform('providers.save', {
        'workspace': str(tmp_path), 'module': 'provider-anthropic', 'id': 'preview', 'apiKey': secret,
    })
    preview = result['providers'][0]['credential']['preview']
    assert preview['masked'] == 'saved2…4444' and preview['source'] == 'key-file'
    result = await manager.perform('providers.credentials', {
        'workspace': str(tmp_path), 'module': 'provider-anthropic', 'envVar': preview['envVar'],
    })
    assert result['credentialCheck']['preview'] == preview
    assert secret not in json.dumps(result)


@pytest.mark.parametrize('value', ['tiny', '123456789012345', 'key-with-\n-control-and-tail', 'nonascii-🔑-private-tail'])
def test_short_or_unusual_values_expose_no_characters(value):
    result = environment_credential('provider-custom', {'api_key': value})
    assert result['preview']['masked'] == '••••'
    assert value not in json.dumps(result, ensure_ascii=False)


def test_oauth_tokens_are_not_previewed_and_unknown_auth_is_not_guessed(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-api-key-private-9999')
    assert environment_credential('provider-openai-chatgpt', {'api_key': 'secret', 'access_token': 'private'})['preview'] is None
    assert environment_credential('provider-custom')['preview']['status'] == 'provider-managed'
    assert environment_credential('provider-custom', {'api_key': 'custom-private-middle-5555'})['preview']['masked'] == 'custom…5555'
    assert environment_credential('provider-github-copilot', {'github_token': 'github-private-middle-6666'})['preview']['masked'] == 'github…6666'


def test_configured_environment_defaults_use_runtime_expansion(monkeypatch):
    monkeypatch.delenv('MISSING_KEY_PREVIEW', raising=False)
    raw = {'api_key': '${MISSING_KEY_PREVIEW:-fallback-private-middle-7777}'}
    preview = environment_credential('provider-openai', raw)['preview']
    assert preview == {'status': 'available', 'source': 'configuration', 'masked': 'fallba…7777', 'envVar': ''}
    assert environment_credential('provider-openai', {'api_key': '${MISSING_KEY_PREVIEW}'})['preview']['masked'] is None


@pytest.mark.parametrize('module,name', [
    ('provider-azure-openai', 'AZURE_OPENAI_KEY'),
    ('provider-ollama', 'OLLAMA_API_KEY'),
    ('provider-vllm', 'VLLM_API_KEY'),
])
def test_provider_specific_environment_fallbacks(module, name, monkeypatch):
    monkeypatch.delenv('AZURE_OPENAI_API_KEY', raising=False)
    monkeypatch.setenv(name, 'actual-private-middle-key-1234')
    preview = environment_credential(module, {})['preview']
    assert preview['envVar'] == name and preview['masked'] == 'actual…1234'


def test_copilot_sdk_priority_and_explicit_unified_override(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'github-private-middle-key-1111')
    monkeypatch.setenv('COPILOT_AGENT_TOKEN', 'agent1-private-middle-key-2222')
    preview = environment_credential('provider-github-copilot', {})['preview']
    assert preview['envVar'] == 'COPILOT_AGENT_TOKEN'
    assert preview['masked'] == 'agent1…2222'
    preview = environment_credential('provider-github-copilot', {'github_token': '${GITHUB_TOKEN}'})['preview']
    assert preview['masked'] == 'github…1111'


def test_compatible_api_reports_environment_overriding_saved_key(monkeypatch):
    monkeypatch.setenv('CHAT_COMPLETIONS_API_KEY', 'actual-private-middle-key-1111')
    preview = environment_credential('provider-chat-completions', {'api_key': 'unused-private-middle-key-2222'})['preview']
    assert preview['source'] == 'environment' and preview['envVar'] == 'CHAT_COMPLETIONS_API_KEY'
    assert preview['masked'] == 'actual…1111'
    # An explicit environment check still reports the variable being checked.
    monkeypatch.setenv('TEAM_KEY', 'choice-private-middle-key-3333')
    checked = environment_credential('provider-chat-completions', env_var='TEAM_KEY')['preview']
    assert checked['envVar'] == 'TEAM_KEY' and checked['masked'] == 'choice…3333'
