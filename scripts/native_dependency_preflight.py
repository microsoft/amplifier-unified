"""Fail early when Python checks no longer use the locked native provider API.

This verifies test preparation only. It neither mounts a provider nor sends a
request, and does not change the separate shipped worker source-selection policy.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import tomllib
from urllib.parse import parse_qs, urlsplit


DISTRIBUTIONS = (
    "amplifier-core", "amplifier-module-provider-openai",
    "amplifier-module-loop-live", "amplifier-module-loop-streaming",
    "amplifier-module-tool-exec", "openai", "websockets",
)
# The workflow separately prepares loop-live/file-policy modules. They are
# observed, not falsely attested as the complete locked environment.
LOCKED = (
    "amplifier-core", "amplifier-module-provider-openai",
    "amplifier-module-tool-exec", "openai", "websockets",
)
EXPORTS = {
    "amplifier_module_provider_openai": ("OpenAIProvider",),
    "amplifier_module_provider_openai._capabilities": ("get_request_policy",),
    "amplifier_module_provider_openai.native": (
        "NativeResponsesProvider", "NativeSendAdmission",
        "NativeOutboundEnvelope", "NATIVE_REQUEST",
    ),
    "amplifier_module_loop_live.scope": ("LIVE_OWNER", "NATIVE_REQUEST"),
}


def verify_locked(name, installed, locked):
    if installed["version"] != locked["version"]:
        raise RuntimeError(f"Native dependency version differs from uv.lock: {name}")
    source = installed["source"]
    if "git" not in locked["source"]:
        if source is not None:
            raise RuntimeError(f"Native dependency is not the locked registry source: {name}")
        return
    url = urlsplit(locked["source"]["git"])
    vcs = (source or {}).get("vcs_info", {})
    expected_url = url._replace(query="", fragment="").geturl()
    requested = parse_qs(url.query).get("rev", [None])[0]
    if (not source or source.get("url") != expected_url
            or vcs.get("vcs") != "git" or vcs.get("commit_id") != url.fragment
            or vcs.get("requested_revision") != requested):
        raise RuntimeError(f"Native dependency source differs from uv.lock: {name}")


def inspect(root, record):
    lock_bytes = (root / "uv.lock").read_bytes()
    record["lockSha256"] = hashlib.sha256(lock_bytes).hexdigest()
    locked = {row["name"]: row for row in tomllib.loads(lock_bytes.decode())["package"]}
    # Capture all identities before validation/imports, so refusal retains the
    # actual resolved source (including the historical incompatible pin).
    for name in DISTRIBUTIONS:
        distribution = metadata.distribution(name)
        record["distributions"][name] = {
            "version": distribution.version,
            "source": json.loads(distribution.read_text("direct_url.json") or "null"),
        }
    for name in LOCKED:
        verify_locked(name, record["distributions"][name], locked[name])
    record["lockedSelection"] = True
    for name, exports in EXPORTS.items():
        module = importlib.import_module(name)
        for export in exports:
            getattr(module, export)  # Missing exports fail, never skip/fallback.
        path = Path(module.__file__)
        record["modules"][name] = {
            "origin": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "exports": list(exports),
        }
    record["requiredExports"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    record = {
        "schemaVersion": 1, "passed": False,
        "scope": "locked native test dependencies and required imports; not a full environment freeze",
        "python": {"executable": sys.executable, "version": sys.version},
        "distributions": {}, "modules": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        inspect(args.root, record)
        record["passed"] = True
    except Exception as error:
        record["errorType"] = type(error).__name__
        record["error"] = str(error)
        raise
    finally:
        args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()