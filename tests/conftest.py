import os

import pytest

# A shell that a running Amplifier Unified starts (a chat's shell tool, a
# worker) inherits these names from that service. CI sets none of them. The
# product reads each one (the function is named beside it), so a test that
# inherits them can use the service's component generation or its source store,
# and can give another result than in CI.
#
# pytest loads this file before it imports a test module of this folder. The
# names go here and not in a fixture, so that module-level code, a fixture with
# a wider scope and a module's own copy of os.environ do not get them either.
# A test that needs one of them sets it itself.
SERVICE_NAMES = (
    'AMPLIFIER_UNIFIED_RELEASE',  # updates.selected_release
    'AMPLIFIER_SOURCE_STORE',  # update_sources.store_environment, reset.perform
    'AMPLIFIER_RUNTIME_IMMUTABLE',  # runtime_preparation.preparation_scope
    'AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH',  # session_files.capture_sessions_dir
    'AMPLIFIER_WEB_HOME',  # host.config.app_home
    'AMPLIFIER_SESSION_STATE_HOME',  # shared_state.shared_state_home
    'OPENAI_BASE_URL',  # native_provider._official_endpoint
    'INVOCATION_ID',  # deployment_service.current_process_is_unit_managed
)
for _name in SERVICE_NAMES:
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def isolated_shared_session_files(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'unified-home'))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'amplifier-home'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'shared-session-state'))
    monkeypatch.setenv('AMPLIFIER_TERMINAL_HOME', str(tmp_path / 'terminal-home'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create
