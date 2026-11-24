"""Compose native initialization hooks; session lifecycle stays with its caller."""


def initialization_hooks(*, isolation, decision_accounts, routing, retain, ephemeral_servers=None, providers=(),
                         media=None, hooks=()):
    """Use the native declarations that this session will load."""
    from ..provider_state_files import uses_state_files
    from .hook_config import MODULE, reference_name, destinations_in
    bound_hooks = any(reference_name(destination.get("api_key"))
                      for row in hooks if row.get("module") == MODULE
                      for _, destination in destinations_in(row.get("config")))
    state_files = uses_state_files(providers)
    if not (isolation or decision_accounts.records or ephemeral_servers is not None
            or state_files or bound_hooks or media is not None):
        return {"before_initialize": routing} if routing else {}

    async def before_initialize(constructing):
        # create_session can fail after constructing a session. Give the caller
        # cleanup ownership before any of its initialization policies can fail.
        retain(constructing)
        if state_files or bound_hooks:
            # Media registration observes the declared plan, outside the load boundary.
            _install_module_config(constructing.coordinator, bound_hooks)
        if media is not None:
            media(constructing)
        if ephemeral_servers is not None:
            constructing.coordinator.register_capability("mcp.ephemeral_servers", ephemeral_servers)
        if isolation:
            await isolation.before_initialize(constructing)
        if decision_accounts.records:
            await decision_accounts.before_initialize(constructing)
        if routing:
            await routing(constructing)

    return {"before_initialize": before_initialize}


def _install_module_config(coordinator, bound_hooks):
    """Materialize host-local configuration only for the actual module load.

    Core loads every module through ``coordinator.loader`` and makes its own
    loader only when it finds none. Call this before the session initializes.
    The session's configuration stays as it is.
    """
    from ..provider_state_files import with_state_files
    from .hook_config import MODULE, mounted_config
    loader = getattr(coordinator, 'loader', None)
    if loader is None:
        from amplifier_core.loader import ModuleLoader
        loader = ModuleLoader(coordinator=coordinator)  # The loader that Core makes itself.
        coordinator.loader = loader
    load = loader.load

    async def load_with_config(module_id, config=None, *arguments, **options):
        if bound_hooks and module_id == MODULE:
            config = mounted_config(config)
        return await load(module_id, with_state_files(module_id, config), *arguments, **options)

    loader.load = load_with_config
