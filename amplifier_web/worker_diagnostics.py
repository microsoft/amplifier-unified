"""Private, bounded startup stderr. Never include worker output in public state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


def save_startup_failure(row, exit_code):
    from .deployment import write_private
    stderr = ''.join(row.get('stderr', []))[-60_000:]
    if not stderr:
        return None
    home = Path(os.environ.get('AMPLIFIER_WEB_HOME', Path.home() / '.amplifier-unified'))
    path = home / 'logs' / 'workers' / f'startup-{uuid.uuid4().hex}.log'
    record = {'time': datetime.now(timezone.utc).isoformat(), 'exitCode': exit_code,
              'phase': row.get('phase', 'runtime-setup')}
    try:
        write_private(path, json.dumps(record) + '\n\n' + stderr)
    except OSError:
        return None  # A full/read-only disk must not hide the original failure.
    return path
