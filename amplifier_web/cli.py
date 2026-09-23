"""Terminal launcher and deployment operations; bundled assets need no Node."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import threading
import webbrowser

from aiohttp import web
import yaml

from . import __version__
from .deployment import DEFAULT_SERVER, load_server_config, save_server_config, setting_get, setting_set, validate_server
from .host.config import app_home


def _data_dir(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else app_home()


def _connection_options(parser):
    parser.add_argument("--port", type=int)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--data-dir")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    binding = parser.add_mutually_exclusive_group()
    binding.add_argument("--bind", action="append", default=None, help="Address to bind (may be repeated)")
    binding.add_argument("--host", help="Alias for one --bind address")
    parser.add_argument("--public-origin", action="append", default=None, help="Exact public HTTP(S) origin (may be repeated)")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--session-ttl", dest="session_ttl_seconds", type=int)


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="amplifier-unified")
    _connection_options(parser)
    parser.add_argument("--version", action="version", version="amplifier-unified " + __version__)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="Run the authenticated web host")
    for name in ("run", "continue"):
        command = subcommands.add_parser(name, help="Run one task using the shared Amplifier host")
        command.add_argument("prompt", nargs="?", default="")
        command.add_argument("--resume")
        command.add_argument("--bundle", "-B")
        command.add_argument("--provider", "-p")
        command.add_argument("--model", "-m")
        command.add_argument("--max-tokens", type=int)
        command.add_argument("--output-format", choices=["text", "json", "json-trace"], default="text")
        command.add_argument("--timeout", type=int, default=3600)
    command = subcommands.add_parser("tool", help="Invoke one configured tool with normal policy checks")
    command.add_argument("name")
    command.add_argument("--args", default="{}")
    command.add_argument("--bundle", "-B")
    command.add_argument("--resume")
    command.add_argument("--timeout", type=int, default=3600)
    tui = subcommands.add_parser("tui", help="Attach the optional terminal client to an existing service")
    tui.add_argument('tui_command', nargs='?', choices=['open', 'install', 'status'], default='open')
    tui.add_argument('--setup-file', help='Install a private setup file downloaded from the service setup page')
    tui.add_argument("--server", default=os.environ.get("AMPLIFIER_UNIFIED_URL"))
    for flag in ("token-file", "ca-file", "client", "state-dir"):
        tui.add_argument("--" + flag)
    tui.add_argument("--workspace", dest="tui_workspace", help="Workspace path on the host (defaults to the launch directory)")
    selection = tui.add_mutually_exclusive_group()
    selection.add_argument("--session", "--resume", dest="session")
    selection.add_argument("--new", action="store_true")
    tui.add_argument("--list-sessions", action="store_true")
    config = subcommands.add_parser("config", help="Manage server configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("list")
    get = config_commands.add_parser("get"); get.add_argument("key")
    set_value = config_commands.add_parser("set"); set_value.add_argument("key"); set_value.add_argument("value")
    reset = config_commands.add_parser("reset"); reset.add_argument("key", nargs="?", default="all")
    service = subcommands.add_parser("service", help="Manage the Linux systemd user service")
    service.add_argument("service_command", choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"])
    service.add_argument("--replace", action="store_true", help="Back up and replace an existing generated unit (install only)")
    setup_tls = subcommands.add_parser("setup-tls", help="Create or inspect the app-owned local CA")
    setup_tls.add_argument("mode", nargs="?", choices=["status", "default", "force", "export"], default="default")
    subcommands.add_parser("doctor", help="Check deployment prerequisites and configuration")
    completion = subcommands.add_parser("completion", help="Print shell completion setup")
    completion.add_argument("shell", choices=["bash", "zsh", "fish"])
    return parser.parse_args()


def _server_overrides(args: argparse.Namespace) -> dict:
    """Translate top-level serving options without replacing nested TLS settings."""
    overrides = {
        "port": args.port,
        "public_origins": args.public_origin,
        "session_ttl_seconds": args.session_ttl_seconds,
    }
    if args.bind is not None:
        overrides["bind"] = args.bind
    elif args.host is not None:
        overrides["bind"] = [args.host]
    tls = {key: value for key, value in {
        "cert": args.tls_cert,
        "key": args.tls_key,
    }.items() if value is not None}
    if args.tls_cert is not None and args.tls_key is not None:
        tls["method"] = "ca"
    if tls:
        overrides["tls"] = tls
    return {key: value for key, value in overrides.items() if value is not None}


def _print_completion(shell: str) -> None:
    words = "serve tui run continue tool doctor config service setup-tls completion --port --workspace --data-dir --no-open --bind --host --public-origin --tls-cert --tls-key --session-ttl --version"
    if shell == "bash":
        print('complete -W "' + words + '" amplifier-unified')
    elif shell == "zsh":
        print('#compdef amplifier-unified\n_arguments "1:command:(serve tui run continue tool doctor config service setup-tls)"')
    else:
        print('complete -c amplifier-unified -f -a "serve tui run continue tool doctor config service setup-tls"')


def _config(args, data_dir: Path) -> None:
    config = load_server_config(data_dir)
    if args.config_command == "list":
        print(yaml.safe_dump(config, sort_keys=False), end="")
    elif args.config_command == "get":
        value = setting_get(config, args.key)
        if isinstance(value, (dict, list)):
            print(yaml.safe_dump(value, sort_keys=False), end="")
        else:
            print(value)
    elif args.config_command == "set":
        save_server_config(data_dir, setting_set(config, args.key, yaml.safe_load(args.value)))
    else:
        if args.key == "all":
            save_server_config(data_dir, DEFAULT_SERVER)
        else:
            save_server_config(data_dir, setting_set(config, args.key, setting_get(DEFAULT_SERVER, args.key)))


def _doctor(data_dir: Path) -> None:
    config = load_server_config(data_dir)
    try:
        import pam  # noqa: F401
        pam_state = "available"
    except ImportError:
        pam_state = "unavailable (install python-pam and the system PAM library)"
    from .tls import ca_bytes, ca_fingerprint, tls_ready
    print(f"Amplifier Unified {__version__}")
    print(f"Configuration: {data_dir / 'config' / 'server.yaml'}")
    print("PAM:", pam_state)
    print("TLS local CA:", "configured" if ca_bytes(data_dir) else "not configured")
    print("TLS listener:", "ready" if tls_ready(data_dir, config) else "not configured or not startable")
    print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir) or "not configured")
    print("Binds:", ", ".join(config["bind"]))
    print("Port:", config["port"])
    from .shared_state import shared_state_home
    print("Shared session state:", shared_state_home())
    print("Standalone hosts must share the state root and canonical workspace. Connected clients use the service API.")


def _setup_tls(data_dir: Path, mode: str) -> None:
    from .tls import ca_bytes, ca_fingerprint, exported_ca_bytes, setup_local_ca, tls_ready
    if mode == "export":
        import sys
        sys.stdout.buffer.write(exported_ca_bytes(data_dir))
        return
    config = load_server_config(data_dir)
    if mode == "status":
        print("Local CA:", "configured" if ca_bytes(data_dir) else "not configured")
        print("TLS:", config["tls"]["method"])
        print("TLS listener:", "ready" if tls_ready(data_dir, config) else "not configured or not startable")
        print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir) or "not configured")
        return
    setup_local_ca(data_dir, config, force=mode == "force")
    print("Created app-owned local CA and leaf certificate. Restart the service to use HTTPS.")
    print("CA SHA-256 fingerprint:", ca_fingerprint(data_dir))


def _serve(args, data_dir: Path) -> None:
    overrides = _server_overrides(args)
    config = load_server_config(data_dir, overrides=overrides)
    os.environ["AMPLIFIER_WEB_HOME"] = str(data_dir)
    from .settings_migration import migrate_settings
    migrate_settings(data_dir, [args.workspace])
    from .host.config import load_config
    load_config(args.workspace)
    from .server import create_app
    from .tls import ssl_context
    secure = ssl_context(data_dir, config)
    scheme = "https" if secure else "http"
    url = (config["public_origins"][0] if config["public_origins"] else
           f"{scheme}://127.0.0.1:{config['port']}")
    print(f"Amplifier Unified · {url}\nWorkspace: {args.workspace}\nState: {data_dir}")
    if not args.no_open:
        timer = threading.Timer(1.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    from .mcp_oauth import SafeAccessLogger
    web.run_app(create_app(data_dir, workspace=args.workspace, server_config=config),
                host=config["bind"], port=config["port"], ssl_context=secure, print=None, access_log_class=SafeAccessLogger, access_log_format='%a %t "%r" %s %b')


def _tui(args, data_dir):
    from .terminal_cli import install, saved
    if args.tui_command == 'install':
        return install(args, data_dir)
    if args.setup_file:
        raise ValueError('--setup-file is only valid with tui install.')
    managed = saved()
    if args.tui_command == 'status':
        print('Terminal connected to ' + managed['server'] if managed else 'No managed terminal installation. Run amplifier-unified tui install.')
        return
    # Connected Terminal uses this scope for Resume, latest and paged listings.
    args.tui_workspace = args.tui_workspace or str(Path.cwd().resolve())
    if managed:
        import subprocess
        options = []
        if not args.server:
            args.server = managed['server']
            args.token_file = args.token_file or managed['tokenFile']
            args.ca_file = args.ca_file or managed.get('caFile')
        for key in ('server', 'token_file', 'ca_file', 'client', 'state_dir', 'session'):
            if getattr(args, key):
                options += ['--' + key.replace('_', '-'), str(getattr(args, key))]
        if args.tui_workspace:
            options += ['--workspace', args.tui_workspace]
        for key in ('new', 'list_sessions'):
            if getattr(args, key):
                options += ['--' + key.replace('_', '-')]
        print('Connecting to ' + args.server, flush=True)
        raise SystemExit(subprocess.call([str(Path(managed['environment']) / 'bin/python'), '-m', 'amplifier_tui.connected', *options]))
    try:
        from amplifier_tui.connected import main as launch
    except ModuleNotFoundError as exc:
        if exc.name not in {"amplifier_tui", "amplifier_tui.connected"}:
            raise
        raise SystemExit("Terminal client is optional. Run amplifier-unified tui install, or open /setup/terminal on your Unified service.") from None
    options = []
    server = args.server
    if not server:
        config = load_server_config(data_dir)
        secure = config["tls"]["method"] != "none"
        server = f"{'https' if secure else 'http'}://127.0.0.1:{config['port']}"
        if not args.token_file and not os.environ.get("AMPLIFIER_UNIFIED_TOKEN"):
            options += ["--token-file", str(data_dir / "config/auth/control-token")]
        ca = data_dir / "config/tls/ca.crt"
        if secure and not args.ca_file and ca.is_file():
            options += ["--ca-file", str(ca)]
    options += ["--server", server]
    for key in ("token_file", "ca_file", "client", "state_dir", "session"):
        if getattr(args, key):
            options += ["--" + key.replace("_", "-"), str(getattr(args, key))]
    if args.tui_workspace:
        options += ["--workspace", args.tui_workspace]
    for key in ("new", "list_sessions"):
        if getattr(args, key):
            options += ["--" + key.replace("_", "-")]
    return launch(options)


def main():
    args = _parse()
    data_dir = _data_dir(args.data_dir)
    if args.command == "tui":
        try:
            return _tui(args, data_dir)
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from None
    if args.command == "completion":
        _print_completion(args.shell)
        return
    if args.command == "config":
        _config(args, data_dir)
        return
    if args.command == "doctor":
        _doctor(data_dir)
        return
    if args.command == "setup-tls":
        _setup_tls(data_dir, args.mode)
        return
    if args.command == "service":
        from . import deployment_service
        if args.replace and args.service_command != "install":
            raise ValueError("--replace is only valid with service install.")
        if args.service_command == "install":
            deployment_service.install(data_dir, args.workspace, replace=args.replace)
        elif args.service_command == "uninstall":
            deployment_service.uninstall()
        else:
            deployment_service.command(args.service_command)
        return
    config = load_server_config(data_dir, overrides=_server_overrides(args))
    args.port, args.data_dir = config["port"], str(data_dir)
    if args.command in {"run", "continue", "tool"}:
        os.environ["AMPLIFIER_WEB_HOME"] = str(data_dir)
        from .settings_migration import migrate_settings
        migrate_settings(data_dir, [args.workspace])
        from .host.config import load_config
        load_config(args.workspace)
        from .headless import run
        try:
            raise SystemExit(asyncio.run(run(args, config=config)))
        except (RuntimeError, ValueError, TimeoutError) as exc:
            import sys
            print(str(exc) or "The task exceeded its wait timeout.", file=sys.stderr)
            raise SystemExit(1)
    _serve(args, data_dir)


if __name__ == "__main__":
    main()
