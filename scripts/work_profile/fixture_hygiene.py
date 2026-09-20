"""Credential cleanup restricted to a completed, newly generated fixture tree."""

import os
import re
from pathlib import Path


def redact_generated_credentials(folder, provider):
    """Call only after fixture workers stop; retain SQLite bytes for audit."""
    secrets = set()

    def collect(value, key=""):
        if isinstance(value, dict):
            for name, item in value.items():
                collect(item, name)
        elif isinstance(value, list):
            for item in value:
                collect(item, key)
        elif (
            isinstance(value, str)
            and len(value) > 12
            and re.search(
                r"api.?key|secret|password|authorization|access.?token|refresh.?token",
                key,
                re.IGNORECASE,
            )
            and "[REDACTED]" not in value
        ):
            secrets.add(value.encode())

    collect(provider)
    skip = {
        "cache",
        "runtime",
        "foundation",
        "node_modules",
        ".git",
        ".venv",
        "__pycache__",
        "shell-packages",
    }
    scanned = redacted = remaining = sqlite_matches = 0
    for directory, directories, filenames in os.walk(Path(folder), followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in skip and not (Path(directory) / name).is_symlink()
        ]
        for name in filenames:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            sqlite = ".sqlite3" in name
            if not sqlite and path.suffix not in {
                ".json",
                ".jsonl",
                ".yaml",
                ".yml",
                ".txt",
                ".log",
                ".html",
                ".py",
                ".md",
            }:
                continue
            scanned += 1
            data = path.read_bytes()
            if not any(secret in data for secret in secrets):
                continue
            if sqlite:
                sqlite_matches += 1
            else:
                for secret in secrets:
                    data = data.replace(secret, b"[REDACTED]")
                path.write_bytes(data)
                redacted += 1
            remaining += any(secret in data for secret in secrets)
    return {
        "secretValuesChecked": len(secrets),
        "scannedFiles": scanned,
        "redactedFiles": redacted,
        "remainingMatches": remaining,
        "sqliteMatches": sqlite_matches,
        "scope": "Generated fixture text/config only; SQLite scanned without edits; dependency caches and symlinks excluded.",
    }
