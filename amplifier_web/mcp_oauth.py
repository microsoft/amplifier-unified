"""Explicit MCP authorization using the SDK's OAuth protocol implementation.

Only the existing private-file writer handles credentials. App state contains
connection progress, consent scopes and an authorization link, never tokens.
"""
from __future__ import annotations

import asyncio
import copy
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from .deployment import validate_origin, write_private
from .mcp_connection import AuthenticationRequired

CALLBACK_PATH = "/oauth/mcp/callback"


class Storage:
    """SDK TokenStorage; files are bound to the exact connection configuration."""
    def __init__(self, owner, identity):
        from .smart_tools import configuration_key
        self.owner, self.identity = owner, identity
        self.key = configuration_key(owner.manager._server(identity))
        self.path = owner.manager.root / "credentials" / (self.key + ".json")
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        from .smart_tools import configuration_key
        if configuration_key(self.owner.manager._server(self.identity)) != self.key:
            raise ValueError("The connection changed during authorization.")
        encoded = json.dumps(self.data)
        if len(encoded.encode()) > 256_000:
            raise ValueError("The authorization server returned oversized credential metadata.")
        write_private(self.path, encoded)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = copy.deepcopy(self.data.get("tokens"))
        if data is None:
            return None
        if self.data.get("expiresAt") is not None:
            data["expires_in"] = max(-1, int(self.data["expiresAt"] - time.time()))
        return OAuthToken.model_validate(data)

    async def set_tokens(self, tokens):
        self.data.update(tokens=tokens.model_dump(mode="json"), expiresAt=time.time() + tokens.expires_in if tokens.expires_in is not None else None)
        context = getattr(self, "context", None)
        if context is not None:
            self.data.update(oauthMetadata=context.oauth_metadata.model_dump(mode="json") if context.oauth_metadata else None,
                             protectedResource=context.protected_resource_metadata.model_dump(mode="json") if context.protected_resource_metadata else None,
                             authServerUrl=context.auth_server_url)
        self.save()
        await self.owner.manager._change(lambda _: self.owner.manager._server(self.identity).update(
            authorization={"consent":"granted", "grantedScopes":tokens.scope.split()[:50] if tokens.scope else None,
                           "requestedScopes": self.owner.sessions.get(self.identity, {}).get("public", {}).get("requestedScopes", []),
                           "source":"oauth-token-response"}))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        return OAuthClientInformationFull.model_validate(self.data["client"]) if self.data.get("client") else None

    async def set_client_info(self, info):
        self.data["client"] = info.model_dump(mode="json")
        self.save()


class Authorization:
    def __init__(self, manager):
        self.manager, self.sessions = manager, {}

    def status(self, identity):
        self.manager._server(identity)
        return {**copy.deepcopy(self.sessions.get(identity, {}).get("public", {"id":identity, "phase":"idle"})),
                "account": copy.deepcopy(self.manager._server(identity).get("account", {"status":"unknown"}))}

    def origin(self, value=None):
        config = getattr(self.manager.service, "server_config", {})
        port = config.get("port", getattr(self.manager.service, "port", 8941))
        allowed = set(config.get("public_origins", [])) | {f"{scheme}://{host}:{port}" for scheme in ("http", "https") for host in ("127.0.0.1", "localhost", "[::1]")}
        scheme = "https" if config.get("tls", {}).get("method", "none") != "none" else "http"
        selected = validate_origin(value or next(iter(config.get("public_origins", [])), f"{scheme}://127.0.0.1:{port}"))
        if selected not in {validate_origin(v) for v in allowed}:
            raise ValueError("Use this app's configured origin for the authorization callback.")
        if urlsplit(selected).scheme != "https" and urlsplit(selected).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote authorization callbacks require HTTPS.")
        return selected

    async def provider(self, identity, interactive=False, origin=None):
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
        row = self.manager._server(identity)
        if row.get("transport") != "streamable-http" or row.get("auth") != "oauth":
            raise ValueError("Configure this connection for Streamable HTTP and OAuth first.")
        storage = Storage(self, identity)
        if not interactive and not storage.data.get("tokens"):
            await self.manager._change(lambda _: row.update(status="auth-required", connectionState="auth-required", error="Sign in explicitly to authorize this connection."))
            raise AuthenticationRequired("Sign in explicitly to authorize this connection.")
        redirect_uri = self.origin(origin) + CALLBACK_PATH if interactive else storage.data.get("redirectUri", self.origin() + CALLBACK_PATH)
        if interactive and storage.data.get("redirectUri") != redirect_uri:
            storage.data.pop("client", None)
        storage.data["redirectUri"] = redirect_uri

        async def redirect(url):
            if not interactive:
                raise AuthenticationRequired("Authorization expired or changed. Start sign-in explicitly.")
            parsed = urlsplit(url)
            # The SDK chooses/discovers the endpoint and verifies issuer/resource.
            # Host presentation additionally rejects unsafe schemes/credentials.
            if len(url) > 16_000 or parsed.username or parsed.password or parsed.fragment or any(c.isspace() for c in url) or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})):
                raise ValueError("The authorization server supplied an unsafe browser URL.")
            query = parse_qs(parsed.query)
            state = query.get("state", [None])[0]
            if not state or query.get("redirect_uri") != [redirect_uri] or query.get("code_challenge_method") != ["S256"]:
                raise ValueError("The authorization redirect is not bound to this login.")
            session = self.sessions[identity]
            session["state"] = state
            session["origin"] = self.origin(origin)
            await self.publish(identity, phase="waiting", url=url, authorizationServer=f"{parsed.scheme}://{parsed.netloc}",
                               requestedScopes=query.get("scope", [""])[0].split()[:50])
            await self.manager._change(lambda _: row.update(status="auth-required", connectionState="auth-required"))

        async def callback():
            return await self.sessions[identity]["callback"]

        provider = OAuthClientProvider(row["url"], OAuthClientMetadata(client_name="Amplifier Unified",
            redirect_uris=[redirect_uri], token_endpoint_auth_method="none", application_type="web"),
            storage, redirect_handler=redirect, callback_handler=callback)
        # MCP 2.2 TokenStorage carries OAuthToken but no acquired-at timestamp or
        # discovery state. Restore the SDK context so restart cannot extend token
        # lifetime or send a refresh to a guessed server-origin token endpoint.
        from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
        provider.context.token_expiry_time = storage.data.get("expiresAt")
        if storage.data.get("oauthMetadata"):
            provider.context.oauth_metadata = OAuthMetadata.model_validate(storage.data["oauthMetadata"])
        if storage.data.get("protectedResource"):
            provider.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(storage.data["protectedResource"])
        provider.context.auth_server_url = storage.data.get("authServerUrl")
        storage.context = provider.context
        return provider

    async def publish(self, identity, **fields):
        session = self.sessions[identity]
        session["public"].update(fields, updatedAt=time.time())
        await self.manager._change(lambda _: self.manager._server(identity).update(login=copy.deepcopy(session["public"])))

    async def start(self, identity, origin=None):
        previous = self.sessions.get(identity)
        if previous and not previous["task"].done():
            return self.status(identity)
        self.origin(origin)
        session = {"callback":asyncio.get_running_loop().create_future(),
                   "public":{"id":identity, "loginId":uuid.uuid4().hex, "phase":"starting", "expiresAt":time.time()+600}}
        self.sessions[identity] = session

        async def run():
            try:
                provider = await self.provider(identity, interactive=True, origin=origin)
                await self.manager.connect(identity, reconnect=True, auth=provider, auth_timeout=600)
                await self.publish(identity, phase="ready", url=None)
            except asyncio.CancelledError:
                await self.publish(identity, phase="cancelled", url=None)
                raise
            except Exception:
                # SDK exceptions may contain authorization response bodies.
                await self.publish(identity, phase="error", url=None,
                    error="Sign-in did not complete. Check the server's OAuth support or configure its documented credentials.")
            finally:
                session.pop("state", None)
                future = session["callback"]
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()
                session.pop("callback", None)
        session["task"] = asyncio.create_task(run())
        await self.publish(identity)
        return self.status(identity)

    async def callback(self, query, origin):
        from mcp.shared.auth import AuthorizationCodeResult
        if any(len(query.getall(k, [])) > 1 for k in ("state", "code", "iss", "error")):
            raise ValueError("The authorization callback is invalid.")
        state = query.get("state", "")
        if not isinstance(state, str) or not 1 <= len(state) <= 1000:
            raise ValueError("The authorization callback is invalid or expired.")
        session = next((s for s in self.sessions.values() if s.get("state") and secrets.compare_digest(s["state"], state)), None)
        if not session or session["callback"].done() or session["public"]["expiresAt"] < time.time() or session["origin"] != origin:
            raise ValueError("The authorization callback is invalid or expired.")
        session.pop("state", None)  # one callback, including provider denial
        if query.get("error"):
            session["callback"].set_exception(AuthenticationRequired("The user did not grant authorization."))
        else:
            code, issuer = query.get("code", ""), query.get("iss")
            if not 1 <= len(code) <= 8000 or issuer and len(issuer) > 2000:
                session["callback"].set_exception(ValueError("The authorization callback is invalid."))
                raise ValueError("The authorization callback is invalid.")
            session["callback"].set_result(AuthorizationCodeResult(code=code, state=state, iss=issuer))
        return "Authorization response received. Return to Amplifier Unified to check the connection."

    async def cancel(self, identity):
        session = self.sessions.get(identity)
        if session and not session["task"].done():
            session["task"].cancel()
            await asyncio.gather(session["task"], return_exceptions=True)
        return self.status(identity)

    async def forget(self, identity):
        await self.cancel(identity)
        await self.manager.disconnect(identity)
        Storage(self, identity).path.unlink(missing_ok=True)
        self.sessions.pop(identity, None)
        row = self.manager._server(identity)
        await self.manager.accounts.changed(row, {"status": "unconfirmed" if row.get("accountBinding") else "unknown", **({"expected": copy.deepcopy(row["accountBinding"]), "detail": "Local sign-in was removed. The previously accepted account remains bound; sign in explicitly to confirm it."} if row.get("accountBinding") else {})})
        await self.manager._change(lambda _: row.update(authorization={"consent":"unknown", "requestedScopes":[], "grantedScopes":None}, login={"id":identity,"phase":"idle"}))
        return {"id":identity, "localCredentials":"removed", "remoteRevocation":"not-attempted"}

    def secret_values(self):
        result = set()
        for row in self.manager.state["servers"]:
            if row.get("auth") != "oauth":
                continue
            data = Storage(self, row["id"]).data
            for field in ("tokens", "client"):
                result.update(v for k, v in data.get(field, {}).items() if k in {"access_token", "refresh_token", "client_secret"} and isinstance(v, str) and v)
        return result

    async def close(self):
        await asyncio.gather(*(self.cancel(identity) for identity in list(self.sessions)), return_exceptions=True)


from aiohttp.web_log import AccessLogger


class SafeAccessLogger(AccessLogger):
    @staticmethod
    def _format_r(request, response, elapsed):
        # OAuth callbacks carry one-time codes in the query. Never put them (or
        # a Referer containing them) in the CLI server's access log.
        return f"{request.method} {request.path} HTTP/{request.version.major}.{request.version.minor}"
