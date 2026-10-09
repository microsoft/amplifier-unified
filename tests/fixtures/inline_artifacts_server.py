"""Synthetic browser-cookie acceptance. No provider or real PAM calls."""
from pathlib import Path
import tempfile
from aiohttp import web
import chat_ui_server  # installs the same deterministic runtime fixture
from amplifier_web import auth

auth.authenticate_pam = lambda username, password: (username, password) == ('inline-fixture', 'fixture-password')

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-inline-ui-') as temporary:
        web.run_app(chat_ui_server.fixture.main(Path(temporary)), host='127.0.0.1', port=8958, print=None)
