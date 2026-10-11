"""Synthetic connection metadata for already-configured browser journeys."""
import json
from pathlib import Path


def seed_connection(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / 'settings.yaml').write_text(json.dumps({'config': {'providers': [
        {'module': 'provider-fixture', 'id': 'fixture',
         'config': {'default_model': 'fixture-model'}}]}}))
