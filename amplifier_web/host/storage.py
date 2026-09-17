"""App-owned, atomic session checkpoints and read-only legacy import."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import time

_SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "secret", "password", "authorization", "client_secret"}


def _metadata(value):
    """Persist routing/config, never literal credential fields."""
    if isinstance(value, dict):
        return {key: _metadata(item) for key, item in value.items() if key.lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_metadata(item) for item in value]
    return value


class SessionStore:
    def __init__(self, base_dir=None, *, legacy_home=None):
        self.base_dir = Path(base_dir or Path.home() / ".amplifier-unified" / "sessions").expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.legacy_home = Path(legacy_home or os.environ.get("AMPLIFIER_UNIFIED_IMPORT_HOME", Path.home() / ".amplifier")).expanduser()

    def directory(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id):
            raise ValueError("Invalid session identifier")
        path = self.base_dir / session_id
        if path.is_symlink():
            raise ValueError("Session directories cannot be symbolic links")
        return path

    @staticmethod
    def _atomic(path, text):
        fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, session_id, messages, metadata, *, preserve_system=False):
        directory = self.directory(session_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        rows = [copy.deepcopy(m if isinstance(m, dict) else m.model_dump()) for m in messages]
        # Regenerate system instructions from the bundle on resume.
        if not preserve_system and not (metadata or {}).get("preserve_system"):
            rows = [m for m in rows if m.get("role") not in {"system", "developer"}]
        saved_metadata = _metadata(copy.deepcopy(metadata or {}))
        saved_metadata.update({"session_id": session_id, "updated_at": time.time(), "host": "amplifier-unified"})
        payload = {"version": 1, "messages": rows, "metadata": saved_metadata}
        # One atomic file is authoritative: transcript/metadata exports cannot create
        # a half-old/half-new checkpoint if the process exits between writes.
        self._atomic(directory / "checkpoint.json", json.dumps(payload, ensure_ascii=False, default=str))
        self._atomic(directory / "transcript.jsonl", "".join(json.dumps(m, ensure_ascii=False, default=str) + "\n" for m in rows))
        self._atomic(directory / "metadata.json", json.dumps(saved_metadata, ensure_ascii=False, indent=2, default=str))

    def load(self, session_id):
        path = self.directory(session_id) / "checkpoint.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("messages"), list) or not isinstance(payload.get("metadata"), dict):
            raise ValueError("Unsupported or corrupted session checkpoint")
        return payload["messages"], payload["metadata"]

    def import_cli(self, session_id, workspace=None):
        """Copy one selected legacy transcript. Never execute or mutate source records."""
        existing = self.load(session_id)
        if existing is not None:
            return existing
        self.directory(session_id)  # Validate before interpolating a glob.
        candidates = []
        if workspace:
            slug = re.sub(r"[^a-zA-Z0-9]", "-", str(Path(workspace).expanduser().resolve()))
            candidates.append(self.legacy_home / "projects" / slug / "sessions" / session_id)
        candidates.append(self.legacy_home / "sessions" / session_id)
        projects = self.legacy_home / "projects"
        if projects.is_dir():
            candidates.extend(projects.glob(f"*/sessions/{session_id}"))
        seen = set()
        for source in candidates:
            if source in seen:
                continue
            seen.add(source)
            transcript = source / "transcript.jsonl"
            if not transcript.is_file():
                continue
            rows = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError("Legacy transcript contains a malformed message")
            metadata_path = source / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            # Checkpoints are conversation evidence only. Pending/unfinished tool
            # calls remain historical; loop-live receives no imported job tasks.
            metadata["legacy_import"] = {"source": str(source), "imported_at": time.time(), "read_only_source": True, "jobs_replayed": False}
            metadata["interrupted_jobs"] = True
            # Carry evidence, not process ownership. The host's JobStore.recover
            # marks any pending dispatch interrupted before mounting execution.
            destination = self.directory(session_id) / "live-jobs"
            for original in (source / "live-jobs").glob("job-*.json"):
                value = json.loads(original.read_text(encoding="utf-8"))
                if value.get("version") != 1 or not isinstance(value.get("call_id"), str):
                    raise ValueError("Legacy job ledger contains an invalid record")
                destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._atomic(destination / original.name, json.dumps(value, ensure_ascii=False))
            self.save(session_id, rows, metadata)
            return self.load(session_id)
        return None
