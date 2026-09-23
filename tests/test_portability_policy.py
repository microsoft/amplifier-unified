"""Policy hashes retain signed bytes without importing host-only packages."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web.portability_policy import readiness_policy
from amplifier_worktrees.git import digest


@pytest.mark.parametrize('value', [
    readiness_policy('provider-openai', 'terra', 'selected-model', 'high'),
    readiness_policy('provider-openai', 'térra', 'selected-model', 'low'),
    {'z': [True, False, None, 1024, 1024.0], 'a': {'text': 'é\n"'}},
    {},
])
def test_policy_digest_matches_existing_host_protocol(value):
    from amplifier_web.portability_policy import policy_digest
    assert policy_digest(value) == digest(value)


def test_actual_probe_entrypoint_needs_only_app_package_and_runtime_dependencies(tmp_path):
    # Editable test installs can otherwise expose sibling host packages even
    # under -I. Remove the source root and deny those imports, matching the
    # managed runtime's actual app-only bootstrap boundary.
    script = Path(__file__).resolve().parents[1] / 'amplifier_web' / 'portability_probe.py'
    driver = '''
import importlib.abc, pathlib, runpy, sys
script = pathlib.Path(sys.argv[1])
sys.path = [entry for entry in sys.path if pathlib.Path(entry).resolve() != script.parent.parent]
sys.path.insert(0, str(script.parent))
class IsolatedRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'amplifier_worktrees', 'amplifier_portability', 'openai'} or fullname.startswith('amplifier_module_provider'):
            raise ModuleNotFoundError('Host siblings and providers are outside this invalid-request check', name=fullname)
sys.meta_path.insert(0, IsolatedRuntime())
def deny_dispatch(event, args):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.exec'}:
        raise AssertionError('Invalid probe input cannot dispatch work')
sys.addaudithook(deny_dispatch)
sys.argv = [str(script)]
runpy.run_path(str(script), run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', driver, str(script)],
        input='{}', cwd=tmp_path, env={'PATH': os.defpath, 'TMPDIR': str(tmp_path)},
        text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    import json
    assert json.loads(result.stdout) == {
        'error': 'Destination execution probe did not verify readiness.', 'code': 'invalid_request'}
