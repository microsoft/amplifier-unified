import base64
import json

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.workspace_canvas import MAX_TEXT, validate_surface


def surface():
    return {"surfaceId":"test", "root":"column", "components":[
        {"id":"column", "component":{"Column":{"children":{"explicitList":["button"]}}}},
        {"id":"button", "component":{"Button":{"child":"text", "action":{"name":"review"}}}},
        {"id":"text", "component":{"Text":{"text":{"literalString":"Review"}}}},
    ]}


@pytest.fixture
async def service(tmp_path):
    work = tmp_path / "workspace"
    work.mkdir()
    app = AppService(tmp_path / "data", workspace=work)
    yield app
    await app.close()


async def test_workspace_management_is_durable_and_non_destructive(service, tmp_path):
    first = service.get_state()["workspaces"][0]
    second = tmp_path / "second"
    second.mkdir()
    (second / "important.txt").write_text("Keep me")
    await service.dispatch("workspace.add", {"path":str(second), "name":"Two"}, origin="agent")
    selected = service.get_state()["selectedWorkspaceId"]
    await service.dispatch("session.create", {})
    sid = service.state["selectedSessionId"]
    assert service._session(sid)["workspace"] == str(second)
    await service.dispatch("workspace.add", {"path":str(second / ".")})
    assert len(service.state["workspaces"]) == 2
    await service.dispatch("workspace.rename", {"id":selected,"name":"Renamed"})
    await service.dispatch("workspace.select", {"id":first["id"]})
    await service.dispatch("workspace.remove", {"id":selected})
    assert (second / "important.txt").read_text() == "Keep me"
    assert service._session(sid)["workspace"] == str(second)
    with pytest.raises(AppError, match="at least one"):
        await service.dispatch("workspace.remove", {"id":first["id"]})
    # Removed legacy paths must not be silently registered again on restart.
    restored = AppService(service.data_dir, workspace=first["path"])
    assert restored.state["workspaces"] == [first]
    assert restored.state["settings"]["workspace"] == first["path"]
    await restored.close()


async def test_workspace_paths_must_exist(service, tmp_path):
    with pytest.raises(AppError, match="existing workspace"):
        await service.dispatch("workspace.add", {"path":str(tmp_path / "missing")})


async def test_canvas_files_are_confined_and_bounded(service, tmp_path):
    root = tmp_path / "workspace"
    (root / "plan.md").write_text("# Plan\nHello")
    await service.dispatch("canvas.show", {"kind":"markdown", "path":"plan.md"}, origin="agent")
    assert service.get_state()["canvas"]["content"] == "# Plan\nHello"
    assert service.get_state()["canvas"]["title"] == "plan.md"
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / "link").symlink_to(outside)
    for path in ["../outside.txt", str(outside), "link"]:
        with pytest.raises(AppError, match="inside the selected workspace"):
            await service.dispatch("canvas.show", {"kind":"text", "path":path})
    (root / "large.txt").write_text("a" * (MAX_TEXT + 1))
    with pytest.raises(AppError, match="too large"):
        await service.dispatch("canvas.show", {"kind":"text", "path":"large.txt"})
    await service.dispatch("canvas.close", {})
    assert not service.state["canvas"]["open"]
    assert service.state["canvas"]["content"] == "# Plan\nHello"


async def test_canvas_images_are_embedded_not_active_documents(service, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"test"
    (tmp_path / "workspace" / "sample.png").write_bytes(png)
    await service.dispatch("canvas.show", {"kind":"image", "path":"sample.png"})
    assert service.state["canvas"]["content"] == "data:image/png;base64," + base64.b64encode(png).decode()
    for content in ["https://example.com/image.png", "data:image/svg+xml;base64," + base64.b64encode(b'<svg onload="alert(1)"/>').decode()]:
        with pytest.raises(AppError):
            await service.dispatch("canvas.show", {"kind":"image", "content":content})


async def test_a2ui_surface_and_events_are_agent_visible(service):
    await service.dispatch("canvas.show", {"kind":"a2ui", "surface":surface()}, origin="agent")
    await service.dispatch("canvas.event", {"surfaceId":"test", "componentId":"button", "name":"review"})
    result = service.get_state()
    assert result["canvas"]["events"][0]["name"] == "review"
    assert result["canvas"]["events"][0]["origin"] == "ui"
    assert not result["sessions"]  # Interaction cannot covertly start a paid turn.
    for args in [{"surfaceId":"old", "componentId":"button", "name":"review"}, {"surfaceId":"test", "componentId":"text", "name":"review"}, {"surfaceId":"test", "componentId":"button", "name":"execute"}]:
        with pytest.raises(AppError):
            await service.dispatch("canvas.event", args)


@pytest.mark.parametrize("modify", [
    lambda s: s["components"][0]["component"]["Column"]["children"].update(explicitList=["column"]),
    lambda s: s["components"][0]["component"]["Column"]["children"].update(explicitList=["missing"]),
    lambda s: s["components"][2].update(component={"WebView":{"url":"https://example.com"}}),
    lambda s: s["components"][2]["component"]["Text"]["text"].update(path="/secret"),
    lambda s: s["components"][1]["component"]["Button"]["action"].update(script="alert(1)"),
    lambda s: s["components"].append(s["components"][0]),
])
def test_a2ui_rejects_cycles_missing_references_and_unsupported_content(modify):
    value = surface()
    modify(value)
    with pytest.raises(AppError):
        validate_surface(value)


async def test_canvas_layout_is_bounded_and_agent_controlled(service):
    await service.dispatch("view.update", {"patch":{"canvasWidth":450,"navPinned":True,"navExpanded":False,"canvasDraft":{"path":"plan.md","kind":"markdown"}}}, origin="agent")
    assert service.get_state()["view"]["canvasWidth"] == 450
    for patch in [{"canvasWidth":-100}, {"canvasWidth":100000}, {"canvasWidth":True}, {"navPinned":"yes"}]:
        with pytest.raises(AppError):
            await service.dispatch("view.update", {"patch":patch})


async def test_selecting_chat_restores_its_workspace_without_changing_paths(service, tmp_path):
    first = service.state["workspaces"][0]
    await service.dispatch("session.create", {"title":"First"})
    first_session = service.state["selectedSessionId"]
    original = service._session(first_session)
    original["workers"] = [{"id":"worker", "workspace":"unchanged"}]
    second = tmp_path / "unregistered"
    second.mkdir()
    await service.dispatch("session.create", {"title":"Second", "workspace":str(second)})
    assert any(w["path"] == str(second) for w in service.state["workspaces"])
    assert service.state["selectedWorkspaceId"] != first["id"]
    await service.dispatch("session.select", {"id":first_session})
    assert service.state["selectedWorkspaceId"] == first["id"]
    assert service.state["settings"]["workspace"] == first["path"]
    assert original["workspace"] == first["path"]
    assert original["workers"] == [{"id":"worker", "workspace":"unchanged"}]


def test_a2ui_depth_limit_holds_regardless_of_component_order():
    components = [{"id":"leaf", "component":{"Text":{"text":{"literalString":"Deep"}}}}]
    child = "leaf"
    for index in range(21):
        identity = f"card-{index}"
        components.append({"id":identity,"component":{"Card":{"child":child}}})
        child = identity
    with pytest.raises(AppError, match="20 levels"):
        validate_surface({"surfaceId":"deep", "root":child, "components":components})


async def test_rich_canvas_detection_controls_and_stale_reports(service, tmp_path):
    for name, content, kind in [('flow.mmd','graph LR; A-->B','mermaid'), ('graph.gv','digraph {a->b}','dot'), ('page.html','<button onclick="this.textContent=42">Test</button>','html'), ('data.json','{"a":1}','json')]:
        (tmp_path/'workspace'/name).write_text(content)
        await service.dispatch('canvas.show', {'kind':'auto','path':name}, origin='agent')
        canvas = service.state['canvas']
        assert canvas['kind'] == kind and canvas['content'] == content
    identity = canvas['id']
    await service.dispatch('canvas.view', {'id':identity,'patch':{'source':True,'zoom':2,'engine':'neato'}}, origin='agent')
    await service.dispatch('canvas.report', {'id':identity,'part':'preview','status':'error','message':'Bad diagram'})
    assert service.get_state()['canvas']['renderReports']['preview']['message'] == 'Bad diagram'
    await service.dispatch('canvas.show', {'kind':'text','content':'Replacement'})
    await service.dispatch('canvas.report', {'id':identity,'part':'preview','status':'ready'})
    assert service.state['canvas']['renderReports'] == {}  # Late reports cannot affect the replacement.
    assert service.state['canvas']['view'] == {}
    with pytest.raises(AppError):
        await service.dispatch('canvas.view', {'id':service.state['canvas']['id'],'patch':{'zoom':10}})


async def test_canvas_exports_use_shared_device_actions(service):
    await service.dispatch('canvas.show', {'kind':'html','content':'<h1>Hello</h1>'}, origin='agent')
    identity = service.state['canvas']['id']
    result = await service.dispatch('canvas.download', {'id':identity}, origin='agent')
    assert result['effects'][0]['filename'] == 'canvas.html'
    assert result['state']['deviceCommands'][-1]['type'] == 'download'
    result = await service.dispatch('canvas.copy', {'id':identity})
    assert result['effects'][0]['type'] == 'clipboard.write'


async def test_html_standard_controls_are_visible_and_agent_operable(service):
    await service.dispatch('canvas.show', {'kind':'html','content':'<button>Run</button>'})
    identity = service.state['canvas']['id']
    await service.dispatch('canvas.snapshot', {'id':identity,'document':{'text':'Run','controls':[{'id':'run','tag':'button','type':'button','label':'Run','value':'','disabled':False}]}})
    await service.dispatch('canvas.interact', {'id':identity,'controlId':'run','event':'click'}, origin='agent')
    assert service.get_state()['canvas']['interaction']['controlId'] == 'run'
    with pytest.raises(AppError, match='enabled control'):
        await service.dispatch('canvas.interact', {'id':identity,'controlId':'invented','event':'click'})
    await service.dispatch('canvas.view', {'id':identity,'patch':{'source':True}})
    assert 'document' not in service.state['canvas'] and 'interaction' not in service.state['canvas']
