"""Finite offline SDK transition checkpoint probe; never a live provider call."""
import asyncio
import json
from pathlib import Path
import sys

# Import the maintained fixture only. Product imports use the interpreter's
# installed environment; this script never adds a product source directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_context_continuity import sdk_transition_process_phase

if __name__ == "__main__":
    print(json.dumps(asyncio.run(sdk_transition_process_phase(sys.argv[1], sys.argv[2]))))