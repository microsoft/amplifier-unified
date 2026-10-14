"""Immutable identity of the packaged frontend served by one application host."""
from dataclasses import dataclass
import json
from pathlib import Path

from . import __version__


BUILD_FILE = Path(__file__).parent / 'static' / 'build.json'


@dataclass(frozen=True)
class InstalledBuild:
    server_version: str
    frontend_version: str
    frontend_id: str

    def project(self):
        # Detach the tiny wire value so callers cannot mutate the host receipt.
        return {'serverVersion': self.server_version,
                'frontend': {'version': self.frontend_version, 'id': self.frontend_id}}


def capture():
    """Read once at host creation, never as part of a state publication."""
    try:
        with BUILD_FILE.open('rb') as source:
            raw = source.read(4097)
        value = json.loads(raw) if len(raw) <= 4096 else {}
    except (OSError, ValueError, UnicodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}

    def field(name, limit):
        item = value.get(name)
        return item if isinstance(item, str) and 0 < len(item) <= limit else 'unknown'

    return InstalledBuild(__version__, field('version', 60), field('id', 64))
