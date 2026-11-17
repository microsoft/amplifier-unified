"""The tests do not depend on what a running Amplifier Unified puts into a shell.

A chat's shell tool, and a worker, inherit names from the service that started
them. CI has none of these names. ``conftest.py`` removes them when pytest loads
it. ``TestInsideARun`` holds what is true inside every run of this suite. The
last case starts a second run that inherits every name with a made-up value,
and requires the same there. That case fails when ``conftest.py`` does not
remove one of the names.

Such a shell also inherits ``AMPLIFIER_APP_GENERATION_EXEC``. The product only
writes that name and never reads it, so ``conftest.py`` leaves it alone.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web import runtime_preparation, session_files, shared_state, update_sources, updates
from amplifier_web.host import config

REPOSITORY = Path(__file__).resolve().parents[1]
# Every value is made up. This list is written out here, apart from the list in
# conftest.py: a name that someone takes out of conftest.py still has its case.
FOLDERS = {
    'AMPLIFIER_SOURCE_STORE': 'source-store',
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH': 'capture',
    'AMPLIFIER_WEB_HOME': 'app-home',
    'AMPLIFIER_SESSION_STATE_HOME': 'ownership',
}
VALUES = {
    'AMPLIFIER_UNIFIED_RELEASE': '5e11e5e11e5e11e5e11e5e11e5e11e50',
    'AMPLIFIER_RUNTIME_IMMUTABLE': '1',
    'OPENAI_BASE_URL': 'http://127.0.0.1:9/made-up/v1',
    'INVOCATION_ID': 'feedfacefeedfacefeedfacefeedface',
}
NAMES = (*FOLDERS, *VALUES)


def inherited():
    return sorted(name for name in NAMES if name in os.environ)


WHEN_PYTEST_IMPORTED_THIS_FILE = inherited()


@pytest.fixture(scope='module')
def in_a_module_fixture():
    return inherited()


class TestInsideARun:
    def test_no_name_was_set_when_pytest_imported_this_file(self):
        # Module-level code runs before every fixture. Only conftest.py is earlier.
        assert WHEN_PYTEST_IMPORTED_THIS_FILE == []

    def test_no_name_is_set_in_a_fixture_with_module_scope(self, in_a_module_fixture):
        # Such a fixture runs before the fixture that gives each test its folders.
        assert in_a_module_fixture == []

    @pytest.mark.parametrize('name', NAMES)
    def test_a_name_is_not_set_or_is_a_folder_of_this_test(self, name, tmp_path):
        value = os.environ.get(name)
        assert value is None or Path(value).is_relative_to(tmp_path)

    def test_the_product_uses_only_the_folders_of_this_test(self, tmp_path):
        own = tmp_path.resolve()
        home = Path(config.app_home())
        assert home.is_relative_to(own)
        # The source store and the component generation come from that home alone.
        assert update_sources.store_environment(home)['AMPLIFIER_SOURCE_STORE'] == str(home / 'source-store')
        assert updates.selected_release(home) is None
        assert updates.foundation_home(home) == home / 'foundation'
        assert shared_state.shared_state_home().resolve().is_relative_to(own)
        assert session_files.capture_sessions_dir(tmp_path).is_relative_to(own)
        with runtime_preparation.preparation_scope() as facts:
            assert facts == {}  # Not the immutable runtime of a worker.


def test_the_cases_above_pass_in_a_run_that_inherits_every_name(tmp_path):
    service = tmp_path / 'service'
    names = dict(VALUES)
    for name, folder in FOLDERS.items():
        (service / folder).mkdir(parents=True)
        names[name] = str(service / folder)
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
         '--basetemp', str(tmp_path / 'run'), f'{Path(__file__).resolve()}::TestInsideARun'],
        cwd=REPOSITORY, env={**os.environ, **names}, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    # The second run put nothing into a folder of the made-up service.
    assert sorted(service.rglob('*')) == sorted(service / folder for folder in FOLDERS.values())
