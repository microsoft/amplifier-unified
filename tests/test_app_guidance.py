"""Verify real provider requests, without network calls to a model service."""
import os
from pathlib import Path
import subprocess
import pytest


def test_real_core_receives_canvas_tool_and_ephemeral_instructions():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the real Core canvas probe')
    result = subprocess.run([python, str(Path(__file__).parent/'fixtures/canvas_runtime_probe.py')],capture_output=True,text=True,timeout=45)
    assert result.returncode == 0, result.stdout+result.stderr
    assert '"canvas_guidance_in_provider_request": true' in result.stdout
    assert '"instructions_ephemeral": true' in result.stdout


def test_real_core_receives_fresh_notice_then_typed_surface_image():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the real Core surface probe')
    result = subprocess.run([python, str(Path(__file__).parent/'fixtures/surface_context_probe.py')], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert '"typed_image_before_answer": true' in result.stdout
