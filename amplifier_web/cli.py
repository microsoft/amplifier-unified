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
    setup_tls.add_argument("mode", nargs="?", choices=["status", "default", "force"], default="default")
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
    words = "serve run continue tool doctor config service setup-tls completion --port --workspace --data-dir --no-open --bind --host --public-origin --tls-cert --tls-key --session-ttl --version"
    if shell == "bash":
        print('complete -W "' + words + '" amplifier-unified')
    elif shell == "zsh":
        print('#compdef amplifier-unified\n_arguments "1:command:(serve run continue tool doctor config service setup-tls)"')
    else:
        print('complete -c amplifier-unified -f -a "serve run continue tool doctor config service setup-tls"')


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


def _setup_tls(data_dir: Path, mode: str) -> None:
    config = load_server_config(data_dir)
    from .tls import ca_bytes, ca_fingerprint, setup_local_ca, tls_ready
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
    web.run_app(create_app(data_dir, workspace=args.workspace, server_config=config),
                host=config["bind"], port=config["port"], ssl_context=secure, print=None)


def main():
    args = _parse()
    data_dir = _data_dir(args.data_dir)
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