"""Opt-in integration proof for the actual isolated warm-worker lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fixture', ['warm_worker_probe.py', 'history_edit_probe.py'])
async def test_worker_probe_answers_current_preparation_batch(fixture):
    """Check the fixture bridge without starting its opt-in real runtime."""
    import ast
    import asyncio
    from types import SimpleNamespace
    from amplifier_web.preparation_context import OPERATIONS

    tree = ast.parse((ROOT / 'tests' / 'fixtures' / fixture).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'main')
    publish = next(node for node in main.body if isinstance(node, ast.FunctionDef)
                   and node.name == 'publish')
    future = asyncio.get_running_loop().create_future()
    worker = SimpleNamespace(session=SimpleNamespace(session_id='fixture-session'),
                             bridges={'request': future})
    scope = {'events': [], 'workers': [worker]}
    exec(compile(ast.Module(body=[publish], type_ignores=[]), fixture, 'exec'), scope)
    scope['publish']({'op': 'bridge', 'id': 'request', 'operation': 'context.prepare',
                      'args': {'_contextInputs': ['original-input']}})
    result = await future
    assert set(result) == set(OPERATIONS)
    assert result['context.manifest'] == {
        'available': True, 'value': {'surfaces': [], 'inputIds': ['original-input']}}
    assert result['coordination.current']['value']['sessionId'] == 'fixture-session'
    assert result['memory.context'] == {'available': True, 'value': {'items': []}}
    assert result['message.reactions.context'] == {'available': False}


def test_actual_worker_parks_and_reuses_with_real_shared_state(tmp_path):
    python = os.environ.get("UNIFIED_RUNTIME_PYTHON")
    foundation = os.environ.get("WARM_FOUNDATION_PATH")
    if not python and not foundation:
        pytest.skip("set UNIFIED_RUNTIME_PYTHON or WARM_FOUNDATION_PATH for the isolated runtime proof")
    command = [python] if python else [shutil.which("uv"), "run", "--project",
        str(ROOT / "amplifier_web/runtime_deps"), "--with", foundation, "python"]
    assert command[0], "uv is required for the isolated runtime proof"
    result = subprocess.run(
        [*command, str(ROOT / "tests/fixtures/warm_worker_probe.py"), str(tmp_path)],
        cwd=ROOT, text=True, capture_output=True, timeout=180,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(tmp_path / "runtime-env")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["same_mounted_session"] is True
    assert evidence["two_authoritative_turns"] is True
    assert evidence["cold_prepare_seconds"] >= 0
    assert evidence["stamp_only_dispatch_seconds"] >= 0
