"""Explicit owned environment; missing prerequisites fail, never skip."""

import os
from pathlib import Path

BASE = Path(os.environ["STORIES_STATE"])
URL = os.environ["STORIES_URL"]
TOKEN = Path(os.environ["STORIES_TOKEN_FILE"])
FIXTURE = Path(os.environ["STORIES_FIXTURE"])
STORIES = Path(os.environ["STORIES_SOURCE"])
SERVER = os.environ["STORIES_SERVER"]
