"""Verify real provider requests, without network calls to a model service."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_surface_runtime_import_does_not_require_host_css_parser():
    result = subprocess.run([sys.executable, '-c', """
import importlib.abc
import sys

class RuntimeWithoutCssParser(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tinycss2' or fullname.startswith('tinycss2.'):
            raise ModuleNotFoundError('CSS parser is only installed in the web host')

sys.meta_path.insert(0, RuntimeWithoutCssParser())
from amplifier_web.surface_delivery import SurfaceProvider
"""], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


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
