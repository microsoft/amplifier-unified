"""Settings browser fixtures finish application cleanup on POSIX shutdown."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
from urllib.parse import urlparse

import pytest


@pytest.mark.skipif(os.name != 'posix', reason='Graceful SIGTERM/SIGINT is a POSIX fixture contract')
@pytest.mark.parametrize('script,prefix', [('settings_ui_server.py', 'amplifier-settings-ui-'), ('storage_history_ui_server.py', 'unified-storage-history-')])
@pytest.mark.parametrize('shutdown_signal', [signal.SIGTERM, signal.SIGINT])
async def test_settings_fixture_shutdown_removes_home_and_listener(tmp_path, shutdown_signal, script, prefix):
    fixture = Path(__file__).parent / 'fixtures' / script
    stderr = tmp_path / 'stderr.log'
    with stderr.open('wb') as errors:
        child = await asyncio.create_subprocess_exec(
            sys.executable, '-u', str(fixture), stdout=asyncio.subprocess.PIPE,
            stderr=errors, env={**os.environ, 'TMPDIR': str(tmp_path)})
        try:
            async def ready():
                while line := await child.stdout.readline():
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if value.get('fixture') == 'settings-ui' or value.get('url'):
                        return value
                raise AssertionError(stderr.read_text())
            receipt = await asyncio.wait_for(ready(), 30)
            if 'pid' in receipt:
                assert receipt['pid'] == child.pid
            homes = list(tmp_path.glob(prefix+'*'))
            assert len(homes) == 1 and (homes[0] / 'workspace').is_dir()
            child.send_signal(shutdown_signal)
            assert await asyncio.wait_for(child.wait(), 10) == 0, stderr.read_text()
            assert not homes[0].exists(), 'TemporaryDirectory must unwind after AppRunner.cleanup'
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(('127.0.0.1', urlparse(receipt.get('base') or receipt['url']).port)) != 0
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
