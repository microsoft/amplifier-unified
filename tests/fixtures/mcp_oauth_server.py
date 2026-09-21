"""Local OAuth + MCP fixture. Real SDK servers, synthetic credentials only."""
import asyncio
import json
import secrets
import socket
import time
from urllib.parse import urlencode

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.extension import Extension
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthToken
from starlette.responses import HTMLResponse, RedirectResponse
import uvicorn


ACCOUNT_URI = "amplifier-account://current"


class AccountExtension(Extension):
    identifier = "io.amplifier/account-identity"

    def settings(self):
        return {"version": 1, "resourceUri": ACCOUNT_URI}


class Provider:
    def __init__(self, origin):
        self.origin = origin
        self.clients, self.pending, self.codes, self.access, self.refresh = {}, {}, {}, {}, {}
        self.exchanges = self.refreshes = 0
        self.principal = "fixture-user"

    async def get_client(self, identity):
        return self.clients.get(identity)

    async def register_client(self, info):
        self.clients[info.client_id] = info

    async def authorize(self, client, params):
        assert params.resource == self.origin + "/mcp"
        assert params.code_challenge
        pending = secrets.token_urlsafe(24)
        self.pending[pending] = (client, params)
        return self.origin + "/consent?request=" + pending

    async def load_authorization_code(self, client, code):
        return self.codes.get(code)

    def mint(self, client_id, scopes, resource):
        token, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[token] = AccessToken(token=token, client_id=client_id, scopes=scopes,
            expires_at=int(time.time())+3600, resource=resource, subject=self.principal)
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes, resource=resource)
        return OAuthToken(access_token=token, refresh_token=refresh, expires_in=3600, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, code):
        assert self.codes.pop(code.code)
        self.exchanges += 1
        return self.mint(client.client_id, code.scopes, code.resource)

    async def load_refresh_token(self, client, token):
        return self.refresh.get(token)

    async def exchange_refresh_token(self, client, token, scopes):
        self.refresh.pop(token.token)
        self.refreshes += 1
        return self.mint(client.client_id, scopes, token.resource)

    async def load_access_token(self, token):
        return self.access.get(token)

    async def revoke_token(self, token):
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)


class Fixture:
    def __init__(self, *, identity=False):
        self.identity_override = None
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.provider = Provider(self.origin)
        self.calls = 0
        self.mcp = MCPServer("Local account fixture", auth_server_provider=self.provider,
            extensions=[AccountExtension()] if identity else [],
            auth=AuthSettings(issuer_url=self.origin, resource_server_url=self.origin + "/mcp",
                validate_token_resource=True, required_scopes=["records:read"],
                client_registration_options=ClientRegistrationOptions(enabled=True,
                    valid_scopes=["records:read"], default_scopes=["records:read"])))

        if identity:
            @self.mcp.resource(ACCOUNT_URI, mime_type="application/json")
            def account_identity() -> str:
                token = get_access_token()
                assert token is not None
                value = {"schemaVersion": 1, "issuer": self.origin,
                         "subject": token.subject, "displayName": "Fixture " + token.subject}
                return json.dumps(self.identity_override if self.identity_override is not None else value)

        @self.mcp.tool(structured_output=True)
        def read_record(key: str) -> dict[str, str]:
            """Read one fixture record by its key."""
            self.calls += 1
            return {"key":key, "value":"fixture data"}

        @self.mcp.tool()
        async def change_tools(ctx: Context) -> str:
            """Announce a changed tool list without silently reloading schemas."""
            await ctx.notify_tools_changed()
            return "changed"

        @self.mcp.custom_route("/consent", methods=["GET", "POST"])
        async def consent(request):
            key = request.query_params.get("request")
            if key not in self.provider.pending:
                return HTMLResponse("Expired request", status_code=400)
            if request.method == "GET":
                return HTMLResponse('<!doctype html><h1>Local fixture consent</h1><p>Grant records:read to Amplifier Unified?</p><form method="post"><button name="decision" value="approve">Approve fixture access</button><button name="decision" value="deny">Deny fixture access</button></form>')
            form = await request.form()
            client, params = self.provider.pending.pop(key)
            result = {"state":params.state, "iss":self.origin}
            if form.get("decision") == "approve":
                code = secrets.token_urlsafe(32)
                self.provider.codes[code] = AuthorizationCode(code=code, scopes=params.scopes or [],
                    expires_at=time.time()+60, client_id=client.client_id, code_challenge=params.code_challenge,
                    redirect_uri=params.redirect_uri, redirect_uri_provided_explicitly=True, resource=params.resource)
                result["code"] = code
            else:
                result["error"] = "access_denied"
            return RedirectResponse(str(params.redirect_uri) + "?" + urlencode(result), status_code=303)

    async def start(self):
        self.server = uvicorn.Server(uvicorn.Config(self.mcp.streamable_http_app(), log_level="critical", access_log=False, lifespan="on", timeout_graceful_shutdown=1))
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(200):
            if self.server.started:
                return self
            if self.task.done():
                await self.task
            await asyncio.sleep(.01)
        raise TimeoutError("Local MCP fixture did not start")

    async def close(self):
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 5)
