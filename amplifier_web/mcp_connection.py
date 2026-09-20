"""MCP SDK transport ownership. No retries of application tool calls."""
from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
import logging
import json
import os
import re
from urllib.parse import urlsplit

_private_diagnostics = ContextVar("smart_tools_private_mcp_diagnostics", default=False)


class PrivateDiagnostics(logging.Filter):
    """SDK wire/validation logs can contain credentials before storage sees them."""
    def filter(self, record):
        if _private_diagnostics.get():
            record.msg = "MCP client diagnostic; inspect the connection's safe operation receipt."
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def protect_diagnostics():
    names = {"mcp.client.auth.oauth2", "mcp.client.streamable_http", "httpx", "httpx2"}
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith(("httpcore.", "httpcore2.", "mcp.client.")))
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(item, PrivateDiagnostics) for item in logger.filters):
            logger.addFilter(PrivateDiagnostics())


class AuthenticationRequired(ValueError):
    pass


class ObservedReadStream:
    """Preserve SDK stream/context behavior while observing transport EOF."""
    def __init__(self, stream, connection):
        self.stream, self.connection = stream, connection

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def receive(self):
        import anyio
        try:
            return await self.stream.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError):
            await self.connection._message(ConnectionError("MCP transport closed"))
            self.connection.task.cancel()
            raise

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)


@asynccontextmanager
async def observed(transport, connection):
    async with transport as (read, write):
        yield ObservedReadStream(read, connection), write


class Connection:
    def __init__(self, config, secrets, changed=None, auth=None, handshake_timeout=30):
        self.config = copy.deepcopy(config)
        self.secrets, self.changed, self.auth = secrets, changed, auth
        self.queue = asyncio.Queue(maxsize=64)
        self.ready = asyncio.get_running_loop().create_future()
        self.handshake_timeout = handshake_timeout
        self.failure = None
        self.challenge = None
        self.catalog_epoch = 0
        self.account_guard = None
        self.account_resource_uri = None
        self.account_authorization = None
        self.last_identity_authorization = None
        self.task = asyncio.create_task(self._run())

    async def _message(self, message):
        data = message.model_dump(mode="json", by_alias=True) if hasattr(message, "model_dump") else {}
        # Both legacy notifications and the SDK's subscription tee are supported.
        if data.get("method") == "notifications/tools/list_changed":
            await self._catalog_changed()
        if isinstance(message, Exception):
            self.failure = message
            if self.changed:
                await self.changed(self, message)

    async def _catalog_changed(self):
        self.catalog_epoch += 1
        if self.changed:
            await self.changed(self, "catalog")

    async def _response(self, response):
        if response.status_code in (401, 403):
            challenge = response.headers.get("www-authenticate", "")[:4000]
            scopes = re.search(r'\bscope="([^"\r\n]*)"', challenge)
            self.challenge = {"status": response.status_code,
                              "requestedScopes": scopes.group(1).split()[:50] if scopes else [],
                              "consent": "required", "account": "unknown"}
        elif response.is_success and str(response.request.url) == self.config.get("url"):
            self.challenge = None

    async def _request(self, request):
        # Apply the same secure endpoint rule to SDK-discovered OAuth endpoints.
        # Never send an authorization code/token to a cleartext remote endpoint.
        parsed = urlsplit(str(request.url))
        if parsed.username or parsed.password or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("MCP authorization endpoints require HTTPS except on loopback.")
        if getattr(self, 'account_resource_uri', None) and request.method == 'POST' and str(request.url) == self.config.get('url'):
            try:
                body = json.loads(request.content)
            except (ValueError, TypeError):
                return
            if not isinstance(body, dict):
                return
            authorization = request.headers.get('authorization')
            if body.get('method') == 'resources/read' and body.get('params', {}).get('uri') == self.account_resource_uri:
                self.last_identity_authorization = authorization
            elif body.get('method') in {'tools/call', 'resources/read'} and authorization != self.account_authorization:
                # SDK refresh may happen after the identity read or following a
                # challenge. Do not let its retry dispatch under an unconfirmed grant.
                from .mcp_account import AccountReviewRequired
                error = AccountReviewRequired('Authorization changed after account verification. Reconnect to confirm the current account; the request was not sent under the new authorization.')
                await self._message(error)
                raise error

    async def _listen(self, client):
        from mcp.client.subscriptions import ListenNotSupportedError
        try:
            async with client.listen(tools_list_changed=True) as subscription:
                async for _ in subscription:
                    # The SDK tees the event to message_handler before delivery.
                    pass
            await self._catalog_changed()
        except ListenNotSupportedError:
            pass
        except Exception:
            # If change observation is lost, cached schemas no longer suffice.
            await self._catalog_changed()

    async def _run(self):
        from mcp import Client, StdioServerParameters
        from mcp.client.extension import advertise
        from mcp.client.stdio import stdio_client
        from .smart_tools import APP_MIME, _json
        current = listener = None
        protect_diagnostics()
        diagnostic_scope = _private_diagnostics.set(True)
        try:
            async with AsyncExitStack() as stack:
                if self.config.get("transport", "stdio") == "streamable-http":
                    import httpx2
                    from mcp.client.streamable_http import streamable_http_client
                    protect_diagnostics()
                    http = await stack.enter_async_context(httpx2.AsyncClient(
                        headers=self.secrets, auth=self.auth, timeout=30, trust_env=False,
                        event_hooks={"request": [self._request], "response": [self._response]}, follow_redirects=False))
                    transport = streamable_http_client(self.config["url"], http_client=http)
                else:
                    errlog = stack.enter_context(open(os.devnull, "w"))
                    transport = stdio_client(StdioServerParameters(command=self.config["command"],
                        args=self.config.get("args", []), env=self.secrets,
                        cwd=self.config.get("cwd") or None), errlog=errlog)
                client = await stack.enter_async_context(Client(observed(transport, self), read_timeout_seconds=self.handshake_timeout,
                    extensions=[advertise("io.modelcontextprotocol/ui", {"mimeTypes": [APP_MIME]}),
                                advertise("io.amplifier/account-identity", {"version": 1})],
                    message_handler=self._message, cache=None))
                instructions = client.instructions or ""
                self.ready.set_result({"protocolVersion": client.protocol_version,
                    "serverInfo": _json(client.server_info), "capabilities": _json(client.server_capabilities),
                    "instructions": instructions[:16_000], "instructionsTruncated": len(instructions) > 16_000})
                if _json(client.server_capabilities).get("tools", {}).get("listChanged"):
                    listener = asyncio.create_task(self._listen(client))
                try:
                    while True:
                        method, args, kwargs, current, expected_epoch = await self.queue.get()
                        if current.cancelled():
                            continue
                        try:
                            if expected_epoch is not None and expected_epoch != self.catalog_epoch:
                                raise ValueError("The tool catalog changed before execution. Refresh discovery and review the current schema.")
                            if self.account_guard and method in {"call_tool", "read_resource"}:
                                await self.account_guard(client)
                            result = await getattr(client, method)(*args, **kwargs)
                            if not current.done():
                                current.set_result(_json(result))
                        except asyncio.CancelledError:
                            if not current.done():
                                current.set_exception(ValueError("The MCP connection closed. The tool outcome is unconfirmed; work was not replayed."))
                            raise
                        except Exception as exc:
                            from .mcp_account import AccountReviewRequired
                            if isinstance(exc, AccountReviewRequired):
                                if not current.done():
                                    current.set_exception(exc)
                                await self._message(exc)
                                return
                            from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT
                            from mcp.shared.exceptions import MCPError
                            if isinstance(exc, MCPError) and exc.code in {CONNECTION_CLOSED, REQUEST_TIMEOUT}:
                                if not current.done():
                                    current.set_exception(ValueError("The MCP connection closed or timed out. The tool outcome is unconfirmed; work was not replayed."))
                                await self._message(exc)
                                return
                            if not current.done():
                                current.set_exception(AuthenticationRequired("Authorization is required. Review this connection's sign-in or credential settings.") if self.challenge else exc)
                        finally:
                            current = None
                finally:
                    if listener:
                        listener.cancel()
                        await asyncio.gather(listener, return_exceptions=True)
        except BaseException as exc:
            self.failure = exc
            if not self.ready.done():
                error = AuthenticationRequired("Authorization is required. Review this connection's sign-in or credential settings.") if self.challenge else ValueError("The MCP server could not start or complete its connection handshake.")
                self.ready.set_exception(error)
            if current and not current.done():
                current.set_exception(ValueError("The MCP connection closed. Work was not replayed."))
        finally:
            while not self.queue.empty():
                pending = self.queue.get_nowait()
                if not pending[3].done():
                    pending[3].set_exception(ValueError("The MCP connection closed. Work was not replayed."))
            if self.changed:
                await self.changed(self, "closed")
            _private_diagnostics.reset(diagnostic_scope)

    async def request(self, method, *args, timeout=60, expected_epoch=None, **kwargs):
        if self.task.done() or self.failure:
            raise ValueError("The MCP connection is closed. Reconnect before sending another request.")
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((method, args, kwargs, future, expected_epoch))
        except asyncio.QueueFull:
            raise ValueError("This tool has too many pending requests. Try again when it finishes.") from None
        try:
            return await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            await self.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ValueError("The tool timed out. It may have started work; it was not replayed. Reconnect and inspect the tool's state.") from None

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
