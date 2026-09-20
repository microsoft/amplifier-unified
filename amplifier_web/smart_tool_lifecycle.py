"""Lifecycle and bounded discovery over the existing Smart Tools registrations."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import uuid

import jsonschema

from .mcp_connection import AuthenticationRequired, Connection

HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,99}$")


def endpoint(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        raise ValueError("Enter a credential-free HTTPS MCP endpoint (HTTP is allowed only on loopback).")
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("Enter a credential-free HTTPS MCP endpoint (HTTP is allowed only on loopback).")
    return value


def summary(tool):
    ui = tool.get("_meta", {}).get("ui", {})
    annotations = tool.get("annotations", {})
    return {"name":tool["name"], "title":str(tool.get("title") or annotations.get("title") or "")[:200],
            "description":str(tool.get("description", ""))[:600],
            "annotations":{k:v for k,v in annotations.items() if k in {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"} and isinstance(v, bool)},
            "_meta":{"ui":{**({"resourceUri":ui["resourceUri"]} if isinstance(ui.get("resourceUri"), str) and len(ui["resourceUri"].encode()) <= 4000 else {}),
                             "visibility":[v for v in ui.get("visibility", ["model", "app"]) if v in {"model", "app"}][:2]}}}


class Lifecycle:
    async def configure(self, args):
        from .smart_tools import ENV_NAME, configuration_key
        identity = args.get("id") or uuid.uuid4().hex
        transport = args.get("transport", "stdio")
        if transport not in {"stdio", "streamable-http"}:
            raise ValueError("Use stdio or streamable-http transport.")
        command, argv, cwd = args.get("command", ""), args.get("args", []), args.get("cwd")
        if transport == "stdio" and (not isinstance(command, str) or not command.strip() or len(command) > 4000 or "\x00" in command):
            raise ValueError("Choose the MCP server executable.")
        if not isinstance(argv, list) or len(argv) > 100 or any(not isinstance(arg, str) or len(arg) > 8000 or "\x00" in arg for arg in argv):
            raise ValueError("Arguments must be a list of text values.")
        references = {}
        for field in ("env", "headers"):
            values = args.get(field, {})
            names = ENV_NAME if field == "env" else HEADER
            if not isinstance(values, dict) or len(values) > 50 or any(not isinstance(k, str) or not isinstance(v, str) or not names.fullmatch(k) or not ENV_NAME.fullmatch(v) for k, v in values.items()):
                raise ValueError("Credential configuration maps variable names or header names to existing environment variable names; do not paste secret values.")
            if field == "headers" and any(k.lower() in {"host", "content-length", "connection", "cookie", "mcp-session-id", "mcp-protocol-version"} for k in values):
                raise ValueError("This protocol header cannot be configured.")
            if len({k.lower() for k in values}) != len(values):
                raise ValueError("Header names must be unique ignoring case.")
            references[field] = values
        if cwd:
            cwd = str(Path(cwd).expanduser().resolve())
            if not Path(cwd).is_dir():
                raise ValueError("The server working folder does not exist.")
        auth = args.get("auth", "environment")
        if auth not in {"environment", "oauth"} or auth == "oauth" and transport != "streamable-http":
            raise ValueError("OAuth requires a Streamable HTTP endpoint.")
        if auth == "oauth" and references["headers"]:
            raise ValueError("Use OAuth or environment header references, not both.")
        row = {"id": identity, "name": str(args.get("name") or Path(command).name)[:120],
               "transport": transport, "command": command if transport == "stdio" else "",
               "args": argv if transport == "stdio" else [], "env": references["env"] if transport == "stdio" else {},
               "cwd": cwd if transport == "stdio" else None, "headers": references["headers"] if transport != "stdio" else {},
               "url": endpoint(args.get("url")) if transport != "stdio" else None, "auth": auth,
               "installationId": args.get("installationId"), "status": "disconnected", "connectionState": "disconnected",
               "tools": [], "loadedSchemas": {}, "catalogState": "stale", "uiCapable": False,
               "account": {"status": "unknown"}, "authorization": {"consent": "unknown", "requestedScopes": [], "grantedScopes": None},
               "updatedAt": time.time()}
        def save(state):
            state["servers"] = [item for item in state["servers"] if item["id"] != identity] + [row]
            return row
        if row["installationId"] and not any(p["id"] == row["installationId"] for p in self.state["installations"]):
            raise ValueError("The selected installation no longer exists.")
        previous = next((item for item in self.state["servers"] if item["id"] == identity), None)
        if previous:
            for field in ("accountBinding", "accountRevision", "lastAccountDecision"):
                if field in previous:
                    row[field] = copy.deepcopy(previous[field])
            if row.get('accountBinding'):
                row['account'] = {'status': 'unconfirmed', 'expected': copy.deepcopy(row['accountBinding']), 'revision': row.get('accountRevision', 0)}
            if configuration_key(previous) != configuration_key(row):
                await self.oauth.forget(identity)
            else:
                await self.oauth.cancel(identity)
                row["authorization"] = copy.deepcopy(previous.get("authorization", row["authorization"]))
        async with self.install_lock:
            if row["installationId"] and not any(p["id"] == row["installationId"] for p in self.state["installations"]):
                raise ValueError("The selected installation no longer exists.")
            async with self.connection_locks.setdefault(identity, asyncio.Lock()):
                if any(item["id"] == identity for item in self.state["servers"]):
                    await self._disconnect(identity)
                self.schemas.pop(identity, None)
                return await self._change(save)

    async def _connection_changed(self, connection, event):
        identity = connection.config["id"]
        if self.connections.get(identity) is not connection or self.closed:
            return
        row = self._server(identity)
        if event == "catalog":
            self.schemas.pop(identity, None)
            await self._change(lambda _: row.update(catalogState="stale", loadedSchemas={}, updatedAt=time.time()))
        else:
            self.schemas.pop(identity, None)
            from .mcp_account import AccountReviewRequired
            review = isinstance(event, AccountReviewRequired) or isinstance(connection.failure, AccountReviewRequired)
            if review and row.get('account', {}).get('status') == 'verified':
                await self.accounts.changed(row, {'status': 'unconfirmed', 'expected': copy.deepcopy(row.get('accountBinding')), 'detail': 'Authorization changed after account verification. Reconnect explicitly to confirm the account.'})
            await self._change(lambda _: row.update(status="disconnected", connectionState="account-review" if review else "disconnected", catalogState="stale", loadedSchemas={}, error=str(connection.failure) if review else "The connection closed. Reconnect explicitly; previous work was not replayed."))

    async def _refresh(self, identity, connection):
        from .smart_tools import MAX_TOOLS, _bounded, _ui
        epoch, tools, cursor, seen = connection.catalog_epoch, [], None, set()
        for _ in range(20):
            page = await connection.request("list_tools", cursor=cursor, timeout=30)
            tools.extend(page.get("tools", []))
            if len(tools) > MAX_TOOLS:
                raise ValueError("This server exposes too many tools for one connection.")
            _bounded(tools, 1_000_000)
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("Tool discovery returned a repeated page cursor.")
            seen.add(cursor)
        else:
            raise ValueError("Tool discovery did not finish after 20 pages.")
        if epoch != connection.catalog_epoch or self.connections.get(identity) is not connection:
            raise ValueError("The tool catalog changed while it was loading. Refresh discovery again.")
        names = [tool.get("name") for tool in tools]
        if any(not isinstance(name, str) or not name or len(name) > 200 for name in names) or len(set(names)) != len(names):
            raise ValueError("The server returned invalid or duplicate tool names.")
        self.schemas[identity] = self._redact(tools)
        revision = uuid.uuid4().hex
        await self._change(lambda _: self._server(identity).update(tools=[summary(t) for t in self.schemas[identity]],
            loadedSchemas={}, catalogRevision=revision, catalogState="current", catalogEpoch=epoch,
            catalogCheckedAt=time.time(), toolCount=len(tools), uiCapable=any(_ui(tool).get("resourceUri") for tool in tools)))

    async def connect(self, identity, reconnect=False, *, auth=None, auth_timeout=30):
        from .smart_tools import _bounded
        async with self.connection_locks.setdefault(identity, asyncio.Lock()):
            row = self._server(identity)
            connection = self.connections.get(identity)
            if connection and not connection.task.done() and not connection.failure and not reconnect:
                return copy.deepcopy(row)
            await self._disconnect(identity)
            secrets = {}
            for name, source in row.get("headers" if row.get("transport") == "streamable-http" else "env", {}).items():
                if not os.environ.get(source):
                    message = f"Environment variable {source} is not set. Set it before connecting."
                    await self._change(lambda _: row.update(status="auth-required", connectionState="auth-required", error=message))
                    raise AuthenticationRequired(message)
                secrets[name] = os.environ[source]
            if row.get("auth") == "oauth" and auth is None:
                auth = await self.oauth.provider(identity, interactive=False)
            await self._change(lambda _: row.update(status="connecting", connectionState="connecting", error=None))
            connection = Connection(row, secrets, self._connection_changed, auth, auth_timeout)
            self.connections[identity] = connection
            try:
                info = self._redact(_bounded(await asyncio.wait_for(asyncio.shield(connection.ready), auth_timeout), 128_000))
                await self.accounts.bind(row, connection, info)
                await self._refresh(identity, connection)
                await self._change(lambda _: row.update(status="connected", connectionState="ready", **info, error=None, lastVerifiedAt=time.time(), updatedAt=time.time()))
                return copy.deepcopy(row)
            except BaseException as exc:
                self.connections.pop(identity, None)
                await connection.close()
                if connection.ready.done() and not connection.ready.cancelled():
                    connection.ready.exception()
                from .mcp_account import AccountReviewRequired
                phase = "account-review" if isinstance(exc, AccountReviewRequired) else "disconnected" if isinstance(exc, asyncio.CancelledError) else "auth-required" if isinstance(exc, AuthenticationRequired) else "error"
                message = "The request was interrupted. Work was not replayed." if isinstance(exc, asyncio.CancelledError) else "The MCP server did not become ready within 30 seconds." if isinstance(exc, TimeoutError) else self._redact(str(exc))[:1000]
                await self._change(lambda _: row.update(status=phase, connectionState=phase, catalogState="stale", loadedSchemas={}, error=message,
                    **({"authorization": connection.challenge} if connection.challenge else {})))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ValueError(message or "The MCP connection failed.") from None

    async def disconnect(self, identity):
        await self.oauth.cancel(identity)
        async with self.connection_locks.setdefault(identity, asyncio.Lock()):
            await self._disconnect(identity)

    async def _disconnect(self, identity):
        row = self._server(identity)
        connection = self.connections.pop(identity, None)
        if connection:
            await connection.close()
        self.schemas.pop(identity, None)
        await self._change(lambda _: row.update(status="disconnected", connectionState="disconnected", catalogState="stale", loadedSchemas={}, updatedAt=time.time()))

    async def _connection(self, identity):
        self._server(identity)
        connection = self.connections.get(identity)
        if not connection or connection.task.done() or connection.failure:
            raise ValueError("Connect this tool before using it. Previous requests are never replayed.")
        return connection

    def _catalog(self, identity, revision=None):
        row = self._server(identity)
        if row.get("catalogState") != "current" or identity not in self.schemas or revision is not None and revision != row.get("catalogRevision"):
            raise ValueError("The tool catalog is stale. Refresh discovery and load the current schema before calling.")
        return self.schemas[identity]

    async def list_tools(self, identity, origin="agent"):
        from .smart_tools import _visible
        await self._connection(identity)
        return [copy.deepcopy(tool) for tool in self._catalog(identity) if _visible(tool, origin)]

    async def discover(self, args, origin):
        from .smart_tools import _visible
        identity = args["id"]
        connection = await self._connection(identity)
        if args.get("refresh"):
            async with self.connection_locks.setdefault(identity, asyncio.Lock()):
                await self._refresh(identity, connection)
        tools = self._catalog(identity, args.get("catalogRevision"))
        query = str(args.get("query", "")).casefold().split()[:20]
        matches = [summary(t) for t in tools if _visible(t, origin) and all(term in (t["name"] + " " + t.get("description", "")).casefold() for term in query)]
        offset, limit = args.get("offset", 0), args.get("limit", 10)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("Use a nonnegative offset and a limit between 1 and 25.")
        rows, used = [], 0
        for row in matches[offset:offset + limit]:
            size = len(json.dumps(row).encode())
            if used + size > 16_000:
                break
            rows.append(row); used += size
        return {"id": identity, "catalogRevision": self._server(identity)["catalogRevision"], "tools": rows,
                "total": len(matches), "nextOffset": offset + len(rows) if offset + len(rows) < len(matches) else None}

    async def load_schemas(self, args, origin):
        from .smart_tools import _bounded, _visible
        identity, names = args["id"], args["names"]
        await self._connection(identity)
        tools = self._catalog(identity, args.get("catalogRevision"))
        if not isinstance(names, list) or not 1 <= len(names) <= 5 or any(not isinstance(n, str) for n in names):
            raise ValueError("Load between one and five named tool schemas.")
        selected = [copy.deepcopy(t) for t in tools if t["name"] in names and _visible(t, origin)]
        if len(selected) != len(set(names)):
            raise ValueError("A requested tool is not available to this caller.")
        _bounded(selected, 64_000)
        # Replacement bounds browser state; receipts retain prior requested pages.
        revision = self._server(identity)["catalogRevision"]
        await self._change(lambda _: self._server(identity).update(loadedSchemas={t["name"]:t for t in selected}, loadedSchemaRevision=revision))
        return {"id": identity, "catalogRevision": revision, "tools": selected}

    async def call_tool(self, identity, name, arguments, origin="ui", timeout_seconds=60, allowed_tools=None, expected_configuration=None, expected_catalog=None):
        from .smart_tools import _bounded, _visible, configuration_key
        connection = await self._connection(identity)
        if expected_configuration is not None and configuration_key(self._server(identity)) != expected_configuration:
            raise ValueError("This tool's connection settings changed. Reopen its view before using it.")
        tool = next((t for t in self._catalog(identity, expected_catalog) if t.get("name") == name), None)
        if not tool or not _visible(tool, origin) or (allowed_tools is not None and name not in allowed_tools):
            raise ValueError("This tool is not available to this caller.")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object.")
        _bounded(arguments, 256_000)
        try:
            jsonschema.validate(arguments, tool.get("inputSchema", {"type": "object"}))
        except (jsonschema.ValidationError, jsonschema.SchemaError) as exc:
            raise ValueError(f"The tool arguments do not match its schema: {exc.message[:300]}") from None
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
            raise ValueError("The request timeout must be between 0 and 300 seconds.")
        epoch = connection.catalog_epoch
        try:
            result = await connection.request("call_tool", name, arguments, read_timeout_seconds=timeout_seconds,
                timeout=timeout_seconds + 1, expected_epoch=epoch)
            return self._redact(_bounded(result))
        except AuthenticationRequired:
            await self._change(lambda _: self._server(identity).update(status="auth-required", connectionState="auth-required", authorization=connection.challenge, catalogState="stale", loadedSchemas={}))
            raise

    async def uninstall(self, identity):
        async with self.install_lock:
            package = next((p for p in self.state["installations"] if p["id"] == identity), None)
            if not package:
                raise ValueError("This package is not installed.")
            if not re.fullmatch(r"[a-f0-9]{16,64}", identity):
                raise ValueError("This installation has an unsupported identifier.")
            folder = self.root / "installs" / identity
            if folder.is_symlink() or folder.parent.is_symlink() or not folder.resolve().is_relative_to((self.root / "installs").resolve()):
                raise ValueError("This installation path is no longer owned by the app.")
            dependents = []
            for server in self.state["servers"]:
                paths = [server.get("command"), server.get("cwd"), *server.get("args", [])]
                if server.get("installationId") == identity or any(isinstance(p, str) and p and Path(p).expanduser().is_absolute() and Path(p).expanduser().resolve().is_relative_to(folder.resolve()) for p in paths):
                    dependents.append(server["name"])
            if dependents:
                raise ValueError("Remove these connection registrations before uninstalling: " + ", ".join(dependents)[:500])
            await asyncio.to_thread(shutil.rmtree, folder)
            await self._change(lambda state: state.update(installations=[p for p in state["installations"] if p["id"] != identity]))
            return {"id": identity, "status": "uninstalled", "removedPath": str(folder), "externalToolWork": "unchanged"}
