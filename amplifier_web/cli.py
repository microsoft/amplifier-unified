"""Terminal launcher; shipped frontend assets require no Node install."""
import argparse
import asyncio
import os
from pathlib import Path
import threading
import webbrowser

from aiohttp import web


def main():
    parser = argparse.ArgumentParser(prog="amplifier-unified")
    parser.add_argument("--port", type=int, default=8941)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--data-dir", default=os.environ.get("AMPLIFIER_WEB_DATA_DIR", str(Path.home() / ".amplifier-unified")))
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    from . import __version__
    parser.add_argument("--version", action="version", version="amplifier-unified " + __version__)
    subcommands=parser.add_subparsers(dest="command")
    for name in ("run","continue"):
        command=subcommands.add_parser(name,help="Run one task using the shared Amplifier host")
        command.add_argument("prompt",nargs="?",default="")
        command.add_argument("--resume")
        command.add_argument("--bundle","-B")
        command.add_argument("--provider","-p")
        command.add_argument("--model","-m")
        command.add_argument("--max-tokens",type=int)
        command.add_argument("--output-format",choices=["text","json","json-trace"],default="text")
        command.add_argument("--timeout",type=int,default=3600)
    command=subcommands.add_parser("tool",help="Invoke one configured tool with normal policy checks")
    command.add_argument("name")
    command.add_argument("--args",default="{}")
    command.add_argument("--bundle","-B")
    command.add_argument("--resume")
    command=subcommands.add_parser("completion",help="Print shell completion setup")
    command.add_argument("shell",choices=["bash","zsh","fish"])
    args = parser.parse_args()
    if args.command=='completion':
        words='run continue tool --port --workspace --data-dir --no-open --version'
        if args.shell=='bash':print('complete -W "'+words+'" amplifier-unified')
        elif args.shell=='zsh':print('#compdef amplifier-unified\n_arguments "1:command:(run continue tool)" "--port[Port]:port:" "--workspace[Workspace]:directory:_files -/" "--data-dir[Data directory]:directory:_files -/"')
        else:print('complete -c amplifier-unified -f -a "run continue tool"')
        return
    os.environ["AMPLIFIER_WEB_PORT"] = str(args.port)
    os.environ["AMPLIFIER_WEB_HOME"] = str(Path(args.data_dir).expanduser())
    from .host.config import load_config
    load_config(args.workspace)
    if args.command:
        from .headless import run
        try:
            raise SystemExit(asyncio.run(run(args)))
        except (RuntimeError,ValueError,TimeoutError) as exc:
            import sys
            print(str(exc) or "The task exceeded its wait timeout.",file=sys.stderr)
            raise SystemExit(1)
    from .server import create_app
    url = f"http://127.0.0.1:{args.port}"
    print(f"Amplifier web · {url}\nWorkspace: {args.workspace}\nState: {args.data_dir}")
    if not args.no_open:
        timer = threading.Timer(1.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    web.run_app(create_app(Path(args.data_dir), workspace=args.workspace), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
