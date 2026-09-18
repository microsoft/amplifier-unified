"""Managed Smart Tool installs and standards-based MCP connections.

Catalog metadata is inert. Only an explicit install or connect action executes
code. All MCP operations run in the task which owns their transport lifetime;
this is important because the SDK's AnyIO cancel scopes are task-local.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import uuid

import jsonschema
import yaml

CATALOG_REPOSITORY = "https://github.com/microsoft/amplifier-smart-tools-catalog.git"
APP_MIME = "text/html;profile=mcp-app"
MAX_RESULT_BYTES = 2_000_000
MAX_HTML_BYTES = 2_000_000
MAX_TOOLS = 500
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXTRA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def configuration_key(config):
    """Bind an open surface to the exact executable configuration it reviewed."""
    values = {key: config.get(key) for key in ("id", "command", "args", "env", "cwd")}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _bounded(value, limit=MAX_RESULT_BYTES):
    value = _json(value)
    if len(json.dumps(value, ensure_ascii=False).encode()) > limit:
        raise ValueError("The tool response is too large. Ask the tool for a smaller page or artifact reference.")
    return value


def _ui(tool):
    meta = tool.get("_meta", {})
    ui = meta.get("ui", {})
    return ui if isinstance(ui, dict) else {}


def _visible(tool, origin):
    visibility = _ui(tool).get("visibility", ["model", "app"])
    return ("app" if origin == "app" else "model") in visibility


def _repository(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        raise ValueError("Enter a credential-free HTTPS Git repository URL.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a credential-free HTTPS Git repository URL.")
    return value.rstrip("/")


def _relative(value):
    value = value or "."
    if not isinstance(value, str) or "\x00" in value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("The tool path must remain inside its repository.")
    return value


def _install_environment():
    from mcp.client.stdio import get_default_environment
    environment = get_default_environment()
    for name in ("TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if name in os.environ:
            environment[name] = os.environ[name]
    environment["GIT_TERMINAL_PROMPT"] = "0"
    # Discovery must not inherit checkout hooks or attribute filters configured
    # elsewhere on the machine. Authentication is supplied explicitly below.
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    if shutil.which("gh"):
        environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.https://github.com.helper", "GIT_CONFIG_VALUE_0": "!gh auth git-credential"})
    return environment


def _inside(root, relative):
    candidate = (root / _relative(relative)).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("A tool file points outside its repository.")
    return candidate


def _manifest(text):
    if len(text.encode()) > 256_000 or not text.startswith("---\n"):
        raise ValueError("SMART_TOOL.md needs bounded YAML front matter.")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ValueError("SMART_TOOL.md has incomplete front matter.")
    data = yaml.safe_load(parts[0][4:])
    if not isinstance(data, dict) or data.get("smart_tool_format") != 1:
        raise ValueError("Only Smart Tool manifest format 1 is supported.")
    for key in ("name", "version", "description"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"The Smart Tool manifest needs {key}.")
    # Requirements and instructions are displayed, never evaluated as commands.
    return _bounded(data, 32_000)


class _Connection:
    """One task owns both the SDK client and its sequential request queue."""

    def __init__(self, config, secrets):
        self.config = copy.deepcopy(config)
        self.secrets = secrets
        self.queue = asyncio.Queue(maxsize=64)
        self.ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run())
        self.failure = None

    async def _run(self):
        from mcp import Client, StdioServerParameters
        from mcp.client.extension import advertise
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(
            command=self.config["command"], args=self.config.get("args", []),
            env=self.secrets, cwd=self.config.get("cwd") or None,
        )
        current = None
        try:
            # Server stderr can contain credentials. Keep it out of application
            # state and HTTP responses, including when a handshake fails.
            with open(os.devnull, "w") as errlog:
                async with Client(
                    stdio_client(params, errlog=errlog), read_timeout_seconds=30,
                    extensions=[advertise("io.modelcontextprotocol/ui", {"mimeTypes": [APP_MIME]})],
                    cache=None,
                ) as client:
                    instructions = client.instructions or ""
                    self.ready.set_result({
                        "protocolVersion": client.protocol_version,
                        "serverInfo": _json(client.server_info),
                        "capabilities": _json(client.server_capabilities),
                        "instructions": instructions[:16_000],
                        "instructionsTruncated": len(instructions) > 16_000,
                    })
                    while True:
                        request = await self.queue.get()
                        if request is None:
                            break
                        method, args, kwargs, current = request
                        if current.cancelled():
                            continue
                        try:
                            result = await getattr(client, method)(*args, **kwargs)
                            if not current.done():
                                current.set_result(_json(result))
                        except Exception as exc:
                            if not current.done():
                                current.set_exception(exc)
                        finally:
                            current = None
        except BaseException as exc:
            self.failure = exc
            if not self.ready.done():
                self.ready.set_exception(ValueError("The MCP server could not start or complete its connection handshake."))
            if current and not current.done():
                current.set_exception(ValueError("The MCP connection closed. Work was not replayed."))
        finally:
            while not self.queue.empty():
                pending = self.queue.get_nowait()
                if pending and not pending[3].done():
                    pending[3].set_exception(ValueError("The MCP connection closed. Work was not replayed."))

    async def request(self, method, *args, timeout=60, **kwargs):
        if self.task.done():
            raise ValueError("The MCP connection is closed. Reconnect before sending another request.")
        future = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((method, args, kwargs, future))
        except asyncio.QueueFull:
            raise ValueError("This tool has too many pending requests. Try again when it finishes.") from None
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            # A timed-out write may have reached the server. Never retry it.
            # Disconnect so a late response cannot be confused with newer work.
            await self.close()
            raise ValueError("The tool timed out. It may have started work; it was not replayed. Reconnect and inspect the tool's state.") from None

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class SmartToolsManager:
    def __init__(self, service):
        self.service = service
        self.root = Path(service.data_dir) / "smart-tools"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connections = {}
        self.connection_locks = {}
        self.install_lock = asyncio.Lock()
        self.closed = False
        service.db.execute("CREATE TABLE IF NOT EXISTS smart_tool_operations (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        service.db.execute("CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        state = service.state.setdefault("smartTools", {})
        for field in ("servers", "operations", "catalog", "installations"):
            state.setdefault(field, [])
        for server in state["servers"]:
            server["status"] = "disconnected"
        for operation in state["operations"]:
            if operation.get("status") in {"running", "queued"}:
                operation.update(status="interrupted", error="The app restarted. Work was not replayed.", updatedAt=time.time())
            self.persist_operation(operation)
        # Interrupted operations can outlive the bounded overview in app state.
        for identity, value in service.db.execute("SELECT id,value FROM smart_tool_operations").fetchall():
            operation = json.loads(value)
            if operation.get("status") in {"running", "queued"}:
                operation.update(status="interrupted", error="The app restarted. Work was not replayed.", updatedAt=time.time())
                self.persist_operation(operation)
        service.db.commit()

    def persist_operation(self, operation):
        """Write a receipt while holding service.lock; the caller publishes/commits."""
        full = copy.deepcopy(operation)
        for field in ("result", "arguments"):
            value = full.get(field)
            if isinstance(value, dict) and "$resource" in value:
                stored = self.service.db.execute("SELECT value FROM state_resources WHERE id=?", (value["$resource"],)).fetchone()
                if stored:
                    full[field] = json.loads(stored[0])
        self.service.db.execute("INSERT OR REPLACE INTO smart_tool_operations VALUES (?, ?)", (operation["id"], json.dumps(full)))
        operation.update(self._overview(full))

    def _overview(self, operation):
        overview = copy.deepcopy(operation)
        for field in ("result", "arguments"):
            if field not in overview:
                continue
            text = json.dumps(overview[field], ensure_ascii=False)
            if len(text.encode()) > 16_000:
                identity = hashlib.sha256(text.encode()).hexdigest()
                self.service.db.execute("INSERT OR IGNORE INTO state_resources VALUES (?, ?)", (identity, text))
                overview[field] = {"$resource": identity, "bytes": len(text.encode()), "summary": "Stored tool data; read this state path for paginated details."}
        return overview

    def operation(self, identity):
        row = self.service.db.execute("SELECT value FROM smart_tool_operations WHERE id=?", (identity,)).fetchone()
        if row:
            return json.loads(row[0])
        operation = next((item for item in self.state["operations"] if item["id"] == identity), None)
        return copy.deepcopy(operation)

    async def inspect_operation(self, identity):
        operation = self.operation(identity)
        if operation is None:
            raise ValueError("This Smart Tool operation could not be found.")
        def inspect(state):
            state["inspectedOperation"] = self._overview(operation)
            return {"operationId": identity, "statePath": "/smartTools/inspectedOperation"}
        return await self._change(inspect)

    @property
    def state(self):
        return self.service.state["smartTools"]

    def _server(self, identity):
        row = next((row for row in self.state["servers"] if row["id"] == identity), None)
        if row is None:
            raise ValueError("This Smart Tool connection is no longer registered.")
        return row

    async def _change(self, callback):
        async with self.service.lock:
            result = callback(self.state)
            self.service._publish()
            return copy.deepcopy(result)

    def _redact(self, value):
        secrets = {
            os.environ[source] for server in self.state["servers"]
            for source in server.get("env", {}).values()
            if source in os.environ and len(os.environ[source]) >= 4
        }
        def clean(item):
            if isinstance(item, str):
                for secret in secrets:
                    item = item.replace(secret, "[redacted]")
                return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", item)
            if isinstance(item, dict):
                return {key: "[redacted]" if key.lower() in {"api_key", "apikey", "access_token", "authorization", "password", "client_secret"} else clean(val) for key, val in item.items()}
            if isinstance(item, list):
                return [clean(val) for val in item]
            return item
        return clean(value)

    async def command(self, action, args, command_id, origin="ui"):
        args = copy.deepcopy(args)
        operation = {
            "id": command_id, "action": action, "origin": origin,
            "target": {key: args[key] for key in ("id", "name", "repository", "ref", "path", "sessionId", "uri") if key in args},
            "status": "running", "createdAt": time.time(), "updatedAt": time.time(),
        }
        if action in {"smartTools.call", "smartTools.resources", "smartTools.readResource"}:
            if action == "smartTools.call":
                operation["arguments"] = self._redact(copy.deepcopy(args.get("arguments", {})))
            server = next((row for row in self.state["servers"] if row["id"] == args.get("id")), None)
            if server is not None:
                operation["configuration"] = configuration_key(server)
                args.setdefault("_configuration", operation["configuration"])
        def add(state):
            # Do not lose an in-flight operation when trimming older results.
            if self.operation(command_id) is not None:
                raise ValueError("This Smart Tool command already has a receipt.")
            state["operations"] = [row for row in state["operations"] if row.get("status") == "running"] + [row for row in state["operations"] if row.get("status") != "running"][-49:]
            state["operations"].append(operation)
            self.persist_operation(operation)
        await self._change(add)
        def finish(**values):
            operation.update(**values, updatedAt=time.time())
            self.persist_operation(operation)
            owner=next((s for s in self.service.state.get('sessions',[]) if s['id']==args.get('sessionId')), {})
            if getattr(self.service,'diagnostics',None):
                self.service.diagnostics.record('smartTools',{'event':'smart-tool:operation','data':{'operationId':command_id,'action':action,'origin':origin,'status':operation['status'],'serverId':args.get('id'),'durationMs':round((time.time()-operation['createdAt'])*1000)}},session_id=owner.get('runtimeSessionId') or owner.get('id'),workspace=owner.get('workspace'))
        try:
            result = self._redact(_bounded(await self.execute(action, args, origin=origin)))
            is_error = isinstance(result, dict) and result.get("isError") is True
            error = " ".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")[:1500] if is_error else None
            await self._change(lambda _: finish(status="failed" if is_error else "completed", result=result, error=error))
            return result
        except asyncio.CancelledError:
            await self._change(lambda _: finish(status="interrupted", error="The request was interrupted. Work was not replayed."))
            raise
        except Exception as exc:
            error = self._redact(str(exc))[:1500]
            await self._change(lambda _: finish(status="failed", error=error))
            return None

    async def execute(self, action, args, origin="ui"):
        if self.closed:
            raise ValueError("Smart Tools are shutting down.")
        name = action.removeprefix("smartTools.")
        if name == "configure":
            return await self.configure(args)
        if name == "result":
            return await self.inspect_operation(args["operationId"])
        if name == "remove":
            async with self.connection_locks.setdefault(args["id"], asyncio.Lock()):
                await self._disconnect(args["id"])
                return await self._change(lambda state: state.update(servers=[row for row in state["servers"] if row["id"] != args["id"]]))
        if name == "connect":
            return await self.connect(args["id"])
        if name == "disconnect":
            await self.disconnect(args["id"])
            return {"id": args["id"], "status": "disconnected"}
        if name == "call":
            return await self.call_tool(args["id"], args["name"], args.get("arguments", {}), origin=origin, timeout_seconds=args.get("timeoutSeconds", 60), allowed_tools=args.get("_allowedTools"), expected_configuration=args.get("_configuration"))
        if name in {"resources", "readResource"}:
            return await self.resource_request(args["id"], "read" if name == "readResource" else args.get("kind", "list"), uri=args.get("uri"), cursor=args.get("cursor"), expected_configuration=args.get("_configuration"))
        if name == "catalog":
            return await self.catalog()
        if name == "inspect":
            return await self.inspect(args)
        if name == "install":
            return await self.install(args)
        raise ValueError("Unknown Smart Tool action.")

    async def configure(self, args):
        identity = args.get("id") or uuid.uuid4().hex
        command = args.get("command", "")
        if not isinstance(command, str) or not command.strip() or len(command) > 4000 or "\x00" in command:
            raise ValueError("Choose the MCP server executable.")
        argv = args.get("args", [])
        if not isinstance(argv, list) or len(argv) > 100 or any(not isinstance(arg, str) or len(arg) > 8000 or "\x00" in arg for arg in argv):
            raise ValueError("Arguments must be a list of text values.")
        env = args.get("env", {})
        if not isinstance(env, dict) or len(env) > 50 or any(not isinstance(k, str) or not isinstance(v, str) or not ENV_NAME.fullmatch(k) or not ENV_NAME.fullmatch(v) for k, v in env.items()):
            raise ValueError("Environment configuration maps variable names to existing environment variable names; do not paste secret values.")
        cwd = args.get("cwd")
        if cwd:
            cwd = str(Path(cwd).expanduser().resolve())
            if not Path(cwd).is_dir():
                raise ValueError("The server working folder does not exist.")
        row = {"id": identity, "name": str(args.get("name") or Path(command).name)[:120], "command": command, "args": argv, "env": env, "cwd": cwd, "status": "disconnected", "tools": [], "uiCapable": False, "updatedAt": time.time()}
        def save(state):
            state["servers"] = [item for item in state["servers"] if item["id"] != identity] + [row]
            return row
        async with self.connection_locks.setdefault(identity, asyncio.Lock()):
            if any(item["id"] == identity for item in self.state["servers"]):
                await self._disconnect(identity)
            return await self._change(save)

    async def connect(self, identity):
        lock = self.connection_locks.setdefault(identity, asyncio.Lock())
        async with lock:
            row = self._server(identity)
            connection = self.connections.get(identity)
            if connection and not connection.task.done():
                return copy.deepcopy(row)
            env = {}
            for name, source in row.get("env", {}).items():
                if not os.environ.get(source):
                    raise ValueError(f"Environment variable {source} is not set. Set it before connecting.")
                env[name] = os.environ[source]
            await self._change(lambda _: row.update(status="connecting", error=None))
            connection = _Connection(row, env)
            try:
                info = self._redact(_bounded(await asyncio.wait_for(asyncio.shield(connection.ready), 30), 128_000))
                tools = []
                cursor = None
                for _ in range(20):
                    page = await connection.request("list_tools", cursor=cursor, timeout=30)
                    tools.extend(page.get("tools", []))
                    if len(tools) > MAX_TOOLS:
                        raise ValueError("This server exposes too many tools for one connection.")
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                else:
                    raise ValueError("Tool discovery did not finish after 20 pages.")
                _bounded(tools, 1_000_000)
                self.connections[identity] = connection
                await self._change(lambda _: row.update(status="connected", tools=self._redact(tools), uiCapable=any(_ui(tool).get("resourceUri") for tool in tools), **info, error=None, updatedAt=time.time()))
                return copy.deepcopy(row)
            except BaseException as exc:
                await connection.close()
                # Consume a startup future exception even if timeout raced it.
                if connection.ready.done() and not connection.ready.cancelled():
                    connection.ready.exception()
                message = "The MCP server did not become ready within 30 seconds." if isinstance(exc, TimeoutError) else self._redact(str(exc))[:1000]
                await self._change(lambda _: row.update(status="error", error=message))
                raise ValueError(message or "The MCP connection failed.") from None

    async def disconnect(self, identity):
        async with self.connection_locks.setdefault(identity, asyncio.Lock()):
            await self._disconnect(identity)

    async def _disconnect(self, identity):
        row = self._server(identity)
        connection = self.connections.pop(identity, None)
        if connection:
            await connection.close()
        await self._change(lambda _: row.update(status="disconnected", updatedAt=time.time()))

    async def _connection(self, identity):
        self._server(identity)
        connection = self.connections.get(identity)
        if not connection or connection.task.done():
            if connection:
                await self._change(lambda _: self._server(identity).update(status="disconnected"))
            raise ValueError("Connect this tool before using it. Previous requests are never replayed.")
        return connection

    async def list_tools(self, identity, origin="agent"):
        await self._connection(identity)
        return [copy.deepcopy(tool) for tool in self._server(identity).get("tools", []) if _visible(tool, origin)]

    async def call_tool(self, identity, name, arguments, origin="ui", timeout_seconds=60, allowed_tools=None, expected_configuration=None):
        connection = await self._connection(identity)
        if expected_configuration is not None and configuration_key(self._server(identity)) != expected_configuration:
            raise ValueError("This tool's connection settings changed. Reopen its view before using it.")
        tool = next((tool for tool in self._server(identity)["tools"] if tool.get("name") == name), None)
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
        try:
            result = await connection.request("call_tool", name, arguments, read_timeout_seconds=timeout_seconds, timeout=timeout_seconds + 1)
            return self._redact(_bounded(result))
        finally:
            if connection.task.done():
                await self._change(lambda _: self._server(identity).update(status="disconnected"))

    async def resource_request(self, identity, kind, *, uri=None, cursor=None, expected_configuration=None):
        """Forward bounded MCP resource requests, never resolve URIs in the host.

        The configured server owns resource authorization. A view gets only its
        saved server binding, with no authority to choose another connection.
        Large media must use tool-defined bounded resource chunks.
        """
        connection = await self._connection(identity)
        key = configuration_key(self._server(identity))
        if expected_configuration is not None and key != expected_configuration:
            raise ValueError("This tool's connection settings changed. Reopen its view before using it.")
        if "resources" not in self._server(identity).get("capabilities", {}):
            raise ValueError("This MCP server does not advertise resource access.")
        if kind == "read":
            if not isinstance(uri, str) or not uri or len(uri) > 4000 or any(ord(c) < 32 for c in uri):
                raise ValueError("Choose a resource URI supplied by this tool.")
            parsed = urlsplit(uri)
            if not parsed.scheme or parsed.username or parsed.password:
                raise ValueError("Resource URIs must be absolute and contain no credentials.")
            result = await connection.request("read_resource", uri, timeout=30)
        elif kind in {"list", "templates"}:
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4000):
                raise ValueError("The resource cursor is invalid.")
            result = await connection.request("list_resources" if kind == "list" else "list_resource_templates", cursor=cursor, timeout=30)
        else:
            raise ValueError("Unknown resource operation.")
        if self.connections.get(identity) is not connection or configuration_key(self._server(identity)) != key:
            raise ValueError("The resource connection changed while reading. Reopen its view.")
        return self._redact(_bounded(result))

    async def read_app(self, identity, uri):
        connection = await self._connection(identity)
        tools = self._server(identity)["tools"]
        if not isinstance(uri, str) or not uri.startswith("ui://") or not any(_ui(tool).get("resourceUri") == uri for tool in tools):
            raise ValueError("This view was not advertised by this MCP connection.")
        result = _bounded(await connection.request("read_resource", uri, timeout=30), MAX_HTML_BYTES + 32_000)
        contents = result.get("contents", [])
        content = next((item for item in contents if str(item.get("uri")) == uri), None)
        if content is None or content.get("mimeType", "").replace(" ", "").lower() != APP_MIME:
            raise ValueError("This view is not an MCP App HTML resource.")
        html = content.get("text")
        if not isinstance(html, str) or len(html.encode()) > MAX_HTML_BYTES:
            raise ValueError("MCP App HTML must be text smaller than 2 MB.")
        meta = content.get("_meta", {})
        ui = meta.get("ui", {}) if isinstance(meta, dict) else {}
        if not isinstance(ui, dict):
            raise ValueError("The MCP App resource metadata is invalid.")
        # The host decides which requested permissions it actually grants.
        return {"uri": uri, "html": self._redact(html), "csp": ui.get("csp", {}), "permissions": ui.get("permissions", {}), "meta": _bounded(meta, 32_000), "tools": [tool["name"] for tool in tools if _visible(tool, "app")]}

    async def _run(self, argv, *, cwd=None, timeout=120, env=None):
        process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            detail = self._redact(err.decode(errors="replace"))[-1500:]
            raise ValueError(f"{Path(argv[0]).name} failed: {detail or 'The command did not complete.'}")
        if len(out) > 2_000_000:
            raise ValueError("The command returned too much metadata.")
        return out.decode(errors="replace").strip()

    async def _checkout(self, repository, ref=None):
        repository = _repository(repository)
        if ref is not None and (not isinstance(ref, str) or not ref or ref.startswith("-") or any(c.isspace() for c in ref) or len(ref) > 200):
            raise ValueError("Enter a Git branch, tag, or commit as the revision.")
        folder = self.root / "sources" / uuid.uuid4().hex
        folder.parent.mkdir(exist_ok=True, mode=0o700)
        env = _install_environment()
        try:
            await self._run(["git", "init", "--quiet", str(folder)], env=env)
            await self._run(["git", "remote", "add", "origin", repository], cwd=folder, env=env)
            await self._run(["git", "fetch", "--quiet", "--depth=1", "origin", ref or "HEAD"], cwd=folder, env=env)
            commit = await self._run(["git", "rev-parse", "FETCH_HEAD"], cwd=folder, env=env)
            await self._run(["git", "-c", f"core.hooksPath={os.devnull}", "checkout", "--quiet", "--detach", commit], cwd=folder, env=env)
            return folder, commit
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def _inspect_checkout(self, checkout, repository, commit, args):
        root = _inside(checkout, args.get("path"))
        descriptor_path = _inside(root, "smart-tool.json")
        if not descriptor_path.is_file() or descriptor_path.stat().st_size > 32_000:
            raise ValueError("This folder needs a smart-tool.json descriptor smaller than 32 KB.")
        descriptor = json.loads(descriptor_path.read_text())
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("manifest"), str):
            raise ValueError("smart-tool.json must identify a manifest file.")
        argv = descriptor.get("cli_argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
            raise ValueError("smart-tool.json must contain a nonempty cli_argv array.")
        manifest_path = _inside(root, descriptor["manifest"])
        if not manifest_path.is_file() or manifest_path.stat().st_size > 256_000:
            raise ValueError("The declared Smart Tool manifest is missing or too large.")
        manifest = _manifest(manifest_path.read_text())
        return {"repository": repository, "ref": args.get("ref") or "HEAD", "path": args.get("path") or ".", "commit": commit, "name": manifest["name"], "description": manifest["description"], "manifest": manifest, "descriptor": descriptor, "pythonSupported": (root / "pyproject.toml").is_file(), "status": "inspected"}

    async def inspect(self, args):
        repository = _repository(args["repository"])
        checkout, commit = await self._checkout(repository, args.get("ref"))
        try:
            return self._inspect_checkout(checkout, repository, commit, args)
        finally:
            shutil.rmtree(checkout, ignore_errors=True)

    async def catalog(self):
        checkout, commit = await self._checkout(CATALOG_REPOSITORY)
        try:
            rows = []
            for source_path in sorted((checkout / "tools").glob("*/source.json"))[:200]:
                if not source_path.resolve().is_relative_to(checkout.resolve()) or source_path.stat().st_size > 32_000:
                    continue
                try:
                    source = json.loads(source_path.read_text())
                    repository = _repository(source["repository"])
                    manifest_path = source_path.parent / "SMART_TOOL.md"
                    if not manifest_path.resolve().is_relative_to(checkout.resolve()):
                        continue
                    manifest = _manifest(manifest_path.read_text())
                    rows.append({"id": source_path.parent.name, "repository": repository, "ref": source.get("ref", "HEAD"), "path": _relative(source.get("path")), "name": manifest["name"], "description": manifest["description"], "version": manifest["version"], "manifest": manifest, "status": "listed"})
                except (KeyError, OSError, ValueError, yaml.YAMLError):
                    continue
            if not rows:
                raise ValueError("The catalog did not contain any valid Smart Tool entries.")
            await self._change(lambda state: state.update(catalog=rows, catalogCommit=commit, catalogCheckedAt=time.time()))
            return {"items": rows, "commit": commit}
        finally:
            shutil.rmtree(checkout, ignore_errors=True)

    async def install(self, args):
        repository = _repository(args["repository"])
        extras = args.get("extras", [])
        if not isinstance(extras, list) or len(extras) > 20 or any(not isinstance(extra, str) or not EXTRA_NAME.fullmatch(extra) for extra in extras):
            raise ValueError("Package extras must be simple names such as mcp.")
        async with self.install_lock:
            checkout, commit = await self._checkout(repository, args.get("ref"))
            install_dir = None
            try:
                info = self._inspect_checkout(checkout, repository, commit, args)
                if not info["pythonSupported"]:
                    raise ValueError("Automatic installation currently supports Python projects with pyproject.toml. Follow this tool's install instructions, then register its MCP server command.")
                identity = hashlib.sha256(json.dumps([repository, commit, info["path"], sorted(extras)]).encode()).hexdigest()[:24]
                install_dir = self.root / "installs" / identity
                previous = next((row for row in self.state["installations"] if row["id"] == identity), None)
                if previous and (install_dir / "venv" / "bin" / "python").exists():
                    return copy.deepcopy(previous)
                if install_dir.exists():
                    shutil.rmtree(install_dir)
                install_dir.mkdir(parents=True, mode=0o700)
                source = install_dir / "source"
                shutil.move(str(checkout), source)
                python = install_dir / "venv" / "bin" / "python"
                uv = shutil.which("uv")
                if not uv:
                    raise ValueError("uv is required to install Smart Tools.")
                environment = _install_environment()
                await self._run([uv, "venv", str(install_dir / "venv")], timeout=120, env=environment)
                package = str(_inside(source, info["path"])) + ("[" + ",".join(extras) + "]" if extras else "")
                await self._run([uv, "pip", "install", "--python", str(python), package], timeout=600, env=environment)
                row = {**info, "id": identity, "status": "installed", "extras": extras, "installedAt": time.time(), "binDir": str(python.parent), "python": str(python), "sourceDir": str(_inside(source, info["path"])), "nextStep": "Register the tool's documented MCP executable from this bin folder. Installation does not start a server or configure model credentials."}
                await self._change(lambda state: state.update(installations=[item for item in state["installations"] if item["id"] != identity] + [row]))
                return row
            except BaseException:
                if install_dir and not any(row["id"] == install_dir.name for row in self.state["installations"]):
                    shutil.rmtree(install_dir, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(checkout, ignore_errors=True)

    async def close(self):
        self.closed = True
        await asyncio.gather(*(connection.close() for connection in self.connections.values()), return_exceptions=True)
        self.connections.clear()
