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


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['true', 'nonboolean', 'agent_config', 'inherited_config'])
async def test_rejected_child_options_preserve_retained_history_before_effects(tmp_path, mode):
    import asyncio
    import copy
    from types import SimpleNamespace
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_foundation')
    from amplifier_web.host.children import Children
    from amplifier_web.host.storage import SessionStore

    runtime=SimpleNamespace(inbox=asyncio.Queue())
    children=Children(runtime,SessionStore(tmp_path/'children'),None)
    config={'agents':{'worker':{}}}
    parent=SimpleNamespace(session_id='parent',coordinator=SimpleNamespace(config=config))
    children.prepared['parent']=object()
    children.rows={f'completed-{index}':{'status':'completed','reportId':f'report-{index}'} for index in range(256)}
    original=copy.deepcopy(children.rows)
    original_paths=list(tmp_path.rglob('*'))
    args={}
    if mode=='true':args['use_subprocess']=True
    elif mode=='nonboolean':args['use_subprocess']=0
    elif mode=='agent_config':args['agent_configs']={'worker':{'spawn_mode':'subprocess'}}
    else:config['spawn_mode']='subprocess'
    match='use_subprocess must be a boolean' if mode=='nonboolean' else 'Subprocess child sessions are not supported'
    with pytest.raises(ValueError,match=match):
        await children.spawn('worker','must not execute',parent,sub_session_id='rejected-child',**args)
    assert children.rows==original
    assert runtime.inbox.empty()
    assert not children.sessions
    assert list(tmp_path.rglob('*'))==original_paths
