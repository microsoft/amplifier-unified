"""Print a credential-free shared-action registration for an installed MCP adapter."""
import argparse
import json
from pathlib import Path


def registration(executable, *, live=False):
    executable = Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError("Choose the installed amplifier-m365-mcp executable")
    env = {"AMPLIFIER_M365_STATE_DIR": "AMPLIFIER_M365_STATE_DIR", "AMPLIFIER_M365_EXPORT_DIR": "AMPLIFIER_M365_EXPORT_DIR"}
    if live:
        env.update({key: key for key in ("AMPLIFIER_M365_LIVE_URL", "AMPLIFIER_M365_LIVE_TOKEN_FILE", "AMPLIFIER_M365_LIVE_CA_FILE")})
    return {"action": "smartTools.configure", "arguments": {"id": "microsoft365-documents", "name": "Microsoft 365 documents and Excel", "transport": "stdio", "command": str(executable), "args": [], "env": env}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    print(json.dumps(registration(args.executable, live=args.live), indent=2))


if __name__ == "__main__": main()
