"""Opt-in integration against a separately installed portable M365 MCP package."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import pytest
from aiohttp import web
from amplifier_web.smart_tools import SmartToolsManager

PACKAGE = os.environ.get("AMPLIFIER_M365_TEST_PACKAGE")
PYTHON = os.environ.get("AMPLIFIER_M365_TEST_PYTHON")
pytestmark = pytest.mark.skipif(not PACKAGE or not PYTHON, reason="Install and explicitly select the optional M365 package")


class Service:
    def __init__(self, path):
        self.data_dir, self.state, self.lock = path, {}, asyncio.Lock()
        self.db = sqlite3.connect(":memory:")
    def _publish(self): self.db.commit()


async def test_shared_ui_agent_m365_workflow(tmp_path, aiohttp_server):
    values, patches = [[2, 3, 5]], []
    async def endpoint(request):
        nonlocal values
        assert request.headers.get("Authorization") == "Bearer synthetic"
        path = request.path
        if path.endswith("/me"): return web.json_response({"id": "synthetic-user"})
        if path == "/v1.0/drives/drive": return web.json_response({"driveType": "business"})
        if path.endswith("/items/book"): return web.json_response({"id": "book", "name": "Synthetic.xlsx", "eTag": "v1", "file": {}})
        if path.endswith("/createSession"): return web.json_response({"id": "remote"}, status=201)
        assert request.headers.get("workbook-session-id") == "remote"
        if path.endswith("/worksheets/sheet"): return web.json_response({"id": "sheet"})
        if "/range(" in path:
            if request.method == "PATCH":
                values = (await request.json())["values"]
                patches.append(values)
            return web.json_response({"address": "Sheet1!A1:C1", "values": values, "formulas": values, "numberFormat": [["General"]*3]})
        if path.endswith("/application/calculate"):
            values[0][2] = sum(values[0][:2])
            return web.Response(status=204)
        return web.Response(status=204)
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", endpoint)
    http = await aiohttp_server(app)
    # Test-only executable injects the fixture URL directly; production configuration
    # cannot change Graph's pinned origin or inject a bearer through tool arguments.
    script = tmp_path / "mcp_fixture.py"
    script.write_text("import sys\nsys.path.insert(0, " + repr(str(Path(PACKAGE).resolve())) + ")\nfrom amplifier_m365 import server\nfrom amplifier_m365.graph import GraphDocuments\nserver._graph = GraphDocuments(lambda: 'synthetic', " + repr(str(tmp_path / "private")) + ", " + repr(str(tmp_path / "exports")) + ", base_url=" + repr(str(http.make_url("/v1.0"))) + ")\nserver.main()\n")
    manager = SmartToolsManager(Service(tmp_path / "host"))
    try:
        await manager.configure({"id": "m365", "name": "Synthetic M365", "command": PYTHON, "args": [str(script)]})
        assert not manager.connections
        await manager.connect("m365")
        catalog = await manager.list_tools("m365")
        assert {"m365_document", "excel_cloud_edit", "excel_live_edit"} <= {t["name"] for t in catalog}
        async def call(name, arguments, operation, origin):
            result = await manager.command("smartTools.call", {"id": "m365", "name": name, "arguments": arguments}, operation, origin=origin)
            return result["structuredContent"]
        session = await call("excel_cloud_open", {"drive_id": "drive", "item_id": "book", "expected_etag": "v1", "persist_changes": True, "operation_id": "open"}, "ui-open", "ui")
        target = {"session_id": session["sessionId"], "drive_id": "drive", "item_id": "book", "worksheet_id": "sheet"}
        read = await call("excel_cloud_read", {**target, "address": "A1:C1"}, "agent-read", "agent")
        edit = await call("excel_cloud_edit", {**target, "address": "A1:C1", "expected_revision": 0, "expected_range_revision": read["range"]["rangeRevision"], "operation_id": "edit", "data": [[10,20,5]]}, "ui-edit", "ui")
        assert edit["range"]["values"] == [[10,20,5]]
        calculated = await call("excel_cloud_calculate", {**target, "readback_address": "A1:C1", "expected_revision": 1, "operation_id": "calc"}, "agent-calc", "agent")
        assert calculated["range"]["values"] == [[10,20,30]]
        assert len(patches) == 1
        assert "Bearer synthetic" not in json.dumps(manager.service.state)
    finally:
        await manager.close()
