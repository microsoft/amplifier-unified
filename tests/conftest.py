import pytest


@pytest.fixture(autouse=True)
def isolated_shared_session_files(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'amplifier-home'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create
