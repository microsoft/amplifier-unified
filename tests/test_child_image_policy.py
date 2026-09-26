"""Image authority remains narrower through real Foundation child composition."""
import asyncio
import copy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_foundation import Bundle
from amplifier_web.host.children import Children, child_plan
from amplifier_web.host.storage import SessionStore


def image(config, **identity):
    return {"module": "tool-image", "source": "fixture-image-source", "config": config, **identity}


@pytest.mark.parametrize("parent_paths,child_paths,expected", [
    (["."], ["images"], ["images"]),
    (["images"], ["."], ["images"]),
    (["images"], ["other"], []),
    (["."], [], []),
    ([], ["images"], []),
    (["images", "other"], ["images/nested", "elsewhere"], ["images/nested"]),
])
@pytest.mark.parametrize("kind", ["read", "write"])
def test_child_image_allow_lists_intersect_in_runtime_workspace(tmp_path, parent_paths, child_paths, expected, kind):
    key = f"allowed_{kind}_paths"
    parent = {"tools": [image({"backend": "images", "allow_paid": True, key: parent_paths})]}
    overlay = {"tools": [image({key: child_paths})]}
    before = copy.deepcopy((parent, overlay))
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    assert plan["tools"][0]["config"][key] == [str(tmp_path / value) for value in expected]
    assert (parent, overlay) == before
    assert plan["tools"][0]["config"]["backend"] == "images"


@pytest.mark.parametrize("parent_paid,child_paid", [(True, False), (False, True), (False, None)])
def test_child_retains_paid_denial_and_both_file_denials(tmp_path, parent_paid, child_paid):
    parent = {"tools": [image({"backend": "images", "allow_paid": parent_paid,
                                "denied_read_paths": ["private"], "denied_write_paths": ["originals"]})]}
    config = {"backend": "child-backend", "denied_read_paths": ["private", "inputs"], "denied_write_paths": []}
    if child_paid is not None:
        config["allow_paid"] = child_paid
    plan = child_plan(parent, {"tools": [image(config)]}, working_dir=tmp_path)
    actual = plan["tools"][0]["config"]
    assert actual["allow_paid"] is False
    assert actual["backend"] == "child-backend"
    assert actual["denied_read_paths"] == [str(tmp_path / "private"), str(tmp_path / "inputs")]
    assert actual["denied_write_paths"] == [str(tmp_path / "originals")]


@pytest.mark.parametrize("value", [None, False, 0, 1, "true", "omitted"])
@pytest.mark.parametrize("boundary", ["parent", "spawn", "child"])
def test_effectively_disabled_image_declaration_cannot_gain_paid_authority(tmp_path, value, boundary):
    disabled = {} if value == "omitted" else {"allow_paid": value}
    parent = {"tools": [image(disabled if boundary == "parent" else {"allow_paid": True})]}
    if boundary == "spawn":
        parent["spawn"] = {"tools": [image(disabled)]}
    overlay = {"tools": [image(disabled if boundary == "child" else {"allow_paid": True})]}
    config = child_plan(parent, overlay, working_dir=tmp_path)["tools"][0]["config"]
    assert config["allow_paid"] is (boundary == "child" and value == "omitted")


def test_spawn_policy_and_new_instance_cannot_escape_parent_image_boundary(tmp_path):
    parent = {"tools": [image({"allow_paid": False, "allowed_write_paths": ["images"]})],
              "spawn": {"tools": [image({"allowed_write_paths": ["images/nested"]}, instance_id="spawn")]}}
    overlay = {"tools": [image({"allow_paid": True, "allowed_write_paths": ["."]}, instance_id="child")]}
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    assert len(plan["tools"]) == 2  # Preserve existing instance composition, constrain both.
    for row in plan["tools"]:
        assert row["config"]["allow_paid"] is False
        assert row["config"]["allowed_write_paths"] == [str(tmp_path / "images/nested")]


def test_excluded_image_is_not_reintroduced_and_other_module_lists_stay_additive(tmp_path):
    parent = {"tools": [image({"allow_paid": True}),
                        {"module": "tool-skills", "config": {"skills": ["existing"]}}]}
    overlay = {"tools": [{"module": "tool-skills", "config": {"skills": ["added"]}}]}
    plan = child_plan(parent, overlay, tool_inheritance={"exclude_tools": ["tool-image"]}, working_dir=tmp_path)
    assert plan["tools"] == [{"module": "tool-skills", "config": {"skills": ["existing", "added"]}}]


@pytest.mark.parametrize("allowed", [[], ["images"]])
def test_inherited_image_obeys_child_filesystem_policy_without_image_redeclaration(tmp_path, allowed):
    parent = {"tools": [image({"backend": "images", "allow_paid": True,
                                "allowed_write_paths": ["."], "denied_write_paths": ["private"]})]}
    overlay = {"tools": [{"module": "tool-filesystem", "config": {
        "allowed_write_paths": allowed, "denied_write_paths": ["images/locked"]}}]}
    plan = child_plan(parent, overlay, working_dir=tmp_path)
    actual = plan["tools"][0]["config"]
    assert actual["allowed_write_paths"] == [str(tmp_path / path) for path in allowed]
    assert actual["denied_write_paths"] == [str(tmp_path / "private"), str(tmp_path / "images/locked")]
    assert actual["allow_paid"] is True


@pytest.mark.parametrize("invalid", ["images", [None], {"path": "images"}])
def test_malformed_child_image_path_policy_fails_before_mount(tmp_path, invalid):
    with pytest.raises(ValueError, match="lists of strings"):
        child_plan({"tools": [image({"allowed_read_paths": ["."]})]},
                   {"tools": [image({"allowed_read_paths": invalid})]}, working_dir=tmp_path)


def test_child_image_environment_paths_resolve_before_intersection(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_CHILD_TEST_DIR", "images")
    plan = child_plan({"tools": [image({"allowed_read_paths": [str(tmp_path)]})]},
                      {"tools": [image({"allowed_read_paths": ["${IMAGE_CHILD_TEST_DIR}"]})]}, working_dir=tmp_path)
    assert plan["tools"][0]["config"]["allowed_read_paths"] == [str(tmp_path / "images")]


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_prepared_child_mount_and_bundle_share_narrowed_policy_before_execution(tmp_path, monkeypatch, resumed):
    workspace = tmp_path / "execution"
    workspace.mkdir()
    elsewhere = tmp_path / "server"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    captured = {}

    class StopBeforeMount(Exception):
        pass

    @dataclass
    class Prepared:
        bundle: Bundle
        mount_plan: dict

        async def create_session(self, **kwargs):
            captured.update(plan=self.mount_plan, tools=self.bundle.tools, kwargs=kwargs)
            raise StopBeforeMount

    parent_plan = {"tools": [image({"backend": "images", "allow_paid": True,
                                     "allowed_read_paths": [str(workspace)],
                                     "allowed_write_paths": [str(workspace)],
                                     "denied_write_paths": [str(workspace / "private")]})]}
    coordinator = SimpleNamespace(config=parent_plan, get=lambda _: None,
        get_capability=lambda name: str(workspace) if name == "session.working_dir" else None)
    parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), SessionStore(tmp_path / "history"), None)
    children.prepared["parent"] = Prepared(Bundle(name="parent", tools=copy.deepcopy(parent_plan["tools"])), parent_plan)
    overlay = {"tools": [image({"allow_paid": False, "allowed_read_paths": [],
                                "allowed_write_paths": ["images"], "denied_write_paths": ["images/no"]})]}
    with pytest.raises(StopBeforeMount):
        await children._execute("child", "must not execute", parent, "worker", overlay, resumed=resumed)
    mounted = captured["plan"]["tools"][0]["config"]
    assert mounted["allowed_write_paths"] == [str(workspace / "images")]
    assert mounted["allowed_read_paths"] == []
    assert mounted["denied_write_paths"] == [str(workspace / "private"), str(workspace / "images/no")]
    assert mounted["allow_paid"] is False
    assert captured["tools"] == captured["plan"]["tools"]
    assert captured["kwargs"]["session_cwd"] == workspace
    assert captured["kwargs"]["is_resumed"] is resumed
    assert not children.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_scope", ["absent", "restricted", "broad", "omitted-paid"])
async def test_actual_resume_preserves_saved_image_authority_and_current_parent_limits(tmp_path, saved_scope):
    workspace = tmp_path / "execution"
    workspace.mkdir()
    captured = {}

    class StopBeforeMount(Exception):
        pass

    @dataclass
    class Prepared:
        bundle: Bundle
        mount_plan: dict

        async def create_session(self, **kwargs):
            captured.update(plan=self.mount_plan, tools=self.bundle.tools, kwargs=kwargs)
            raise StopBeforeMount

    parent_plan = {"tools": [image({"backend": "images", "allow_paid": True,
                                     "allowed_write_paths": [str(workspace / "images")],
                                     "denied_write_paths": [str(workspace / "images/private")]})]}
    coordinator = SimpleNamespace(config=parent_plan, get=lambda _: None,
        get_capability=lambda name: str(workspace) if name == "session.working_dir" else None)
    parent = SimpleNamespace(session_id="parent", coordinator=coordinator)
    store = SessionStore(tmp_path / "history")
    children = Children(SimpleNamespace(inbox=asyncio.Queue()), store, None)
    children.prepared["parent"] = Prepared(Bundle(name="parent", tools=copy.deepcopy(parent_plan["tools"])), parent_plan)
    saved_config = {"backend": "images", "allow_paid": False,
                    "allowed_write_paths": [str(workspace / "images/retained")],
                    "denied_write_paths": [str(workspace / "images/retained/originals")]}
    if saved_scope == "broad":
        saved_config.update(allow_paid=True, allowed_write_paths=[str(workspace)])
    elif saved_scope == "omitted-paid":
        saved_config.pop("allow_paid")
    saved_tools = [] if saved_scope == "absent" else [image(saved_config)]
    # An excluded image stays absent even with an empty saved overlay. An old
    # permissive overlay must also retain the recorded effective restrictions.
    store.save("child", [{"role": "user", "content": "Retained original"}], {
        "parent_id": "parent", "agent_name": "worker",
        "agent_overlay": {} if saved_scope == "absent" else {
            "tools": [image({"allow_paid": True, "allowed_write_paths": ["."]})]},
        "mount_plan": {"tools": saved_tools}})
    original = {path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()}
    with pytest.raises(StopBeforeMount):
        await children.resume("child", "must not execute", parent)
    image_rows = [row for row in captured["plan"]["tools"] if row["module"] == "tool-image"]
    if saved_scope == "absent":
        assert image_rows == []
    else:
        config = image_rows[0]["config"]
        assert config["allowed_write_paths"] == [str(workspace / ("images" if saved_scope == "broad" else "images/retained"))]
        assert config["allow_paid"] is (saved_scope == "broad")
        assert set(config["denied_write_paths"]) == {str(workspace / "images/private"), str(workspace / "images/retained/originals")}
    assert [row for row in captured["tools"] if row["module"] == "tool-image"] == image_rows
    assert captured["kwargs"]["is_resumed"] is True
    assert original == {path: path.read_bytes() for path in (tmp_path / "history").rglob("*") if path.is_file()}
