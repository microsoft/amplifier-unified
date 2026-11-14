"""The delete choice of the Windows uninstall: portable order and rules, and the real thing on native Windows."""
import pytest

from amplifier_web import windows_removal as removal

from test_windows_removal import file, folder, link, opened  # noqa: F401  (opened is a fixture)


def test_holding_tells_an_empty_folder_in_use_from_an_entry_that_still_holds_something(opened):
    outside = folder(precious=file())
    root = folder(workspace=folder(_held=True), app=folder(_held=True, chat=file(held=True)), shared=folder(),
                  keys=file(), junction=link(outside), APP=folder(other=file()))
    tree = opened["use"](root)
    with removal.folder(opened["path"]) as owned:
        # What uninstall does: delete, then ask what of the data is still there.
        result = owned.remove(["workspace", "app"])
        assert result["left"] == ["workspace", "app\\chat"] and result["leftCount"] == 2
        tree.opened.clear()
        held = owned.holding(["workspace", "app", "shared", "keys", "junction", "absent"])
    # The empty folder that a program keeps open is not data. Exact names only.
    assert held == ["app", "junction", "keys"]
    assert sorted(root["children"]) == ["APP", "app", "junction", "keys", "shared", "workspace"]
    # Only real folders were opened, for listing and never for deletion; the link was not opened at all.
    assert sorted(tree.opened) == [("app", True, False), ("shared", True, False), ("workspace", True, False)]
    assert outside not in tree.listed and not tree.handles


@pytest.mark.parametrize("change", ["link", "refused"])
def test_holding_counts_what_it_cannot_examine_and_never_enters_a_link(opened, change):
    outside = folder(precious=file())
    root = folder(app=folder())
    tree = opened["use"](root)
    def cross(parent, name):
        if change == "refused":
            raise PermissionError(5, "Access is denied")
        parent["children"]["app"] = link(outside)  # Swapped after the listing.
    tree.before_open = cross
    with removal.folder(opened["path"]) as owned:
        assert owned.holding(["app"]) == ["app"]
    assert outside not in tree.listed and outside["children"].keys() == {"precious"} and not tree.handles
