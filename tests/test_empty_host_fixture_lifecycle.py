"""The browser fixture must finish real application cleanup on SIGTERM."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize('mode', [None, '--first-use', '--startup-failure'])
async def test_sigterm_closes_fixture_and_removes_owned_temporary_home(tmp_path, mode):
    fixture = Path(__file__).parent / 'fixtures/empty_host_ui_server.py'
    stderr = tmp_path / 'stderr.log'
    with stderr.open('wb') as error_log:
        child = await asyncio.create_subprocess_exec(sys.executable, str(fixture),
            *([mode] if mode else []), stdout=asyncio.subprocess.PIPE, stderr=error_log,
            env={**os.environ, 'TMPDIR': str(tmp_path)})
        try:
            async def ready():
                while line := await child.stdout.readline():
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if value.get('url'):
                        return value
                raise AssertionError(stderr.read_text())
            await asyncio.wait_for(ready(), 20)
            homes = list(tmp_path.glob('amplifier-empty-host-*'))
            assert len(homes) == 1 and (homes[0] / 'app').is_dir()
            child.terminate()
            code = await asyncio.wait_for(child.wait(), 15)
            assert code == 0, stderr.read_text()
            assert not homes[0].exists(), 'TemporaryDirectory must unwind after AppRunner.cleanup'
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
