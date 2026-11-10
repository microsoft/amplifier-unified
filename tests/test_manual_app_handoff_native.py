"""Portable fixture-contract checks; actual qualification is the Windows job."""
import ast
from pathlib import Path


def test_native_fixture_compiles_and_keeps_production_verifiers_real():
    path = Path(__file__).parent / 'fixtures/manual_app_handoff_native.py'
    source = path.read_text()
    ast.parse(source)
    assert 'WindowsOwnedProcess.start' in source and 'trusted_python=sys._base_executable' in source
    assert "artifacts._get = local_acquisition" in source
    for name in ('_native_binding', '_payload', '_health', '_pinned_reader', 'installed_target'):
        assert name + ' =' not in source
    assert "'publicAcceptance': False" in source
    assert "'UV_LINK_MODE': 'copy'" in source


def test_native_fixture_is_a_separate_narrow_workflow():
    path = Path(__file__).parents[1] / '.github/workflows/manual-app-handoff-native.yml'
    source = path.read_text()
    assert 'runs-on: windows-latest' in source and 'matrix:' not in source
    assert 'workflow_dispatch:' in source
    assert 'platform-mechanisms' not in source and 'release.yml' not in source
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in source