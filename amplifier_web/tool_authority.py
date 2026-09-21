"""Keep generic tool controls from bypassing shared operation admission."""


def require_generic_tool(name, arguments):
    # Compute exposes mutations only. Bash's legacy foreground/background run
    # and passive managed-process observations remain ordinary tool controls.
    if name == "compute" or (
        name == "bash"
        and isinstance(arguments, dict)
        and arguments.get("action", "run") not in ("run", "read", "wait", "status")
    ):
        raise ValueError(
            "Use the shared computation and operation actions for managed mutations; "
            "generic tool invocation cannot bypass their ownership and admission checks."
        )
