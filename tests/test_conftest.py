"""The tests do not use what belongs to an Amplifier Unified of this machine.

A shell that a running Amplifier Unified starts (a chat's shell tool, a worker)
inherits environment variables from that service. A person's shell can hold
provider credentials. An installed Amplifier Unified keeps its data in the
product's default folders. The product also finds some folders through the
account database. ``conftest.py`` handles these when pytest loads it, and
again around each test.

``TestInsideARun`` holds what is true inside every run of this suite. Its cases
run in the order in which they are written. The last case of this file starts a
second run that inherits the variables of a service's shell and the provider
credentials with made-up values, and requires the same there.

Such a shell also inherits ``AMPLIFIER_APP_GENERATION_EXEC``. The product only
writes that variable and never reads it, so ``conftest.py`` leaves it alone.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web import (
    runtime_preparation, session_files, setup, shared_state, terminal_cli, test_host_resources, update_sources, updates,
)
from amplifier_web.host import config

REPOSITORY = Path(__file__).resolve().parents[1]
# The lists are written out here, apart from the lists in conftest.py: a
# variable that someone takes out of conftest.py still has its case. Every
# value is made up.
SERVICE_FOLDERS = {
    'AMPLIFIER_SOURCE_STORE': 'source-store',
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH': 'capture',
    'AMPLIFIER_WEB_HOME': 'app-home',
    'AMPLIFIER_SESSION_STATE_HOME': 'ownership',
}
SERVICE_VALUES = {
    'AMPLIFIER_UNIFIED_RELEASE': '5e11e5e11e5e11e5e11e5e11e5e11e50',
    'AMPLIFIER_RUNTIME_IMMUTABLE': '1',
    'OPENAI_BASE_URL': 'http://127.0.0.1:9/made-up/v1',
    'INVOCATION_ID': 'feedfacefeedfacefeedfacefeedface',
}
CREDENTIALS = (
    'OPENAI_API_KEY', 'TYPESAFE_API_KEY', 'LOCAL_DECISIONS_API_KEY', 'AZURE_OPENAI_API_KEY',
    'CHAT_COMPLETIONS_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY',
    'GITHUB_TOKEN', 'COPILOT_AGENT_TOKEN', 'COPILOT_GITHUB_TOKEN', 'GH_TOKEN',
)
# The product's own folders: a folder of the run outside a test, a folder of the test inside one.
HOMES = ('AMPLIFIER_WEB_HOME', 'AMPLIFIER_HOME', 'AMPLIFIER_SESSION_STATE_HOME', 'AMPLIFIER_TERMINAL_HOME')
# A test must never get these from outside.
REMOVED = sorted({*SERVICE_FOLDERS, *SERVICE_VALUES, *CREDENTIALS} - set(HOMES))
# The product sets this one for its own process and does not take it back (host.session).
LEFT = 'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'


def variables():
    return {name: os.environ.get(name) for name in (*HOMES, *REMOVED)}


def product_folders():
    folders = [config.app_home(), session_files.amplifier_home(), shared_state.shared_state_home(),
               terminal_cli.root(), session_files.capture_sessions_dir(REPOSITORY)]
    # The product takes some folders from the account database. HOME and the variables do not move them.
    try:
        import pwd
        folders.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):  # No pwd module on Windows, or no entry for this account.
        return folders
    if test_host_resources.identity():  # Linux with a machine ID: the account's test-resource authority.
        folders.append(test_host_resources.directory())
    return folders


# Module-level code runs before every fixture. Only conftest.py is earlier.
WHEN_PYTEST_IMPORTED_THIS_FILE = (variables(), product_folders())


@pytest.fixture(scope='module')
def between_two_tests():
    # pytest sets up a fixture with a wider scope between two tests: after the
    # end of the test before, and before the folders of the test that asks for it.
    seen = variables(), product_folders()
    os.environ[LEFT] = 'left by a fixture'
    return seen


def real(folder):
    # A temporary folder can be reached through a link or a short name.
    return Path(os.path.realpath(folder))


def below(folder, parent):
    return real(folder).is_relative_to(real(parent))


def outside_a_test(seen, session_home):
    found, folders = seen
    assert {name: found[name] for name in REMOVED} == dict.fromkeys(REMOVED)
    for name in HOMES:
        assert found[name] is not None and below(found[name], session_home), name
    for folder in folders:
        assert below(folder, session_home), folder


class TestInsideARun:
    def test_module_level_code_gets_no_service_variable_and_folders_of_this_run(self, session_home):
        outside_a_test(WHEN_PYTEST_IMPORTED_THIS_FILE, session_home)

    def test_a_test_gets_no_service_variable_and_folders_of_its_own(self, tmp_path):
        found = variables()
        assert {name: found[name] for name in REMOVED} == dict.fromkeys(REMOVED)
        for name in HOMES:
            assert below(found[name], tmp_path), name
        for folder in product_folders():
            assert below(folder, tmp_path), folder
        home = Path(config.app_home())
        # The source store and the component generation come from that home alone.
        # An Amplifier Foundation without a shared source store names no store.
        store = update_sources.store_environment(home).get('AMPLIFIER_SOURCE_STORE')
        assert store is None or real(store) == real(home / 'source-store')
        assert updates.selected_release(home) is None
        assert updates.foundation_home(home) == home / 'foundation'
        with runtime_preparation.preparation_scope() as facts:
            assert facts == {}  # Not the immutable runtime of a worker.

    def test_every_credential_variable_of_the_product_has_its_case(self):
        # A new provider's variable must get into both lists.
        assert set(CREDENTIALS) == {name for names in setup.PROVIDER_ENV.values() for name in names}
        assert set(setup.COPILOT_ENV_ORDER) <= set(CREDENTIALS)

    def test_a_test_leaves_a_variable_set_as_the_product_does(self):
        os.environ[LEFT] = 'left by a test'

    def test_what_runs_between_two_tests_gets_no_service_variable_and_folders_of_this_run(
            self, between_two_tests, session_home):
        outside_a_test(between_two_tests, session_home)  # Not what the test before left.
        assert os.environ.get(LEFT) is None  # Not what the fixture left after it looked.

    def test_the_rest_of_the_account_entry_is_the_real_one(self):
        pwd = pytest.importorskip('pwd')  # Python has no pwd module on Windows.
        try:
            entry = pwd.getpwuid(os.getuid())
        except KeyError:
            pytest.skip('This account has no entry in the account database.')
        named = pwd.getpwnam(entry.pw_name)  # conftest.py changes the lookup by ID only.
        assert (*entry[:5], *entry[6:]) == (*named[:5], *named[6:])
        assert entry.pw_dir != named.pw_dir
        try:
            other = pwd.getpwuid(0 if os.getuid() else 1)
        except KeyError:  # This machine has no second account to look at.
            return
        assert other == pwd.getpwnam(other.pw_name)  # Another account keeps its whole entry.


def test_the_cases_above_pass_in_a_run_that_inherits_the_outside_variables(tmp_path, monkeypatch):
    service, temporary = tmp_path / 'service', tmp_path / 'temporary'
    temporary.mkdir()
    inherited = {**SERVICE_VALUES, **{name: 'made-up-' + name.lower() for name in CREDENTIALS}}
    for name, folder in SERVICE_FOLDERS.items():
        (service / folder).mkdir(parents=True)
        inherited[name] = str(service / folder)
    # This run can have options of its own. They are not for the second run.
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k no_case_has_this_name')
    environment = {name: value for name, value in os.environ.items() if name != 'PYTEST_ADDOPTS'}
    environment.update(inherited, TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
         '--basetemp', str(tmp_path / 'run'), f'{Path(__file__).resolve()}::TestInsideARun'],
        cwd=REPOSITORY, env=environment, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    # The second run put nothing into a folder of the made-up service.
    assert sorted(service.rglob('*')) == sorted(service / folder for folder in SERVICE_FOLDERS.values())
    # It removed its own folder for the product's folders when it ended.
    assert list(temporary.glob('amplifier-unified-tests-*')) == []
