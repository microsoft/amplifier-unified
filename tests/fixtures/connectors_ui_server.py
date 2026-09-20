"""Production host/browser acceptance with local SDK OAuth and MCP servers."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web.server import create_app
from mcp_oauth_server import Fixture


class Runtime:
    async def close(self): pass
    async def stop(self, *args): pass


async def main(home):
    os.environ["AMPLIFIER_HOME"] = str(home / "native")
    os.environ["AMPLIFIER_WEB_HOME"] = str(home)
    os.environ["AMPLIFIER_UNIFIED_IMPORT_HOME"] = str(home / "legacy")
    workspace = home / "workspace"
    workspace.mkdir()
    fixture = await Fixture(identity=True).start()
    app = await create_app(home, workspace=workspace, runtime=Runtime(), voice=False,
        preload_providers=False, background_updates=False, server_config={"port":8989})
    app["control_token"] = "fixture-browser-control-token"
    service = app["service"]
    await service.dispatch("session.create", {"title":"Connector acceptance", "workspace":str(workspace)})

    async def info(request):
        return web.json_response({"endpoint":fixture.origin+"/mcp", "calls":fixture.calls,
            "exchanges":fixture.provider.exchanges, "registrations":len(fixture.provider.clients),
            "packageExists":(service.smart_tools.root / "installs" / ("d"*16)).exists(),
            "externalWorkExists":(workspace / "saved-tool-work").exists()})

    async def principal(request):
        payload = await request.json()
        assert payload['subject'] in {'fixture-user', 'principal-two'}
        for token in fixture.provider.access.values():
            token.subject = payload['subject']
        return web.json_response({'changed': True})

    async def package(request):
        identity = "d"*16
        folder = service.smart_tools.root / "installs" / identity
        folder.mkdir(parents=True)
        (folder / "package.txt").write_text("Synthetic installed package")
        (workspace / "saved-tool-work").write_text("Preserve external work")
        await service.smart_tools._change(lambda state:state.update(installations=[{
            "id":identity,"name":"Fixture package","repository":"https://example.com/fixture-package",
            "ref":"main","path":".","status":"installed","binDir":str(folder)}]))
        await service.smart_tools.configure({"id":"package-dependency","name":"Package dependency",
            "command":sys.executable,"args":[],"installationId":identity})
        return web.json_response({"id":identity})

    async def agent(request):
        payload = await request.json()
        result = await service.app_bridge("dispatch", payload, service.state["selectedSessionId"])
        if result.get("operationId"):
            await service.wait_smart_tool(result["operationId"])
        return web.json_response(result)

    app.router.add_get("/fixture/info", info)
    app.router.add_post("/fixture/agent", agent)
    app.router.add_post("/fixture/package", package)
    app.router.add_post("/fixture/principal", principal)

    async def cleanup(app):
        await fixture.close()
    app.on_cleanup.append(cleanup)
    return app


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="unified-connector-acceptance-") as temp:
        web.run_app(main(Path(temp)), host="127.0.0.1", port=8989, print=None, access_log=None)
