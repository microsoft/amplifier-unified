import os
from pathlib import Path
import shutil
import tempfile

import pytest

try:
    import pwd
except ImportError:  # Python has no pwd module on Windows.
    pwd = None

# The tests must not use what belongs to an Amplifier Unified of this machine:
# the environment variables that a running one gives to the shells that it
# starts, the person's provider credentials, and the folders where an installed
# one keeps its data. tests/test_conftest.py holds the cases.
#
# pytest loads this file before it imports a test module of this folder. The
# lines below run here and not only in a fixture, so that they also hold for
# module-level code, for a fixture with a wider scope, for a module's own copy
# of os.environ, and for work that a test leaves running when it ends.

# 1. A shell that a running Amplifier Unified starts (a chat's shell tool, a
# worker) inherits these variables from that service. The product reads each
# one (the function is named beside it). A test that inherits one can use the
# service's component generation or its source store, and can give another
# result than a run without it. A test that needs one sets it itself.
SERVICE_VARIABLES = (
    'AMPLIFIER_UNIFIED_RELEASE',  # updates.selected_release
    'AMPLIFIER_SOURCE_STORE',  # update_sources.store_environment, reset.perform
    'AMPLIFIER_RUNTIME_IMMUTABLE',  # runtime_preparation.preparation_scope
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH',  # session_files.capture_sessions_dir
    'OPENAI_BASE_URL',  # native_provider._official_endpoint
    'INVOCATION_ID',  # deployment_service.current_process_is_unit_managed
)
# The person's provider credentials (setup.PROVIDER_ENV). The product takes the
# first one that is set as the credential of a connection, and each one is part
# of a connection's identity (provider_catalog.connection_key). A test must not
# use them. A test that needs a credential sets a made-up one itself.
CREDENTIAL_VARIABLES = (
    'OPENAI_API_KEY',
    'TYPESAFE_API_KEY',
    'LOCAL_DECISIONS_API_KEY',
    'AZURE_OPENAI_API_KEY',
    'CHAT_COMPLETIONS_API_KEY',
    'ANTHROPIC_API_KEY',
    'GOOGLE_API_KEY',
    'GEMINI_API_KEY',
    'GITHUB_TOKEN',
    'COPILOT_AGENT_TOKEN',
    'COPILOT_GITHUB_TOKEN',
    'GH_TOKEN',
)


def remove_outside_variables():
    for name in (*SERVICE_VARIABLES, *CREDENTIAL_VARIABLES):
        os.environ.pop(name, None)


remove_outside_variables()

# 2. The product's own folders. Without these variables the product uses its
# default folders, and an installed Amplifier Unified keeps its data there. So
# each variable holds a folder of this run from here on. Each test then gets
# new folders of its own (the fixture below).
SESSION_HOME = tempfile.mkdtemp(prefix='amplifier-unified-tests-')
HOMES = {
    'AMPLIFIER_WEB_HOME': 'unified-home',  # host.config.app_home
    'AMPLIFIER_HOME': 'amplifier-home',  # session_files.amplifier_home
    'AMPLIFIER_SESSION_STATE_HOME': 'shared-session-state',  # shared_state.shared_state_home
    'AMPLIFIER_TERMINAL_HOME': 'terminal-home',  # terminal_cli.root
}
for _name, _folder in HOMES.items():
    os.environ[_name] = os.path.join(SESSION_HOME, _folder)


def pytest_unconfigure(config):
    shutil.rmtree(SESSION_HOME, ignore_errors=True)


# 3. Some folders the product finds through the account database
# (pwd.getpwuid), not through HOME or a variable: the test-resource authority
# that every Amplifier installation of the account shares
# (test_host_resources.directory), and the test runners' folders. A test must
# not take that authority's lock or read its reservations. So the lookup gives
# the caller's own account a home folder of this run, and of each test. A
# program that a test starts still asks the real database.
ACCOUNT = {'home': os.path.join(SESSION_HOME, 'account-home')}
if pwd is not None:
    _getpwuid = pwd.getpwuid

    def _account(uid):
        entry = _getpwuid(uid)
        if uid != os.getuid():
            return entry
        return pwd.struct_passwd((*entry[:5], ACCOUNT['home'], *entry[6:]))

    pwd.getpwuid = _account


@pytest.fixture(scope='session')
def session_home():
    """The folder that holds the product's folders outside a test."""
    return Path(SESSION_HOME)


@pytest.fixture(autouse=True)
def isolated_shared_session_files(tmp_path, monkeypatch):
    # The product sets some of these variables for its own process and does
    # not take them back (host.session). So they go before each test and after
    # it, directly: monkeypatch puts back what it found, and it would find the
    # value that an earlier test left.
    remove_outside_variables()
    for name, folder in HOMES.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    monkeypatch.setitem(ACCOUNT, 'home', str(tmp_path / 'account-home'))
    yield
    remove_outside_variables()


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create
