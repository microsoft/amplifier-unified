import pytest


@pytest.fixture
def authenticated_client(aiohttp_client):
    async def create(app):
        client = await aiohttp_client(app)
        client.session.headers["Authorization"] = "Bearer " + app["control_token"]
        return client

    return create