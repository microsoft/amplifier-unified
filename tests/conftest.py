import atexit
import os
from pathlib import Path
import shutil
import tempfile

import pytest

try:
    import pwd
except ImportError:  # Windows has no account database.
    pwd = None

# The tests must not use what belongs to an Amplifier Unified of this machine:
# the names that a running one gives to its shells, and the folders where an
# installed one keeps its data. tests/test_conftest.py holds the cases.
#
# pytest loads this file before it imports a test module of this folder. The
# lines below run here and not in a fixture, so that they also hold for
# module-level code, for a fixture with a wider scope, for a module's own copy
# of os.environ, and for work that a test leaves running when it ends.

# 1. A shell that a running Amplifier Unified starts (a chat's shell tool, a
# worker) inherits these names from that service. CI sets none of them. The
# product reads each one (the function is named beside it), so a test that
# inherits them can use the service's component generation or its source store,
# and can give another result than in CI. A test that needs one sets it itself.
SERVICE_NAMES = (
    'AMPLIFIER_UNIFIED_RELEASE',  # updates.selected_release
    'AMPLIFIER_SOURCE_STORE',  # update_sources.store_environment, reset.perform
    'AMPLIFIER_RUNTIME_IMMUTABLE',  # runtime_preparation.preparation_scope
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH',  # session_files.capture_sessions_dir
    'OPENAI_BASE_URL',  # native_provider._official_endpoint
    'INVOCATION_ID',  # deployment_service.current_process_is_unit_managed
)
for _name in SERVICE_NAMES:
    os.environ.pop(_name, None)

# 2. The product's own folders. Without a name the product uses a folder below
# the home folder, and an installed Amplifier Unified keeps its data there. So
# each name holds a folder of this run from here on. Each test then gets new
# folders of its own (the fixture below).
SESSION_HOME = tempfile.mkdtemp(prefix='amplifier-unified-tests-')
atexit.register(shutil.rmtree, SESSION_HOME, ignore_errors=True)
HOMES = {
    'AMPLIFIER_WEB_HOME': 'unified-home',  # host.config.app_home
    'AMPLIFIER_HOME': 'amplifier-home',  # session_files.amplifier_home
    'AMPLIFIER_SESSION_STATE_HOME': 'shared-session-state',  # shared_state.shared_state_home
    'AMPLIFIER_TERMINAL_HOME': 'terminal-home',  # terminal_cli
}
for _name, _folder in HOMES.items():
    os.environ[_name] = os.path.join(SESSION_HOME, _folder)


@pytest.fixture(scope='session')
def session_home():
    """The folder that holds the product's folders outside a test."""
    return Path(SESSION_HOME)


@pytest.fixture(autouse=True)
def isolated_shared_session_files(tmp_path, monkeypatch):
    for name, folder in HOMES.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    if pwd is not None:
        # 3. Some folders the product finds through the account database, not
        # through HOME or a name: the test-resource authority that every
        # Amplifier installation of the account shares
        # (test_host_resources.directory), and the test runners' folders. A
        # test must not take that authority's lock or read its reservations.
        real = pwd.getpwuid

        def account(uid):
            entry = real(uid)
            if uid != os.getuid():
                return entry
            return pwd.struct_passwd((*entry[:5], str(tmp_path / 'account-home'), entry[6]))

        monkeypatch.setattr(pwd, 'getpwuid', account)


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create
