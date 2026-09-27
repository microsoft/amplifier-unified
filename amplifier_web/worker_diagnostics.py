"""Bounded, redacted startup receipts. Their contents never enter public state."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import traceback
import uuid

MAX_DETAIL_BYTES = 60_000
_SECRET_NAME = re.compile(r'(?i)(api.?key|password|secret|authorization|cookie|credential|token)')
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(["']?(?:api[_-]?key|password|client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|cookie)["']?\s*[:=]\s*)
    (?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
_RECEIPT = re.compile(r'^startup-[a-f0-9]{32}\.log$')


def redact_diagnostic(value):
    """Best-effort secret removal, including secrets inherited by this process.

    Receipts remain private because arbitrary module exception text can contain
    sensitive context beyond recognizable credentials. Never publish this text.
    """
    text = str(value)
    for name, secret in os.environ.items():
        if _SECRET_NAME.search(name) and len(secret) >= 4:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    text = _SECRET_ASSIGNMENT.sub(r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED]', text)
    return text


def _bounded(text, limit=MAX_DETAIL_BYTES):
    return text.encode('utf-8', errors='replace')[-limit:].decode('utf-8', errors='ignore') if limit else ''


def _home():
    return Path(os.environ.get('AMPLIFIER_WEB_HOME', Path.home() / '.amplifier-unified'))


def receipt_path(name, *, home=None):
    """Resolve only a receipt created in this instance, never a worker-sent path."""
    if not isinstance(name, str) or not _RECEIPT.fullmatch(name):
        return None
    path = (Path(home) if home is not None else _home()) / 'logs' / 'workers' / name
    try:
        return path if path.is_file() and not path.is_symlink() else None
    except OSError:
        return None


def diagnostic_reference(error):
    name = getattr(getattr(error, "diagnostic_path", None), "name", None)
    return {"diagnosticReceipt": name} if isinstance(name, str) and _RECEIPT.fullmatch(name) else {}


def save_startup_failure(row, exit_code, *, failure=None):
    """Save stderr, or a metadata-only receipt for a handled startup failure."""
    try:
        from .deployment import write_private
        stderr = _bounded(redact_diagnostic(''.join(row.get('stderr', []))))
        if not stderr and failure is None:
            return None
        record = {'time': datetime.now(timezone.utc).isoformat(), 'exitCode': exit_code,
                  'phase': redact_diagnostic(row.get('phase', 'runtime-setup'))[:100]}
        identity = row.get('runtime_id')
        if isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', identity):
            record['sessionId'] = identity
        if failure is not None:
            record['failure'] = _bounded(redact_diagnostic(failure), 8_000)
        # Bound the complete file, including JSON escaping of arbitrary errors.
        header = json.dumps(record)
        stderr = _bounded(stderr, max(0, MAX_DETAIL_BYTES - len(header.encode('utf-8')) - 2))
        path = _home() / 'logs' / 'workers' / f'startup-{uuid.uuid4().hex}.log'
        write_private(path, header + '\n\n' + stderr)
        return path
    except Exception:
        return None  # Diagnostics must never replace the original startup error.


class StartupCapture(logging.Handler):
    """Capture the original Core validation error before safe metadata replaces it.

    No prompt/config dumps or frame locals are collected. Core's loader error
    includes the failed contract check and underlying exception. If INFO logging
    is already enabled, mount source lines are retained too. This handler never
    changes application log levels and is removed once startup finishes.
    """
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []
        self._loggers = [logging.getLogger(name) for name in ('amplifier_core', 'amplifier_foundation')]
        for logger in self._loggers:
            logger.addHandler(self)

    def emit(self, record):
        try:
            message = record.getMessage()
            if record.levelno < logging.WARNING and '[module:mount]' not in message:
                return
            self.lines.append(_bounded(redact_diagnostic(f'{record.name}: {message}\n')))
            self.lines = [_bounded(''.join(self.lines))]
        except Exception:
            pass

    def save(self, exc, session_id):
        try:
            # Traceback locations only: source lines and locals can contain secrets.
            locations = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                         for frame in traceback.extract_tb(exc.__traceback__)[-20:]]
            detail = json.dumps({'errorType': type(exc).__name__,
                                 'message': redact_diagnostic(exc), 'frames': locations})
            return save_startup_failure({'stderr': self.lines, 'runtime_id': session_id,
                                         'phase': 'session-mount'}, None, failure=detail)
        except Exception:
            return None

    def close(self):
        for logger in self._loggers:
            logger.removeHandler(self)
        super().close()
