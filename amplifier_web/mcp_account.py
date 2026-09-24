"""Optional connector-attested account identity; never decode access tokens."""
from __future__ import annotations

import asyncio
import copy
import json
import time

CAPABILITY = 'io.amplifier/account-identity'
MAX_BYTES = 8192


class AccountReviewRequired(ValueError):
    pass


def identity(value):
    return (value.get('issuer'), value.get('subject')) if value else None


def parse_result(result, uri, issuer):
    contents = result.get('contents', [])
    if len(contents) != 1 or contents[0].get('uri') != uri or contents[0].get('mimeType') != 'application/json':
        raise ValueError('The account resource must return one exact JSON resource.')
    text = contents[0].get('text')
    if not isinstance(text, str) or len(text.encode()) > MAX_BYTES:
        raise ValueError('The account resource exceeds its text bound.')
    value = json.loads(text)
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value.get('schemaVersion') != 1 or value.get('issuer') != issuer:
        raise ValueError('The account assertion does not match the authenticated authorization issuer.')
    for name, maximum in (('issuer', 2000), ('subject', 500), ('displayName', 200)):
        field = value.get(name)
        if name == 'displayName' and field is None:
            continue
        if not isinstance(field, str) or not field or len(field.encode()) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in field):
            raise ValueError('The account assertion contains an invalid identity field.')
    return {key: value[key] for key in ('issuer', 'subject', 'displayName') if key in value}


class Accounts:
    def __init__(self, manager):
        self.manager = manager

    async def changed(self, row, observation):
        previous = row.get('account', {})
        # Repeated observations of the same account do not invalidate an exact
        # pending review. Display names are not part of principal identity.
        different = (previous.get('status'), identity(previous), identity(previous.get('candidate'))) != (
            observation.get('status'), identity(observation), identity(observation.get('candidate')))
        revision = row.get('accountRevision', 0) + int(different)
        await self.manager._change(lambda _: row.update(account={**observation, 'revision': revision}, accountRevision=revision))

    async def bind(self, row, connection, info):
        advertised = info.get('capabilities', {}).get('extensions', {}).get(CAPABILITY)
        context = getattr(connection.auth, 'context', None)
        metadata = getattr(context, 'oauth_metadata', None)
        tokens = getattr(context, 'current_tokens', None)
        supported = advertised is not None and row.get('transport') == 'streamable-http' and row.get('auth') == 'oauth' and metadata is not None and tokens is not None
        if not supported:
            await self.unavailable(row, 'This connector does not supply a supported authenticated account identity.')
            return
        if not isinstance(advertised, dict) or type(advertised.get('version')) is not int or advertised.get('version') != 1:
            await self.unavailable(row, 'The connector account identity version is unsupported.')
            return
        uri = advertised.get('resourceUri')
        if not isinstance(uri, str) or not uri or len(uri.encode()) > 2000 or any(ord(c) < 33 for c in uri):
            raise ValueError('The connector advertised an invalid account resource.')
        issuer = str(metadata.issuer)
        connection.account_resource_uri = uri

        async def verify(client=None):
            try:
                if client is None:
                    result = await connection.request('read_resource', uri, timeout=15)
                else:
                    from .smart_tools import _json
                    result = _json(await client.read_resource(uri))
                current = parse_result(result, uri, issuer)
            except Exception:
                await self.unavailable(row, 'The connector could not attest the current account identity.')
                raise AccountReviewRequired('Account identity could not be confirmed; tools remain disconnected.') from None
            connection.account_authorization = connection.last_identity_authorization
            current.update(provenance={'kind': 'authenticated-connector-resource', 'endpoint': row['url'],
                                       'resourceUri': uri, 'issuerSource': 'mcp-sdk-validated-oauth-metadata'},
                           observedAt=time.time())
            expected = row.get('accountBinding')
            if expected and identity(expected) != identity(current):
                await self.changed(row, {'status': 'changed', 'expected': copy.deepcopy(expected), 'candidate': current,
                                        'detail': 'The connector reports a different account. Review this identity before reconnecting.'})
                raise AccountReviewRequired('The connected account changed. Review and explicitly accept it before reconnecting.')
            if not expected:
                await self.manager._change(lambda _: row.update(accountBinding=copy.deepcopy(current)))
            if connection.observation_active and expected and row.get('account', {}).get('status') == 'verified':
                # The exact principal was just re-attested. Quiet reads retain
                # that evidence in their occurrence, not a timestamp-only app update.
                return
            await self.changed(row, {'status': 'verified', **current,
                                    'verification': 'Server-attested through authenticated transport; not independent identity verification.'})

        await verify()
        # The existing serial SDK request owner rechecks identity immediately
        # before any queued tool/resource read, after previous requests settle.
        connection.account_guard = verify

    async def unavailable(self, row, detail):
        expected = row.get('accountBinding')
        await self.changed(row, {'status': 'unconfirmed' if expected else 'unknown', 'detail': detail,
                                **({'expected': copy.deepcopy(expected)} if expected else {})})
        if expected:
            raise AccountReviewRequired('The previously bound account cannot be confirmed; tools remain disconnected.')

    async def accept(self, args, origin):
        manager, sid = self.manager, args['id']
        async with manager.connection_locks.setdefault(sid, asyncio.Lock()):
            row = manager._server(sid)
            account = row.get('account', {})
            candidate = account.get('candidate')
            if (account.get('status') != 'changed' or not candidate or
                    type(args.get('expectedRevision')) is not int or args['expectedRevision'] != row.get('accountRevision') or
                    identity(args) != identity(candidate)):
                raise ValueError('The account review changed. Inspect the current account before accepting it.')
            await manager._disconnect(sid)
            decision = {'previous': copy.deepcopy(row.get('accountBinding')), 'accepted': copy.deepcopy(candidate),
                        'reviewRevision': args['expectedRevision'], 'origin': origin, 'acceptedAt': time.time()}
            await manager._change(lambda _: row.update(accountBinding=copy.deepcopy(candidate), lastAccountDecision=decision))
            await self.changed(row, {'status': 'accepted', **copy.deepcopy(candidate),
                                    'detail': 'Account change accepted. Reconnect explicitly to confirm it; no tools were called.'})
            return {'id': sid, 'account': copy.deepcopy(row['account']), 'decision': decision, 'connected': False}
