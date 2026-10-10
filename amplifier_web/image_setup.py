"""Guided image connection edits preserve the connection and tool policy."""
import copy


async def configure_images(manager, args, workspace, scope):
    identity = args['id']
    enabled = args['enabled']
    if not isinstance(enabled, bool):
        raise ValueError('Choose whether to enable image generation.')
    before = manager.catalog_key(args, workspace)
    if enabled:
        catalog = await manager.cached_probe('providers.imageModels', args, workspace)
        info = catalog.get('providerMetadata', {}).get('imageGeneration') or {}
        if not catalog.get('imageModelsSupported') or info.get('schemaVersion') != 1 or info.get('configKey') != 'image_generation':
            raise ValueError('This connection does not offer image setup. Update the provider or choose another connection.')
        model = args.get('model', '').strip()
        models = {row.get('id') for row in catalog.get('imageModels', []) if isinstance(row, dict)}
        automatic = info.get('automaticModel')
        if not model or not models or (model not in models and model != automatic):
            raise ValueError('Choose an image model from this connection’s current catalog.')
        if before != manager.catalog_key(args, workspace):
            raise ValueError('The connection changed. Refresh the image choices before saving.')
    effective = manager.config(workspace)
    rows = effective.providers
    def row_id(row):
        return row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')
    row = next((row for row in rows if row_id(row) == identity), None)
    disabled = effective.settings.get('configurator', {}).get('disabled', {}).get('providers', [])
    if not row or identity in disabled or row.get('enabled') is False:
        raise ValueError('This connection is no longer available. Refresh your connections.')
    config = copy.deepcopy(row.get('config', {}))
    image = config.get('image_generation') or {}
    if not isinstance(image, dict):
        raise ValueError('Review this connection’s image settings in Advanced connection settings.')
    if not enabled and not image:
        return {'providers': manager.provider_rows(workspace), 'configurationChanged': False,
                'imageSetupCompletion': {'id': identity, 'enabled': False, 'takesEffect': 'new_sessions'}}
    if enabled:
        backend = image.get('id', 'images')
        for other in rows:
            other_image = other.get('config', {}).get('image_generation') or {}
            if row_id(other) != identity and row_id(other) not in disabled and other.get('enabled') is not False and isinstance(other_image, dict) and other_image.get('enabled') is True and other_image.get('id', 'openai') == backend:
                raise ValueError('Another connection already supplies these images. Turn off image generation on that connection before switching accounts.')
        image = {**image, 'enabled': True, 'id': backend, 'model': model}
    else:
        image = {**image, 'enabled': False}
    config['image_generation'] = image
    result = manager._provider_mutation({**args, 'module': row['module'], 'config': config}, workspace, scope)
    result['imageSetupCompletion'] = {'id': identity, 'enabled': enabled, 'takesEffect': 'new_sessions'}
    return result
