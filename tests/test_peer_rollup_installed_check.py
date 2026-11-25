"""Small source-mapping oracles for the fixed installed peer qualification."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('peer_rollup_installed_check', ROOT / 'scripts/peer_rollup_installed_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_force_include_mapping_matches_source_and_rejects_corrupt_bytes(checker, tmp_path):
    (tmp_path / 'bundle.yaml').write_bytes(b'bundle: exact\n')
    (tmp_path / 'context').mkdir()
    (tmp_path / 'context/system.md').write_bytes(b'Exact instructions\n')
    mapping = {'bundle.yaml': 'amplifier_web/bundle_data/bundle.yaml',
               'context': 'amplifier_web/bundle_data/context'}
    source = checker.source_member(tmp_path, 'amplifier_web/bundle_data/bundle.yaml', mapping)
    assert source == tmp_path / 'bundle.yaml'
    assert checker.sha(source.read_bytes()) == checker.sha(b'bundle: exact\n')
    assert checker.sha(source.read_bytes()) != checker.sha(b'bundle: forged\n')
    assert checker.source_member(tmp_path, 'amplifier_web/bundle_data/context/system.md', mapping) == tmp_path / 'context/system.md'
    assert checker.source_member(tmp_path, 'amplifier_web/service.py', mapping) == tmp_path / 'amplifier_web/service.py'


def test_wheel_mapping_refuses_path_escape(checker, tmp_path):
    for name in ('/absolute', '../escape', 'amplifier_web/../../escape'):
        with pytest.raises(ValueError):
            checker.source_member(tmp_path, name, {})


def test_declared_force_include_members_have_exact_source_owner(checker):
    import tomllib
    mapping = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['force-include']
    for source, destination in mapping.items():
        assert checker.source_member(ROOT, destination, mapping) == ROOT / source
        assert (ROOT / source).exists()
