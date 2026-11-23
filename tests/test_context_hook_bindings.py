"""Inheritance carries a named static destination binding; only the selected mount resolves it."""
import copy

import pytest
import yaml

from amplifier_web.bundles import BundleManager
from amplifier_web.host import hook_config
from amplifier_web.host.config import MissingConfiguredEnvironment
from amplifier_web.host.session import _expand_module_configuration
from amplifier_web.provider_environment import credential_bindings
from amplifier_web.runtime_controls import REDACTED, public_config, restore_redactions


def hook(destinations):
    return {"module": hook_config.MODULE, "id": "capture", "source": "git+https://github.com/microsoft/example@123456789",
            "config": {"destinations": destinations}}


def destination(name, **options):
    return {"url": "https://" + name + ".invalid", "include": ["**"], **options}


@pytest.mark.parametrize("reference", ["${TEST_KEY}", "${TEST_KEY:}", "${TEST_KEY:-}"])
def test_source_snapshot_nested_plan_and_inherited_worker_keep_name(monkeypatch, reference):
    monkeypatch.setenv("TEST_KEY", "private-value")
    config = {"destinations": {"first": destination("first", api_key=reference),
                               "other": destination("other")}}
    row = hook(config["destinations"])
    snapshot = {"agents": {"child": {"hooks": [row]}}}
    saved = _expand_module_configuration(snapshot)
    assert saved == credential_bindings(snapshot) == snapshot
    assert hook_config.declared_config(config) == config
    for key in ["first", "other"]:
        assert hook_config.mounted_config(config)["destinations"][key] == (
            {**config["destinations"][key], "api_key": "private-value"} if key == "first"
            else config["destinations"][key])
    assert config["destinations"]["first"]["api_key"] == reference
    monkeypatch.setenv("TEST_KEY", "rotated")
    assert hook_config.mounted_config(config)["destinations"]["first"]["api_key"] == "rotated"


def test_unavailable_reference_and_optional_local_logging(monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    config = {"destinations": {"selected": destination("selected", api_key="${TEST_KEY}")}}
    with pytest.raises(MissingConfiguredEnvironment):
        hook_config.declared_config(config)
    config["destinations"]["selected"].update(api_key="${TEST_KEY:}", include=[])
    declared = hook_config.declared_config(config)
    assert declared == config
    assert hook_config.mounted_config(declared)["destinations"]["selected"]["api_key"] == ""


def test_legacy_literals_stay_in_the_source_and_never_supply_another_worker(monkeypatch):
    monkeypatch.setenv("UNRELATED", "old-value")
    entries = {"selected": destination("selected", api_key="old-value"),
               "absent": destination("another"), "inactive": destination("inactive", include=[]),
               "local-only": {}, "entra": destination("entra", auth_mode="entra", auth_resource="resource")}
    source = {"hooks": [hook(entries)]}
    inherited = copy.deepcopy(source)
    diagnostic = hook_config.omit_inherited_literals(inherited)
    assert len(diagnostic["warnings"]) == 1 and "named environment reference" in diagnostic["warnings"][0]
    assert "old-value" not in str(diagnostic)
    selected = inherited["hooks"][0]["config"]["destinations"]
    assert selected["selected"] == destination("selected")
    assert {k: v for k, v in selected.items() if k != "selected"} == {
        k: v for k, v in entries.items() if k != "selected"}
    assert source["hooks"][0]["config"]["destinations"]["selected"]["api_key"] == "old-value"
    assert hook_config.mounted_config(inherited["hooks"][0]["config"])["destinations"] == selected


@pytest.mark.parametrize("reference", ["${TEST_KEY}", "${TEST_KEY:}"])
def test_redacted_destination_edits_and_portable_export(tmp_path, monkeypatch, reference):
    monkeypatch.setenv("TEST_KEY", "old-value")
    row = hook({"selected": destination("first", api_key=reference)})
    edited = public_config(row)
    assert edited["config"]["destinations"]["selected"]["api_key"] == REDACTED
    assert restore_redactions(edited, row) == row
    edited["config"]["destinations"]["selected"]["url"] = "https://changed.invalid"
    with pytest.raises(ValueError, match="changed Context Intelligence destination"):
        restore_redactions(edited, row)
    edited["config"]["destinations"]["selected"]["api_key"] = "${SECOND_KEY}"
    assert restore_redactions(edited, row) == edited
    # The normal export takes a redacted inspection and explicitly authored settings.
    exported = BundleManager(tmp_path).export_document({"config": {"hooks": [row]}},
        effective_plan={"hooks": [public_config(row)]}, resources={"instruction": ""})
    document = yaml.safe_load(exported["content"].split("---", 2)[1])
    assert "old-value" not in exported["content"]
    assert ("TEST_KEY" in exported["requiredEnvironment"]) == (reference == "${TEST_KEY}")
    selected = document["hooks"][0]["config"]["destinations"]["selected"]
    assert selected == row["config"]["destinations"]["selected"]
    assert hook_config.mounted_config(document["hooks"][0]["config"])["destinations"]["selected"]["api_key"] == "old-value"

    redacted = public_config(row)
    redacted["config"]["destinations"]["selected"]["url"] = "https://changed.invalid"
    changed = BundleManager(tmp_path).export_document({"config": {"hooks": [row]}},
        effective_plan={"hooks": [redacted]}, resources={"instruction": ""})
    assert "TEST_KEY" not in changed["content"] and "old-value" not in changed["content"]


def test_destination_names_are_not_secret_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAPH_KEY", "local-test-only")
    row = hook({"private_key": destination("one", api_key="${GRAPH_KEY}")})
    assert credential_bindings(row) == row
    visible = public_config(row)
    assert visible["config"]["destinations"]["private_key"]["api_key"] == REDACTED
    exported = BundleManager(tmp_path).export_document({"config": {"hooks": [row]}},
        effective_plan={"hooks": [visible]}, resources={"instruction": ""})
    assert "local-test-only" not in exported["content"]
    assert yaml.safe_load(exported["content"].split("---", 2)[1])["hooks"] == [row]


def test_no_new_warning_for_disabled_local_optional_or_unauthored_config():
    row = hook({"off": destination("off", include=[], api_key="opaque"),
                "absent": destination("absent"),
                "optional": destination("optional", api_key="${EMPTY:}"),
                "entra": destination("entra", auth_mode="entra", api_key="ignored")})
    disabled = {**hook({"off": destination("off", api_key="opaque")}), "enabled": False}
    agent = hook({"child": destination("child", api_key="opaque")})
    assert hook_config.omit_inherited_literals({"hooks": [row, disabled],
                                                "agents": {"specialist": {"hooks": [agent]}}}) == {}
