"""One bounded utility request with explicit model and mounted-account identity."""
import logging
import inspect

log = logging.getLogger('amplifier_module_hooks_session_naming')


def role_provider(providers, name):
    if name in providers:
        return name, providers[name]
    # The public resolver can name a provider family, but not choose silently
    # between two mounted accounts of that family.
    matches = [(key, value) for key, value in providers.items()
               if isinstance(name, str) and name and name.lower() in key.lower()]
    if len(matches) != 1:
        raise ValueError('Naming role must identify one mounted provider instance; choose an unambiguous naming connection.')
    return matches[0]


async def complete(hook, prompt):
    from amplifier_core import ChatRequest, Message
    providers = hook.coordinator.get('providers') or {}
    name, provider = hook._select_session_provider(providers)
    if provider is None:
        raise ValueError('The selected naming provider is unavailable.')
    model = getattr(hook, 'selected_model', None)
    if hook.config.model_role:
        resolver = hook.coordinator.get_capability('model_role_resolver')
        if resolver is None and getattr(hook, 'explicit_role', False):
            raise ValueError('The explicit naming role needs a model-role resolver; no default connection was substituted.')
        if resolver is not None:
            resolved = await resolver.resolve(hook.config.model_role)
            if not resolved:
                raise ValueError('The configured naming role has no available candidate; no other connection was used.')
            name, provider = role_provider(providers, resolved[0].provider)
            model = resolved[0].model
            if not isinstance(model, str) or not model.strip():
                raise ValueError('The configured naming role did not select a model.')
    stamped = hook._stamped_provider(provider)
    if stamped is None:
        raise ValueError('Naming cannot attribute the selected provider request; no call was sent.')
    info = provider.get_info() if hasattr(provider, 'get_info') else None
    if inspect.isawaitable(info):
        info = await info
    defaults = getattr(info, 'defaults', {}) or {}
    config = getattr(provider, 'config', {}) or {}
    if defaults.get('auth_mode') == 'chatgpt_plan' or config.get('auth_mode') == 'chatgpt_plan':
        raise ValueError('This connection cannot enforce the bounded naming output limit; the current name was kept.')
    request = ChatRequest(messages=[Message(role='user', content=prompt)], model=model,
                          metadata={'stream': False}, max_output_tokens=256)
    # Deliberately do not copy the foreground output or reasoning selection.
    reply = await stamped.complete(request, extended_thinking=False)
    text = ''.join(getattr(block, 'text', '') for block in getattr(reply, 'content', None) or []
                   if isinstance(getattr(block, 'text', None), str))
    if not text:
        log.warning('The naming provider returned no text; the current name was kept.')
    return text or None
