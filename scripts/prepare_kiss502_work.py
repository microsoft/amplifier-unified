"""Prepare exact public Work ACP sources (no conversation, provider or container).

From an owned checkout:
    uv sync --locked --group dev --group work-media
    .venv/bin/python scripts/prepare_kiss502_work.py .ci/acp-work-sources
Then copy the resulting sources.json path into UNIFIED_ACP_WORK_SOURCES.
An installed guest prepares with its own Python and the same locked modules.
"""
import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures.acp_work_sources import prepare


if __name__ == "__main__":
    # Never enroll this preparation into a live host's shared store.
    os.environ.pop("AMPLIFIER_SOURCE_STORE", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    print(asyncio.run(prepare(sys.argv[1])))