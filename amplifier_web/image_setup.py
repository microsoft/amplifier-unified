"""Guided image connection edits preserve the connection and tool policy."""
import copy


def row_id(row):
    return row.get('id') or row.get('instance_id') or row['module'].removeprefix('provider-')


def select_images(manager, workspace, scope, rows, identity, image, enabled):
    """Switch the everyday image choice in one locked settings-file write."""
    from .shared_settings import overlay, settings_paths, read_yaml
    paths = settings_paths(workspace, shared_home=manager.store.shared_home,
                           global_only=manager.global_only)
    selected = next(row for row in rows if row_id(row) == identity)
    def preview(settings, stop=None):
        # Match Foundation read_settings: dictionaries overlay, and provider
        # rows merge by id (or module). Preview the uncommitted file in memory.
        result, providers = {}, []
        for level, path in paths.items():
            value = settings if level == scope else read_yaml(path)
            result = overlay(result, value)
            for row in value.get('config', {}).get('providers', []):
                key = row.get('id') or row.get('module')
                index = next((i for i, p in enumerate(providers)
                              if (p.get('id') or p.get('module')) == key), None)
                if index is None: providers.append(copy.deepcopy(row))
                else: providers[index] = overlay(providers[index], row)
            if level == stop: break
        result.setdefault('config', {})['providers'] = providers
        return result
    def mutate(settings):
        # Only image fields are edited. Never promote a workspace's credentials,
        # chat model, source overrides or other settings into a broader scope.
        inherited = preview(settings, stop=scope)
        if enabled and not any(row_id(row) == identity for row in inherited.get('config', {}).get('providers', [])):
            raise ValueError('This connection is only saved for this workspace. Choose this workspace under More options.')
        targets = {row_id(row): row for row in rows
                   if isinstance(row.get('config', {}).get('image_generation'), dict)
                   and row['config']['image_generation'].get('enabled') is True}
        targets[identity] = selected
        scoped = settings.setdefault('config', {}).setdefault('providers', [])
        for key, source in targets.items():
            row = next((row for row in scoped if row_id(row) == key), None)
            if row is None:
                row = {'id': key, 'module': source['module'], 'config': {}}
                scoped.append(row)
            patch = image if key == identity else {'enabled': False}
            old = row.setdefault('config', {}).get('image_generation') or {}
            if not isinstance(old, dict):
                raise ValueError('Review the saved image configuration in Advanced connection settings.')
            row['config']['image_generation'] = {**old, **patch}
        # A more-specific override must not turn a successful save into two
        # active backends. Reject before writing, with a useful scope choice.
        effective = preview(settings)
        disabled = effective.get('configurator', {}).get('disabled', {}).get('providers', [])
        active = [row_id(row) for row in effective.get('config', {}).get('providers', [])
                  if row_id(row) not in disabled and row.get('enabled') is not False
                  and isinstance(row.get('config', {}).get('image_generation'), dict)
                  and row['config']['image_generation'].get('enabled') is True]
        selected_effective = next((row.get('config', {}).get('image_generation', {})
                                   for row in effective.get('config', {}).get('providers', [])
                                   if row_id(row) == identity), {})
        if (active != ([identity] if enabled else []) or enabled and
                any(selected_effective.get(key) != image.get(key) for key in ('model', 'id'))):
            raise ValueError('This workspace overrides image settings. Choose this workspace under More options to change its image provider.')
    manager.store.update(workspace, scope, mutate)
    return {'providers': manager.provider_rows(workspace), 'takesEffect': 'new_sessions',
            'scope': scope, 'configurationChanged': True}


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
    row = next((row for row in rows if row_id(row) == identity), None)
    disabled = effective.settings.get('configurator', {}).get('disabled', {}).get('providers', [])
    if not row or identity in disabled or row.get('enabled') is False:
        raise ValueError('This connection is no longer available. Refresh your connections.')
    config = copy.deepcopy(row.get('config', {}))
    image = config.get('image_generation') or {}
    if not isinstance(image, dict):
        raise ValueError('Review this connection’s image settings in Advanced connection settings.')
    if not enabled and not image and not args.get('exclusive'):
        return {'providers': manager.provider_rows(workspace), 'configurationChanged': False,
                'imageSetupCompletion': {'id': identity, 'enabled': False, 'takesEffect': 'new_sessions'}}
    if enabled:
        backend = image.get('id', 'images')
        for other in rows if not args.get('exclusive') else []:
            other_image = other.get('config', {}).get('image_generation') or {}
            if row_id(other) != identity and row_id(other) not in disabled and other.get('enabled') is not False and isinstance(other_image, dict) and other_image.get('enabled') is True and other_image.get('id', 'openai') == backend:
                raise ValueError('Another connection already supplies these images. Turn off image generation on that connection before switching accounts.')
        image = {**image, 'enabled': True, 'id': backend, 'model': model}
    else:
        image = {**image, 'enabled': False}
    config['image_generation'] = image
    result = (select_images(manager, workspace, scope, rows, identity, image, enabled)
              if args.get('exclusive') else
              manager._provider_mutation({**args, 'module': row['module'], 'config': config}, workspace, scope))
    result['imageSetupCompletion'] = {'id': identity, 'enabled': enabled, 'takesEffect': 'new_sessions'}
    return result
