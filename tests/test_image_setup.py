import copy
import pytest

from amplifier_web.setup import SetupManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    value = SetupManager(tmp_path / 'app')
    async def no_routing(workspace): pass
    monkeypatch.setattr(value, 'ensure_routing_catalog', no_routing)
    async def catalog(action, args, workspace):
        assert action == 'providers.imageModels'
        return {'imageModelsSupported': True, 'imageModels': [{'id': 'image-fixture'}],
                'providerMetadata': {'imageGeneration': {'schemaVersion': 1, 'configKey': 'image_generation'}}}
    monkeypatch.setattr(value, 'cached_probe', catalog)
    return value


async def save(manager, workspace, identity='one', image=None):
    config = {'default_model': 'kept-chat-model', 'reasoning_effort': 'high',
              'api_key': '${FIXTURE_IMAGE_CREDENTIAL}', 'opaque': {'keep': True}}
    if image is not None: config['image_generation'] = image
    await manager.perform('providers.save', {'workspace': str(workspace), 'id': identity,
        'module': 'provider-openai', 'config': config})
    return config


@pytest.mark.asyncio
async def test_image_setup_preserves_chat_credentials_and_explicit_tool_policy(manager, tmp_path):
    previous = await save(manager, tmp_path)
    manager.store.update(tmp_path, 'global', lambda settings: settings.update(
        config={**settings['config'], 'tools': [{'module': 'tool-image', 'config': {'allow_paid': False}}]},
        web_bundles={'excluded': ['kept-disabled-feature']}, routing={'matrix': 'kept'}))
    before = copy.deepcopy(manager.store.read(tmp_path, 'global'))
    result = await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'one', 'enabled': True, 'model': 'image-fixture'})
    after = manager.store.read(tmp_path, 'global')
    config = after['config']['providers'][0]['config']
    assert config == {**previous, 'image_generation': {'enabled': True, 'id': 'images', 'model': 'image-fixture'}}
    assert after['config']['tools'] == before['config']['tools']
    assert after['routing'] == before['routing']
    assert after['web_bundles'] == before['web_bundles']
    assert result['imageSetupCompletion']['enabled'] is True


@pytest.mark.asyncio
async def test_image_setup_rejects_unknown_models_without_saving(manager, tmp_path):
    await save(manager, tmp_path)
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='current catalog'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'not-in-catalog'})
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
async def test_automatic_images_require_provider_contract_and_preserve_pinned_chat(manager, tmp_path, monkeypatch):
    previous = await save(manager, tmp_path)
    original = manager.cached_probe
    async def automatic(*args):
        result = await original(*args)
        result['providerMetadata']['imageGeneration']['automaticModel'] = 'auto'
        return result
    monkeypatch.setattr(manager, 'cached_probe', automatic)
    await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'one', 'enabled': True, 'model': 'auto'})
    config = manager.config(tmp_path).providers[0]['config']
    assert config == {**previous, 'image_generation': {'enabled': True, 'id': 'images', 'model': 'auto'}}
    monkeypatch.setattr(manager, 'cached_probe', original)
    with pytest.raises(ValueError, match='current catalog'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'auto'})


@pytest.mark.asyncio
async def test_image_setup_does_not_silently_switch_accounts(manager, tmp_path):
    await save(manager, tmp_path)
    await save(manager, tmp_path, 'other', {'enabled': True, 'id': 'images', 'model': 'kept-image'})
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='Another connection'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'image-fixture'})
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
async def test_image_disable_needs_no_catalog_and_keeps_custom_values(manager, tmp_path, monkeypatch):
    image = {'enabled': True, 'id': 'custom-backend', 'model': 'kept-image', 'timeout': 420}
    await save(manager, tmp_path, image=image)
    async def unexpected(*args): pytest.fail('Disabling must not require network discovery')
    monkeypatch.setattr(manager, 'cached_probe', unexpected)
    await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'one', 'enabled': False, 'scope': 'local'})
    assert manager.config(tmp_path).providers[0]['config']['image_generation'] == {**image, 'enabled': False}
    assert manager.store.read(tmp_path, 'global')['config']['providers'][0]['config']['image_generation'] == image


@pytest.mark.asyncio
async def test_image_setup_rejects_a_connection_changed_during_discovery(manager, tmp_path, monkeypatch):
    await save(manager, tmp_path)
    original = manager.cached_probe
    async def changing(*args):
        manager.store.update(tmp_path, 'global', lambda settings:
            settings['config']['providers'][0]['config'].update(default_model='newer-choice'))
        return await original(*args)
    monkeypatch.setattr(manager, 'cached_probe', changing)
    with pytest.raises(ValueError, match='connection changed'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'image-fixture'})
    row = manager.config(tmp_path).providers[0]
    assert row['config']['default_model'] == 'newer-choice'
    assert 'image_generation' not in row['config']


@pytest.mark.asyncio
async def test_image_probe_never_uses_chat_discovery_or_generation(monkeypatch):
    from amplifier_web import provider_probe
    class Provider:
        def get_info(self): return {}
        def get_image_generation_info(self): return {'schemaVersion': 1, 'configKey': 'image_generation'}
        async def list_image_models(self): return [{'id': 'image-fixture', 'entitlement': 'unverified'}]
        async def list_models(self): pytest.fail('Image choices must not use the chat catalog')
        async def complete(self, *args): pytest.fail('Discovery must not call a model')
    async def schema(*args, **kwargs): return {'fields': []}
    async def close(*args): pass
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setattr(provider_probe, 'construct_schema_provider', lambda *args: Provider())
    monkeypatch.setattr(provider_probe, 'construct_provider', lambda *args: Provider())
    monkeypatch.setattr(provider_probe, 'config_schema', schema)
    monkeypatch.setattr(provider_probe, 'close_provider', close)
    result = await provider_probe.query({'module': 'provider-fixture', 'action': 'providers.imageModels'})
    assert result['imageModels'] == [{'id': 'image-fixture', 'entitlement': 'unverified'}]
    assert result['imageModelsSupported'] is True


@pytest.mark.asyncio
async def test_unsupported_image_setup_does_not_change_connection(manager, tmp_path, monkeypatch):
    await save(manager, tmp_path)
    before = manager.store.read(tmp_path, 'global')
    async def unsupported(*args):
        return {'imageModelsSupported': False, 'imageModels': []}
    monkeypatch.setattr(manager, 'cached_probe', unsupported)
    with pytest.raises(ValueError, match='does not offer image setup'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'image-fixture'})
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
async def test_disable_without_image_settings_is_noop(manager, tmp_path, monkeypatch):
    await save(manager, tmp_path)
    before = manager.store.read(tmp_path, 'global')
    async def unexpected(*args): pytest.fail('Disable must not discover models')
    monkeypatch.setattr(manager, 'cached_probe', unexpected)
    result = await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'one', 'enabled': False})
    assert result['configurationChanged'] is False
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
async def test_disabled_connection_cannot_enable_images(manager, tmp_path):
    await save(manager, tmp_path)
    manager.store.update(tmp_path, 'global', lambda settings:
        settings.setdefault('configurator', {}).setdefault('disabled', {}).update(providers=['one']))
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='no longer available'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'one', 'enabled': True, 'model': 'image-fixture'})
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
async def test_shared_image_catalog_is_attributed_to_requested_connection(tmp_path, monkeypatch):
    manager = SetupManager(tmp_path / 'app')
    for identity in ['first', 'second']:
        await save(manager, tmp_path, identity)
    calls = []
    async def probe(action, args, workspace):
        calls.append(args['id'])
        return {'imageModelsProviderId': args['id'], 'imageModelsSupported': True,
                'imageModels': [{'id': 'fixture'}]}
    monkeypatch.setattr(manager, 'probe', probe)
    first = await manager.cached_probe('providers.imageModels', {'id': 'first'}, tmp_path)
    second = await manager.cached_probe('providers.imageModels', {'id': 'second'}, tmp_path)
    assert first['imageModelsProviderId'] == 'first'
    assert second['imageModelsProviderId'] == 'second'
    assert second['imageModels'] == first['imageModels']
    assert calls == ['first']

@pytest.mark.asyncio
async def test_exclusive_switch_preserves_accounts_and_disables_all_prior_backends(manager, tmp_path, monkeypatch):
    first = await save(manager, tmp_path, 'one', {'enabled': True, 'id': 'old', 'model': 'pinned', 'timeout': 420})
    second = await save(manager, tmp_path, 'other')
    await save(manager, tmp_path, 'third', {'enabled': True, 'id': 'another', 'model': 'kept'})
    writes = []
    update = manager.store.update
    def counted(*args):
        result = update(*args)
        writes.append(copy.deepcopy(manager.config(tmp_path).providers))
        return result
    monkeypatch.setattr(manager.store, 'update', counted)
    await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'other', 'enabled': True, 'model': 'image-fixture', 'exclusive': True})
    assert len(writes) == 1
    rows = {row['id']: row['config'] for row in writes[0]}
    assert rows['one'] == {**first, 'image_generation': {**first['image_generation'], 'enabled': False}}
    assert rows['other'] == {**second, 'image_generation': {'enabled': True, 'id': 'images', 'model': 'image-fixture'}}
    assert rows['third']['image_generation'] == {'enabled': False, 'id': 'another', 'model': 'kept'}


@pytest.mark.asyncio
async def test_rejected_exclusive_switch_keeps_previous_choice(manager, tmp_path):
    await save(manager, tmp_path, 'one', {'enabled': True, 'id': 'images', 'model': 'kept'})
    await save(manager, tmp_path, 'other')
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='current catalog'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'other', 'enabled': True, 'model': 'unavailable', 'exclusive': True})
    assert manager.store.read(tmp_path, 'global') == before


@pytest.mark.asyncio
@pytest.mark.parametrize('override', ['old-provider', 'same-provider-model'])
async def test_exclusive_global_switch_rejects_shadowed_choice_atomically(manager, tmp_path, override):
    await save(manager, tmp_path, 'one', {'enabled': True, 'id': 'images', 'model': 'kept'})
    await save(manager, tmp_path, 'other')
    identity = 'one' if override == 'old-provider' else 'other'
    manager.store.update(tmp_path, 'local', lambda settings: settings.update(config={'providers': [
        {'id': identity, 'module': 'provider-openai', 'config': {'image_generation': {'enabled': True, 'model': 'local-model'}}}]}))
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='workspace overrides'):
        await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
            'id': 'other', 'enabled': True, 'model': 'image-fixture', 'exclusive': True})
    assert manager.store.read(tmp_path, 'global') == before
    await manager.perform('providers.configureImages', {'workspace': str(tmp_path), 'scope': 'local',
        'id': 'other', 'enabled': True, 'model': 'image-fixture', 'exclusive': True})
    assert manager.store.read(tmp_path, 'global') == before
    rows = {row['id']: row['config'] for row in manager.config(tmp_path).providers}
    assert rows['one']['image_generation']['enabled'] is False
    assert rows['other']['image_generation']['model'] == 'image-fixture'
    # Local selection must not copy account credentials into the overlay.
    assert all(set(row['config']) == {'image_generation'} for row in manager.store.read(tmp_path, 'local')['config']['providers'])


@pytest.mark.asyncio
async def test_exclusive_disable_all_needs_no_discovery(manager, tmp_path, monkeypatch):
    for identity in ['one', 'other']:
        await save(manager, tmp_path, identity, {'enabled': True, 'id': identity, 'model': 'kept'})
    async def unexpected(*args): pytest.fail('Disable must not discover models')
    monkeypatch.setattr(manager, 'cached_probe', unexpected)
    await manager.perform('providers.configureImages', {'workspace': str(tmp_path),
        'id': 'one', 'enabled': False, 'exclusive': True})
    assert all(row['config']['image_generation']['enabled'] is False for row in manager.config(tmp_path).providers)
