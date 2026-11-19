"""The update service keeps its limits while its uv child resolves Git sources."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from amplifier_web import updates


@pytest.mark.skipif(os.name != 'posix', reason='macOS updater uses POSIX process limits')
@pytest.mark.parametrize(('parent_soft', 'hard', 'expected'), [
    (256, 8192, 4096),
    (256, 768, 768),
    (4608, 8192, 4608),
])
def test_uv_child_limits_and_arguments(tmp_path, parent_soft, hard, expected):
    import resource

    actual_hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if actual_hard != resource.RLIM_INFINITY and hard > actual_hard:
        pytest.skip('runner cannot supply the test hard limit')
    # Run the real updater in a disposable parent so the CI worker's hard
    # limit is never altered. The uv stand-in reports its inherited state.
    tool = tmp_path / 'uv'
    tool.symlink_to(sys.executable)
    script = """
import asyncio, json, os, resource, sys
from types import SimpleNamespace
from amplifier_web import updates
soft, hard = int(sys.argv[1]), int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
updates.sys = SimpleNamespace(platform='darwin', executable=sys.executable)
async def run():
    report = ('import json, os, resource, sys; '
              'print(json.dumps([resource.getrlimit(resource.RLIMIT_NOFILE), '
              'sys.argv[1:], os.getcwd(), os.environ["QUALIFICATION"]]))')
    output = await updates.process(sys.argv[3], '-c', report, 'lock', 'with spaces', cwd=sys.argv[4],
                                   env=dict(os.environ, QUALIFICATION='present'))
    print(json.dumps([json.loads(output), resource.getrlimit(resource.RLIMIT_NOFILE)]))
asyncio.run(run())
"""
    output = subprocess.check_output(
        [sys.executable, '-c', script, str(parent_soft), str(hard), str(tool), str(tmp_path)],
        cwd=Path(updates.__file__).resolve().parents[1], text=True)
    child, parent = json.loads(output)
    assert child == [[expected, hard], ['lock', 'with spaces'], str(tmp_path.resolve()), 'present']
    assert parent == [parent_soft, hard]
