import os
from pathlib import Path
import shutil
import tempfile

import pytest

try:
    import pwd
except ImportError:  # Python has no pwd module on Windows.
    pwd = None

# This file keeps three things of this machine away from the pytest process:
# the environment variables in its two lists (parts 1 and 2), the folders that
# the product takes when four of its folder variables are not set (part 3),
# and the home folder that a lookup of the own account by ID gives (part 4).
# tests/test_conftest.py holds the cases.
#
# pytest loads this file before it imports a test module of this folder. The
# lines below run here and not only in a fixture, so that they also hold for
# module-level code, for a fixture with a wider scope, for a module's own copy
# of os.environ, and for work that a test leaves running when it ends. The
# changes stay until the process ends.
#
# The file does nothing else. It does not change HOME (USERPROFILE on
# Windows): a file or a folder that the product finds through that variable,
# and not through one of the four variables of part 3, is still the person's
# (for example a ChatGPT sign-in file below ~/.amplifier), and tools such as
# Git and uv keep their own files below the home folder. It removes no
# variable outside its two lists. Parts 1 to 3 change os.environ, so a program
# that a test starts with that environment gets them too. Part 4 is in this
# process only. A pytest run with --noconftest does not load the file.

# 1. A shell that a running Amplifier Unified starts (a chat's shell tool, a
# worker) inherits these variables from that service. The product reads each
# one (the function is named beside it). A test that inherits one can use the
# service's component generation or its source store, and can give another
# result than a run without it. A test that needs one sets it itself.
# The list comes from a shell of an installation on Linux. An installation on
# macOS or Windows sets more variables for its service
# (deployment_service._install_launchd, windows_consumer.environment). They
# are not in the list: no shell of such an installation was looked at.
SERVICE_VARIABLES = (
    'AMPLIFIER_UNIFIED_RELEASE',  # updates.selected_release
    'AMPLIFIER_SOURCE_STORE',  # update_sources.store_environment, reset.perform
    'AMPLIFIER_RUNTIME_IMMUTABLE',  # runtime_preparation.preparation_scope
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH',  # session_files.capture_sessions_dir
    'OPENAI_BASE_URL',  # native_provider._official_endpoint
    'INVOCATION_ID',  # deployment_service.current_process_is_unit_managed
)
# 2. The product's default environment variables for provider credentials
# (setup.PROVIDER_ENV). A person's shell can hold them. The product reads them
# for a provider's credential (setup.environment_credential), and the
# variables of a provider are part of the identity of its connections
# (provider_catalog.connection_key). A test must not get one from outside. A
# test that needs one sets a made-up one itself. A connection can name another
# variable for its credential; such a variable is not in the list.
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


# The product sets some of these variables for its own process and does not
# take them back (for example host.session), and a test can leave one. So they
# also go before each test (the fixture below) and after it. After it means:
# after pytest's teardown of the test, when the fixtures that end with the
# test have ended. monkeypatch is such a fixture. When it ends, it puts back
# what it found, and that can be a value that the product set during the test.
# hookwrapper=True: an error in the teardown of a test does not skip the line
# after the yield.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown():
    yield
    remove_outside_variables()


# 3. Four folders of the product. The product takes each one from its variable
# (the function is named beside it). When that variable is not set, the
# product takes another variable or a folder below the home folder, and an
# installed Amplifier Unified can keep its data there. So each variable holds a
# folder of this run from here on. Each test then gets new folders of its own
# (the fixture below).
# A real path: no link and no short name in it. On Windows the temporary folder
# can have a short name with a '~', and the product refuses a '~' in the path
# of its app home (windows_files._component).
SESSION_HOME = os.path.realpath(tempfile.mkdtemp(prefix='amplifier-unified-tests-'))
HOMES = {
    'AMPLIFIER_WEB_HOME': 'unified-home',  # host.config.app_home
    'AMPLIFIER_HOME': 'amplifier-home',  # session_files.amplifier_home
    'AMPLIFIER_SESSION_STATE_HOME': 'shared-session-state',  # shared_state.shared_state_home
    'AMPLIFIER_TERMINAL_HOME': 'terminal-home',  # terminal_cli.root
}
for _name, _folder in HOMES.items():
    os.environ[_name] = os.path.join(SESSION_HOME, _folder)


def pytest_unconfigure(config):
    # A second pytest run inside this process that loads this file would remove
    # the folder at its own end, before this run ends.
    shutil.rmtree(SESSION_HOME, ignore_errors=True)


# 4. Some folders the product finds through a lookup of the account by ID
# (pwd.getpwuid), not through HOME or a variable: the test-resource authority
# that every Amplifier installation of the account shares
# (test_host_resources.directory), and folders that incus_engine_generations,
# incus_engine_probe and test_runner_controller use below that home folder. A
# test must not take that authority's lock or read its reservations. So, where
# Python has the pwd module, that lookup gives the caller's own account a home
# folder of this run, and of each test, in this process. The account database
# does not change: a lookup by name, and a program that a test starts, get the
# real home folder.
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
    # Directly, not with monkeypatch: monkeypatch puts back what it found.
    remove_outside_variables()
    for name, folder in HOMES.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    monkeypatch.setitem(ACCOUNT, 'home', str(tmp_path / 'account-home'))


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create
