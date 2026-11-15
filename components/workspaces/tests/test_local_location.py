"""A local-location view neither migrates nor owns registration records."""
import copy

import pytest

from amplifier_workspace_metadata import LocalLocation, edit, local_location, reconcile


@pytest.mark.parametrize("path", ["/work/Reports", r"C:\Work\Reports", "~/notes/../Résumé"])
def test_location_preserves_existing_identity_and_exact_path(path):
    row = {"id": "existing-path-derived-id", "name": "Reports", "path": path, "available": True}

    assert local_location(row) == LocalLocation(row["id"], path, True)
    assert row["path"] == path


@pytest.mark.parametrize("fields", [{}, {"path": None}, {"path": ""}])
def test_unresolved_registration_has_no_location_or_record_changes(fields):
    row = {"id": "native-placeholder", "nativeProject": "reports", "available": False, **fields}
    before = dict(row)

    assert local_location(row) is None
    assert row == before


@pytest.mark.parametrize("fields, expected", [
    ({"available": False}, False),
    ({"available": True}, True),
    ({"available": None}, None),
    ({}, None),
    ({"available": 0}, 0),
])
def test_availability_is_the_recorded_observation(fields, expected):
    row = {"id": "reports", "path": "/missing/reports", **fields}

    location = local_location(row)

    assert location is not None
    assert location.path == row["path"]
    assert location.available is expected


def test_snapshot_is_immutable_and_later_reads_observe_the_single_path_owner():
    row = {"id": "kept", "path": "/old", "available": True}
    first = local_location(row)
    with pytest.raises(AttributeError):
        first.path = "/elsewhere"

    row.update(path="/new", available=False)
    assert first == LocalLocation("kept", "/old", True)
    assert local_location(row) == LocalLocation("kept", "/new", False)
    assert set(row) == {"id", "path", "available"}


def test_manual_edits_keep_location_and_opaque_custom_fields():
    custom = {"locations": [{"path": "/not-a-second-owner"}], "color": "blue"}
    row = {"id": "kept", "name": "Reports", "path": "/reports", "available": True,
           "aliases": ["Old display name"], "locations": custom["locations"], "custom": custom}
    rows = [row]
    before = copy.deepcopy(row)
    location = local_location(row)
    assert row == before and rows[0] is row
    assert row["custom"] is custom and row["locations"] is custom["locations"]

    edit(rows, "workspace.rename", {"id": "kept", "name": "Field notes"})
    edit(rows, "workspace.archive", {"id": "kept"})
    assert local_location(row) == location
    assert row["id"] == "kept" and row["aliases"] == ["Old display name"]
    assert row["custom"] is custom and row["locations"] is custom["locations"]


def test_unresolved_placeholder_keeps_existing_discovery_remap():
    placeholder = {"id": "placeholder", "name": "Reports", "path": None, "nativeProject": "project"}
    rows = [placeholder]
    assert local_location(placeholder) is None

    result = reconcile(rows, [], [
        {"id": "resolved", "name": "project", "path": "/project", "nativeProject": "project", "available": False},
    ], hidden_ids=[], managed_paths=set())

    assert result.remaps == [("placeholder", "resolved", "/project")]
    assert local_location(rows[0]) == LocalLocation("resolved", "/project", False)
    assert rows[0]["name"] == "Reports"
    assert placeholder["id"] == "placeholder" and placeholder["path"] is None
