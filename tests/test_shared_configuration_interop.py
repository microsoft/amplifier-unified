"""The installed CLI and web host resolve the same native settings scopes."""
import pytest

from amplifier_web.shared_settings import read_settings, settings_paths, update_settings


def test_cli_and_web_read_session_provider_choice_and_unknown_fields(tmp_path, monkeypatch):
    pytest.importorskip("amplifier_app_cli")
    from amplifier_app_cli.lib.settings import AppSettings
    from amplifier_app_cli.project_utils import get_project_slug
    workspace = tmp_path / "multi repo workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    paths = settings_paths(workspace, session_id="same-native-id")
    update_settings(paths["global"], lambda value: {
        "config": {"providers": [
            {"id": "personal", "module": "provider-example", "config": {"default_model": "global-model", "account": "one"}},
            {"id": "work", "module": "provider-example", "config": {"default_model": "other-model", "account": "two"}},
        ]},
        "voice": {"preferred_model": "shared-voice"}, "custom": {"keep": True},
    })
    update_settings(paths["session"], lambda value: {
        "config": {"providers": [
            {"id": "work", "module": "provider-example", "config": {"default_model": "session-model"}},
        ]}, "configurator": {"disabled": {"tools": ["test-tool"]}},
    })
    before = {scope: path.read_bytes() for scope, path in paths.items() if path.exists()}
    cli = AppSettings().with_session("same-native-id", get_project_slug())
    web = read_settings(workspace, session_id="same-native-id")
    assert cli.get_merged_settings() == web
    assert cli.get_provider_overrides() == web["config"]["providers"]
    assert web["config"]["providers"][1]["config"] == {"default_model": "session-model", "account": "two"}
    assert web["voice"]["preferred_model"] == "shared-voice" and web["custom"]["keep"]
    assert before == {scope: path.read_bytes() for scope, path in paths.items() if path.exists()}
