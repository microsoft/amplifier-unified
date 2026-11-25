"""Offline checks for the pinned fixture recipe, not connector qualification."""
from contextlib import chdir
import importlib.util
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "connector_recipe", Path(__file__).resolve().parents[1] / "scripts/kiss502_connector_checks.py")
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


class ConnectorRecipe(unittest.TestCase):
    def test_fake_fixture_env_drops_live_credentials_and_host_overrides(self):
        output = Path("/artifacts/example")
        with patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "must-not-leak", "OPENAI_API_KEY": "must-not-leak",
            "ACP_SLACK_TEST_BINARY": "/wrong", "PYTHONPATH": "/host",
            "AMPLIFIER_HOME": "/host-settings", "HOME": "/real-user",
        }):
            env = recipe.fixture_env(output)
        self.assertNotIn("SLACK_BOT_TOKEN", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("AMPLIFIER_HOME", env)
        self.assertNotIn("ACP_SLACK_TEST_BINARY", env)
        self.assertEqual(env["HOME"], str(output / "home"))
        self.assertEqual(env["TMPDIR"], str(output / "tmp"))

    def test_selected_maintained_commands_do_not_run_a_game_or_live_account_tool(self):
        self.assertEqual(recipe.MC_TESTS, ("test_sdk_boundary", "test_session_resume", "test_reconnect"))
        self.assertEqual(recipe.SLACK_TESTS,
                         ("process_integration.py", "scoped_work.py", "slack_huddles.py"))
        self.assertEqual(len(recipe.SDK_COMMIT), 40)

    def test_archive_accepts_only_expected_regular_package_entries(self):
        rows = [tarfile.TarInfo(name) for name in
                ("LICENSE", "native-bundle.json", "acp-slack-adapter", "runtime/python/lib/a.py")]
        recipe.validate_members(rows)

    def test_archive_rejects_traversal_absolute_backslash_links_devices_and_duplicates(self):
        for name in ("/tmp/evil", "../evil", "runtime/../../evil", r"runtime\..\evil", "unexpected"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                recipe.validate_members([tarfile.TarInfo(name)])
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE):
            row = tarfile.TarInfo("runtime/evil")
            row.type = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                recipe.validate_members([row])
        with self.assertRaises(ValueError):
            recipe.validate_members([tarfile.TarInfo("LICENSE"), tarfile.TarInfo("LICENSE")])

    def test_archive_bounds_unpacked_size_and_entry_count(self):
        row = tarfile.TarInfo("runtime/huge")
        row.size = 500_000_001
        with self.assertRaises(ValueError):
            recipe.validate_members([row])
        with self.assertRaises(ValueError):
            recipe.validate_members([tarfile.TarInfo(f"runtime/{i}") for i in range(5001)])

    def test_full_check_cannot_start_on_this_host_without_parent_guest_contract(self):
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(recipe.platform, "system", return_value="Linux"), \
             patch.object(recipe.platform, "machine", return_value="aarch64"), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "parent's prepared guest"):
                recipe.check(Path("/not-artifacts"))

    def test_partial_prepare_retires_only_this_runs_work_and_keeps_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp):
            output = Path(tmp) / "results"
            neighbor = Path(tmp) / "other-run"
            neighbor.mkdir()

            def fail(results, inputs, archive):
                self.assertEqual(results, output)
                self.assertNotEqual(inputs, output)
                (inputs / "partial-download").touch()
                (results / "failed.log").write_text("source failure")
                raise KeyboardInterrupt

            with patch.object(recipe, "prepare_inputs", side_effect=fail), self.assertRaises(KeyboardInterrupt):
                recipe.prepare(output)
            self.assertEqual(set(Path(tmp).iterdir()), {output, neighbor})
            self.assertEqual((output / "failed.log").read_text(), "source failure")

    def test_existing_offline_layout_defaults_to_its_own_prepared_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp):
            output = Path(tmp) / "results"
            output.mkdir()
            recipe.write(output / "prepared.json", {})
            self.assertEqual(recipe.prepared_inputs(output), output)
            recipe.write(output / "prepared.json", {"workspaceInputs": True})
            inputs = recipe.prepared_inputs(output)
            self.assertEqual(inputs.parent, Path(tmp))
            self.assertNotEqual(inputs, output)
            self.assertNotEqual(inputs, recipe.workspace_inputs(Path(tmp) / "another-run"))

    def test_extracted_package_drift_is_refused_before_fixture_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            inputs = output.parent / "scratch"
            recipe.write(output / "slack-artifact.json",
                         {"payload": {"acp-slack-adapter": recipe.BINARY_HASH}})
            with patch.object(recipe, "digest", side_effect={
                    output / "slack-release-manifest.json": recipe.MANIFEST_HASH,
                    inputs / "slack.tar.gz": recipe.ARCHIVE_HASH,
                 }.__getitem__), \
                 patch.object(recipe, "package_inventory", return_value={"acp-slack-adapter": "changed"}) as inventory:
                with self.assertRaisesRegex(ValueError, "extracted payload drift"):
                    recipe.verify_package(output, inputs)
                inventory.assert_called_once_with(inputs / "slack-package")


if __name__ == "__main__":
    unittest.main()