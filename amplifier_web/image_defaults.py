"""Choose an image connection before dispatch, never retry an image operation."""
import asyncio
import inspect


def preference(settings, rows):
    saved = settings.get('image_generation', {})
    if saved.get('mode') in {'automatic', 'off', 'selected'}:
        return saved['mode']
    images = [row.get('config', {}).get('image_generation', {}) for row in rows]
    if any(image.get('enabled') is True for image in images if isinstance(image, dict)):
        return 'selected'
    # Old versions had no global off switch. Retain deliberate legacy opt-outs.
    if any(image.get('enabled') is False for image in images if isinstance(image, dict)):
        return 'off'
    return 'automatic'


async def apply_defaults(plan, settings, *, class_loader=None, check_available=None):
    """Use capability/catalog evidence from installed providers, in priority order.

    This runs before provider mounting. Only metadata and model listing occur;
    failures can advance to another connection because no generation was sent.
    The chosen backend owns all subsequent generation and outcome handling.
    """
    from .provider_environment import provider_class, construct_provider, close_provider
    rows = plan.get('providers', [])
    mode = preference(settings, rows)
    if mode == 'selected':
        return
    for row in rows:
        if check_available is not None and not check_available(row):
            continue
        image = row.get('config', {}).get('image_generation')
        if isinstance(image, dict):
            image['enabled'] = False
    if mode == 'off':
        return
    for row in sorted(rows, key=lambda row: row.get('config', {}).get('priority', 100)):
        if row.get('enabled') is False or (check_available is not None and not check_available(row)):
            continue
        provider = None
        try:
            cls = (class_loader or provider_class)(row['module'])
            if not callable(getattr(cls, 'get_image_generation_info', None)) or not callable(getattr(cls, 'list_image_models', None)):
                continue
            provider = construct_provider(cls, row.get('config', {}))
            async def available():
                info = provider.get_image_generation_info()
                if inspect.isawaitable(info): info = await info
                if not isinstance(info, dict) or info.get('schemaVersion') != 1 or info.get('configKey') != 'image_generation' or not info.get('automaticModel'):
                    return None
                models = provider.list_image_models()
                if inspect.isawaitable(models): models = await models
                return info if models else None
            info = await asyncio.wait_for(available(), 10)
            if info:
                row.setdefault('config', {})['image_generation'] = {
                    **row.get('config', {}).get('image_generation', {}),
                    'enabled': True, 'id': 'images', 'model': info['automaticModel']}
                return row.get('instance_id') or row.get('id') or row['module']
        except Exception:
            # Do not expose account details or make image availability block chat.
            continue
        finally:
            if provider is not None:
                await close_provider(provider)
