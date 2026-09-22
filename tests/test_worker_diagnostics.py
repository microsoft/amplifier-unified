import asyncio
import json
import sys

import pytest

from amplifier_web.runtime import RuntimeManager
from amplifier_web.worker_diagnostics import save_startup_failure


@pytest.mark.asyncio
async def test_startup_stderr_survives_cleanup_without_becoming_public(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    fixture = "import sys; sys.stdin.readline(); print('Dependency setup failed: secret-fixture-value', file=sys.stderr, flush=True); raise SystemExit(1)"
    manager = RuntimeManager(command=[sys.executable, '-c', fixture])
    try:
        with pytest.raises(RuntimeError, match='Startup details were saved locally') as error:
            await manager.start({'id': 'probe'}, emit)
        assert not manager.workers
        files = list((tmp_path / 'logs/workers').glob('startup-*.log'))
        assert len(files) == 1
        assert 'Dependency setup failed' in files[0].read_text()
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert files[0].parent.stat().st_mode & 0o777 == 0o700
        assert str(files[0]) in str(error.value)
        assert 'secret-fixture-value' not in json.dumps(events)
        assert 'secret-fixture-value' not in str(error.value)
        assert not any(kind == 'assistant.message' for kind, _ in events)
    finally:
        await manager.close()


def test_diagnostic_is_bounded_and_optional(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    assert save_startup_failure({'stderr': []}, 1) is None
    path = save_startup_failure({'stderr': ['x' * 100_000]}, 1)
    assert path.stat().st_size < 61_000
    def fail(*_):
        raise OSError('disk unavailable')
    monkeypatch.setattr('amplifier_web.deployment.write_private', fail)
    assert save_startup_failure({'stderr': ['failed']}, 1) is None
