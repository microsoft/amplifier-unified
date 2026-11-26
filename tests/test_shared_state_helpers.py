"""Bootstrap path helpers must not load the native runtime dependency graph."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_shared_state_helpers_import_without_foundation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = f"""
import builtins
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(root)!r})
original_import = builtins.__import__
def without_foundation(name, *args, **kwargs):
    if name == 'amplifier_foundation' or name.startswith('amplifier_foundation.'):
        raise AssertionError('Bootstrap helpers imported the native runtime')
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_foundation
from amplifier_web.shared_state import shared_state_home, configuration_paths, configuration_stamp
os.environ['AMPLIFIER_SESSION_STATE_HOME'] = {str(tmp_path / 'private-state')!r}
assert shared_state_home() == Path({str(tmp_path / 'private-state')!r})
assert callable(configuration_paths) and callable(configuration_stamp)
assert not any(name == 'amplifier_foundation' or name.startswith('amplifier_foundation.') for name in sys.modules)
"""
    subprocess.run([sys.executable, '-I', '-S', '-c', script], check=True,
                   capture_output=True, text=True, timeout=10)


def test_configuration_path_digest_is_bounded_and_regular(tmp_path):
    from amplifier_web.shared_state import (
        MAX_CONFIGURATION_DIGEST_BYTES, configuration_path_digest)
    path = tmp_path / 'settings.yaml'
    path.write_bytes(b'allowed: true\n')
    assert configuration_path_digest(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert configuration_path_digest(tmp_path / 'missing.yaml') is None
    with path.open('wb') as stream:
        stream.truncate(MAX_CONFIGURATION_DIGEST_BYTES + 1)
    with pytest.raises(RuntimeError, match='too large'):
        configuration_path_digest(path)
    with pytest.raises(RuntimeError, match='regular file|read safely'):
        configuration_path_digest(tmp_path)


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='FIFO unavailable')
def test_configuration_path_digest_refuses_fifo_without_blocking(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fifo = tmp_path / 'settings.yaml'
    os.mkfifo(fifo)
    script = f"""
import sys
sys.path.insert(0, {str(root)!r})
from amplifier_web.shared_state import configuration_path_digest
try:
    configuration_path_digest({str(fifo)!r})
except RuntimeError:
    print('refused')
"""
    result = subprocess.run(
        [sys.executable, '-I', '-S', '-c', script],
        check=True, capture_output=True, text=True, timeout=2)
    assert result.stdout.strip() == 'refused'
