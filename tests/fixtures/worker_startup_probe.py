"""Run the maintained native startup probe with checkout sources during development."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[2] / 'amplifier_web/worker_startup_probe.py'), run_name='__main__')
