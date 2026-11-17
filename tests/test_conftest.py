"""The tests do not use what belongs to an Amplifier Unified of this machine.

A shell that a running Amplifier Unified starts (a chat's shell tool, a worker)
inherits names from that service. CI has none of them. An installed one keeps
its data below the home folder, and the product uses those folders when no name
says otherwise. ``conftest.py`` handles both when pytest loads it.

The product also finds some folders through the account database. ``conftest.py``
gives the account's entry a home folder of the run, and of each test.

``TestInsideARun`` holds what is true inside every run of this suite. The last
case starts a second run that inherits every name with a made-up value, and
requires the same there. It fails when ``conftest.py`` leaves one name alone.

Such a shell also inherits ``AMPLIFIER_APP_GENERATION_EXEC``. The product only
writes that name and never reads it, so ``conftest.py`` leaves it alone.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web import (
    runtime_preparation, session_files, shared_state, terminal_cli, test_host_resources, update_sources, updates,
)
from amplifier_web.host import config

REPOSITORY = Path(__file__).resolve().parents[1]
# The lists are written out here, apart from the lists in conftest.py: a name
# that someone takes out of conftest.py still has its case. Every value is made up.
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
# The product's own folders: a folder of the run outside a test, a folder of the test inside one.
HOMES = ('AMPLIFIER_WEB_HOME', 'AMPLIFIER_HOME', 'AMPLIFIER_SESSION_STATE_HOME', 'AMPLIFIER_TERMINAL_HOME')
# A test must never get these from outside.
REMOVED = sorted({*SERVICE_FOLDERS, *SERVICE_VALUES} - set(HOMES))


def names():
    return {name: os.environ.get(name) for name in (*HOMES, *REMOVED)}


def product_folders():
    folders = [config.app_home(), session_files.amplifier_home(), shared_state.shared_state_home(),
               terminal_cli.root(), session_files.capture_sessions_dir(REPOSITORY)]
    # The product takes some folders from the account database. HOME and the names do not move them.
    try:
        import pwd
    except ImportError:  # Windows has no account database, and no such folder of the product.
        return folders
    folders.append(pwd.getpwuid(os.getuid()).pw_dir)
    if test_host_resources.identity():  # Linux with a machine ID: the account's test-resource authority.
        folders.append(test_host_resources.directory())
    return folders


# Module-level code runs before every fixture. Only conftest.py is earlier.
WHEN_PYTEST_IMPORTED_THIS_FILE = (names(), product_folders())


@pytest.fixture(scope='module')
def in_a_module_fixture():
    # Such a fixture runs before the fixture that gives each test its folders.
    return names(), product_folders()


def below(folder, parent):
    # Compare real paths: a temporary folder can be reached through a link or a short name.
    return Path(os.path.realpath(folder)).is_relative_to(os.path.realpath(parent))


def outside_a_test(seen, session_home):
    found, folders = seen
    assert {name: found[name] for name in REMOVED} == dict.fromkeys(REMOVED)
    for name in HOMES:
        assert found[name] is not None and below(found[name], session_home), name
    for folder in folders:
        assert below(folder, session_home), folder


class TestInsideARun:
    def test_module_level_code_gets_no_service_name_and_folders_of_this_run(self, session_home):
        outside_a_test(WHEN_PYTEST_IMPORTED_THIS_FILE, session_home)

    def test_a_module_fixture_gets_no_service_name_and_folders_of_this_run(self, in_a_module_fixture, session_home):
        outside_a_test(in_a_module_fixture, session_home)

    def test_a_test_gets_no_service_name_and_folders_of_its_own(self, tmp_path):
        found = names()
        assert {name: found[name] for name in REMOVED} == dict.fromkeys(REMOVED)
        for name in HOMES:
            assert below(found[name], tmp_path), name
        for folder in product_folders():
            assert below(folder, tmp_path), folder
        home = Path(config.app_home())
        # The source store and the component generation come from that home alone.
        assert update_sources.store_environment(home)['AMPLIFIER_SOURCE_STORE'] == str(home / 'source-store')
        assert updates.selected_release(home) is None
        assert updates.foundation_home(home) == home / 'foundation'
        with runtime_preparation.preparation_scope() as facts:
            assert facts == {}  # Not the immutable runtime of a worker.

    def test_the_rest_of_the_account_entry_is_the_real_one(self):
        pwd = pytest.importorskip('pwd')  # Windows has no account database.
        entry = pwd.getpwuid(os.getuid())
        real = pwd.getpwnam(entry.pw_name)  # conftest.py changes the lookup by ID only.
        assert (*entry[:5], *entry[6:]) == (*real[:5], *real[6:])
        assert entry.pw_dir != real.pw_dir
        other = next((row for row in pwd.getpwall() if row.pw_uid != os.getuid()), None)
        if other is not None:  # Another account keeps its whole entry.
            found = pwd.getpwuid(other.pw_uid)
            assert found == pwd.getpwnam(found.pw_name)


def test_the_cases_above_pass_in_a_run_that_inherits_every_name(tmp_path):
    service = tmp_path / 'service'
    inherited = dict(SERVICE_VALUES)
    for name, folder in SERVICE_FOLDERS.items():
        (service / folder).mkdir(parents=True)
        inherited[name] = str(service / folder)
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
         '--basetemp', str(tmp_path / 'run'), f'{Path(__file__).resolve()}::TestInsideARun'],
        cwd=REPOSITORY, env={**os.environ, **inherited}, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    # The second run put nothing into a folder of the made-up service.
    assert sorted(service.rglob('*')) == sorted(service / folder for folder in SERVICE_FOLDERS.values())
