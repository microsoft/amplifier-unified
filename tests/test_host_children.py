import os
from pathlib import Path
import subprocess
import pytest


def test_model_selection_inheritance_is_opt_in_and_specialists_win():
    from types import SimpleNamespace
    from amplifier_web.host.model_selection import inherited_selection
    choice = {"instance": "provider-instance", "model": "chosen-model", "effort": "high"}
    loop = SimpleNamespace(config={"inherit_effective_model": True}, root_provider=SimpleNamespace(selection=choice))
    parent = SimpleNamespace(coordinator=SimpleNamespace(get=lambda name: loop))
    result = inherited_selection(parent, {}, [])
    assert result == choice and result is not choice
    assert inherited_selection(parent, {"model_role": "specialist"}, []) is None
    assert inherited_selection(parent, {"providers": [{"module": "other"}]}, []) is None
    assert inherited_selection(parent, {}, [object()]) is None
    loop.config.clear()
    assert inherited_selection(parent, {}, []) is None
    assert inherited_selection(parent, {}, [], {"effective_selection": choice}) == choice


def test_real_foundation_child_lifecycle_and_delegate_contract():
    python=os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON to a warmed standalone runtime for the real-core probe')
    probe=Path(__file__).parent/'fixtures/standalone_children_probe.py'
    result=subprocess.run([python,str(probe)],text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr+result.stdout
    assert '"delegate_compatible": true' in result.stdout
    assert '"cli_imports": false' in result.stdout


def test_real_community_recipe_completes_its_report_step():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python or not os.environ.get('WARM_RECIPES_PATH'):
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON and WARM_RECIPES_PATH for the community recipe probe')
    probe = Path(__file__).parent / 'fixtures/recipe_children_probe.py'
    result = subprocess.run([python, str(probe)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"recipe_report_completed": true' in result.stdout
    assert '"approval_preserved": true' in result.stdout
    assert '"subprocess_not_downgraded": true' in result.stdout
