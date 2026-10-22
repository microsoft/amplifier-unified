"""Synthetic browser-cookie acceptance. No provider or real PAM calls."""
from pathlib import Path
import os
import sys
import tempfile
from aiohttp import web
if os.environ.get('ARTIFACT_INSTALLED_ROOT'):
    # Import the installed candidate before shared fixtures add the source root.
    import amplifier_web
    actual = Path(amplifier_web.__file__).resolve()
    assert actual.is_relative_to(Path(os.environ['ARTIFACT_INSTALLED_ROOT']).resolve()), actual
    print('INSTALLED_BOUNDARY', actual, flush=True)
import chat_ui_server  # installs the same deterministic runtime fixture
from amplifier_web import auth

auth.authenticate_pam = lambda username, password: (username, password) == ('inline-fixture', 'fixture-password')

async def main(home, port):
    app = await chat_ui_server.fixture.main(home)
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    return app


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8958
    with tempfile.TemporaryDirectory(prefix='amplifier-inline-ui-') as temporary:
        web.run_app(main(Path(temporary), port), host='127.0.0.1', port=port, print=None)
