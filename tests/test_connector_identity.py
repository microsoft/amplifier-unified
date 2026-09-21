"""Real local OAuth/MCP identity binding; no external account or token claims."""
import copy
import json
import pytest

from test_smart_tools import manager, Service
from test_connector_lifecycle import authorize, configure_remote, wait_for
from mcp_oauth_server import Fixture, ACCOUNT_URI
from amplifier_web.mcp_account import AccountReviewRequired, parse_result
from amplifier_web.smart_tools import SmartToolsManager


@pytest.fixture
async def remote():
    fixture = await Fixture(identity=True).start()
    yield fixture
    await fixture.close()


async def connected(manager, remote):
    await configure_remote(manager, remote, auth='oauth')
    await authorize(manager, remote)
    await wait_for(lambda: manager.oauth.status('remote')['phase'] in {'ready', 'error'})
    assert manager.oauth.status('remote')['phase'] == 'ready', manager._server('remote').get('error')
    return manager._server('remote')


async def test_verified_account_reconnect_refresh_and_restart(manager, remote):
    row = await connected(manager, remote)
    assert row['account']['status'] == 'verified'
    assert row['account']['subject'] == 'fixture-user'
    assert row['account']['provenance']['kind'] == 'authenticated-connector-resource'
    await manager.call_tool('remote', 'read_record', {'key':'first'})
    store = next((manager.root / 'credentials').glob('*.json'))
    saved = json.loads(store.read_text()); saved['expiresAt'] = 1; store.write_text(json.dumps(saved))
    await manager.connect('remote', reconnect=True)
    assert remote.provider.refreshes == 1 and row['account']['subject'] == 'fixture-user'
    old = copy.deepcopy(manager.service.state)
    await manager.close()
    restarted = SmartToolsManager(Service(manager.service.data_dir, old))
    try:
        assert restarted._server('remote')['account']['status'] == 'unconfirmed'
        assert not restarted.connections and remote.calls == 1
        await restarted.connect('remote')
        assert restarted._server('remote')['account']['subject'] == 'fixture-user'
        assert remote.calls == 1 and remote.provider.exchanges == 1
        await restarted.execute('smartTools.authForget', {'id':'remote'})
        assert restarted._server('remote')['account']['status'] == 'unconfirmed'
        assert restarted._server('remote')['accountBinding']['subject'] == 'fixture-user'
        assert not store.exists()
    finally:
        await restarted.close()


async def test_identity_switch_blocks_calls_and_requires_exact_shared_accept(manager, remote):
    row = await connected(manager, remote)
    for token in remote.provider.access.values():
        token.subject = 'principal-two'
    with pytest.raises(AccountReviewRequired, match='account changed'):
        await manager.call_tool('remote', 'read_record', {'key':'must-not-run'})
    assert remote.calls == 0
    assert row['account']['status'] == 'changed'
    assert row['accountBinding']['subject'] == 'fixture-user'
    candidate = row['account']['candidate']
    args = dict(id='remote', expectedRevision=row['accountRevision'], issuer=candidate['issuer'], subject=candidate['subject'])
    with pytest.raises(ValueError, match='review changed'):
        await manager.execute('smartTools.accountAccept', {**args, 'subject':'forged'}, origin='agent')
    result = await manager.command('smartTools.accountAccept', args, 'accept-second', origin='agent')
    assert result['decision']['origin'] == 'agent' and result['connected'] is False
    assert remote.calls == 0
    # Same command receipt is idempotent; accepting never connects or executes.
    with pytest.raises(ValueError, match='already has a receipt'):
        await manager.command('smartTools.accountAccept', args, 'accept-second', origin='agent')
    assert manager.operation('accept-second')['result'] == result
    await manager.connect('remote')
    assert row['account']['status'] == 'verified' and row['account']['subject'] == 'principal-two'
    await manager.call_tool('remote', 'read_record', {'key':'explicit-new-account'})
    assert remote.calls == 1


async def test_identity_refresh_change_and_missing_attestation_block_reconnect(manager, remote):
    row = await connected(manager, remote)
    remote.provider.principal = 'principal-two'
    store = next((manager.root / 'credentials').glob('*.json'))
    saved = json.loads(store.read_text()); saved['expiresAt'] = 1; store.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="account|Account"):
        await manager.connect('remote', reconnect=True)
    assert row['account']['candidate']['subject'] == 'principal-two'
    assert remote.calls == 0 and row['connectionState'] == 'account-review'
    remote.identity_override = {'schemaVersion':1, 'issuer':'https://wrong.example/', 'subject':'principal-two'}
    with pytest.raises(ValueError, match="account|Account"):
        await manager.connect('remote', reconnect=True)
    assert row['account']['status'] == 'unconfirmed'
    assert row['accountBinding']['subject'] == 'fixture-user'
    assert remote.calls == 0


@pytest.mark.parametrize('changes', [{'issuer':'https://wrong.example/'}, {'subject':''}, {'displayName':'a\nforged'}, {'displayName':'x'*201}, {'schemaVersion':2}, {'schemaVersion':True}])
def test_assertion_is_bounded_and_issuer_bound(changes):
    value = dict(schemaVersion=1, issuer='https://issuer.example/', subject='one', displayName='<script>literal</script>')
    good = dict(contents=[dict(uri=ACCOUNT_URI, mimeType='application/json', text=json.dumps(value))])
    assert parse_result(good, ACCOUNT_URI, value['issuer'])['displayName'] == '<script>literal</script>'
    value.update(changes); good['contents'][0]['text'] = json.dumps(value)
    with pytest.raises(ValueError):
        parse_result(good, ACCOUNT_URI, 'https://issuer.example/')


async def test_sdk_refresh_cannot_dispatch_with_an_unattested_authorization():
    import httpx2
    from amplifier_web.mcp_connection import Connection
    from unittest.mock import AsyncMock
    connection = object.__new__(Connection)
    connection.config = {'url':'https://connector.example/mcp'}
    connection.account_resource_uri = ACCOUNT_URI
    connection.account_authorization = 'Bearer synthetic-one'
    connection._message = AsyncMock()
    def request(token, method='tools/call', params=None):
        return httpx2.Request('POST', connection.config['url'], headers={'Authorization':token},
                              json={'method':method, 'params':params or {'name':'read_record'}})
    await connection._request(request('Bearer synthetic-one'))
    with pytest.raises(AccountReviewRequired, match='request was not sent'):
        await connection._request(request('Bearer synthetic-two'))
    assert connection._message.await_count == 1
    # A new explicit attestation may use refreshed credentials; its result must
    # still pass issuer/subject binding before the transport can use them.
    await connection._request(request('Bearer synthetic-two', 'resources/read', {'uri':ACCOUNT_URI}))
    assert connection.last_identity_authorization == 'Bearer synthetic-two'
    assert connection.account_authorization == 'Bearer synthetic-one'
