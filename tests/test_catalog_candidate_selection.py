"""Source-only controls for explicit catalog graphs; no installed providers needed."""
import builtins
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


FIXTURES = Path(__file__).with_name("fixtures")
HELPER = FIXTURES / "catalog_candidate_transports.py"
GRAPH = "filelock4-litellm-1.108.3"
SUCCESSOR = "canonical_model_catalog_filelock4.json"


def load_graph(**selection):
    environment = dict(os.environ)
    for name in ("UNIFIED_CATALOG_CANDIDATES", "UNIFIED_CATALOG_ACCEPTED", "UNIFIED_CATALOG_GRAPH"):
        environment.pop(name, None)
    environment.update(selection)
    original_import = builtins.__import__

    def source_only(name, *args, **kwargs):
        if name.startswith(("amplifier_", "litellm", "google", "httpx", "copilot")):
            raise AssertionError("Graph selection must not import the application or providers")
        return original_import(name, *args, **kwargs)

    with patch.dict(os.environ, environment, clear=True), patch("builtins.__import__", source_only):
        return runpy.run_path(str(HELPER))


class CatalogCandidateSelectionTests(unittest.TestCase):
    def test_historical_manifests_stay_byte_exact(self):
        for name, expected in {
            "canonical_model_catalog_candidate.json": "86ed795a4a9b44434e8a83cb196e1f9cb11809f0e8fdf9bfe5f3545fcfd70401",
            "model_catalog_accepted.json": "c66c72e6a059339028a99c926da5c638d48a42951ea6ffe6b6416219153bd3a8",
        }.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), expected)

    def test_successor_changes_only_label_and_litellm_sdk_identity(self):
        original = json.loads((FIXTURES / "canonical_model_catalog_candidate.json").read_text())
        expected = deepcopy(original)
        expected["status"] = "canonical-candidate-" + GRAPH
        expected["localProviders"]["litellm"]["sdk"].update(
            qualificationVersion="1.108.3",
            wheelSha256="ffd746c20138a078c451a851a68c85da43d67e114001e4a4cd4082953de5494d",
        )
        actual = json.loads((FIXTURES / SUCCESSOR).read_text())
        self.assertEqual(actual, expected)
        providers = {**actual["providers"], **actual["localProviders"]}
        self.assertEqual(len(providers), 10)
        self.assertEqual(sum(len(row["files"]) for row in providers.values()), 89)
        self.assertEqual(actual["localProviders"]["perplexity"], original["localProviders"]["perplexity"])

    def test_each_selection_loads_and_identifies_exact_manifest_without_imports(self):
        for selection, name in [
            ({}, "canonical_model_catalog_candidate.json"),
            ({"UNIFIED_CATALOG_ACCEPTED": "1"}, "model_catalog_accepted.json"),
            ({"UNIFIED_CATALOG_GRAPH": GRAPH}, SUCCESSOR),
            ({"UNIFIED_CATALOG_GRAPH": GRAPH, "UNIFIED_CATALOG_CANDIDATES": "1"}, SUCCESSOR),
        ]:
            with self.subTest(selection=selection):
                result = load_graph(**selection)
                raw = (FIXTURES / name).read_bytes()
                self.assertEqual(result["CANDIDATE_MANIFEST"].name, name)
                self.assertEqual(result["CANDIDATE_MANIFEST_SHA256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(result["CANDIDATES"], json.loads(raw))

    def test_unknown_or_ambiguous_selection_is_not_a_fallback(self):
        for value in ("", "1", "latest", "filelock4", "../model_catalog_accepted.json", GRAPH + " "):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Unknown catalog graph"):
                load_graph(UNIFIED_CATALOG_GRAPH=value)
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            load_graph(UNIFIED_CATALOG_GRAPH=GRAPH, UNIFIED_CATALOG_ACCEPTED="1")

    def test_missing_or_malformed_selected_manifest_is_not_a_fallback(self):
        original_read = Path.read_bytes
        for failure in (FileNotFoundError("missing selected fixture"), b"{invalid"):
            def read(path):
                if path.name == SUCCESSOR:
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                return original_read(path)
            with self.subTest(failure=failure), patch.object(Path, "read_bytes", read):
                with self.assertRaises((FileNotFoundError, json.JSONDecodeError)):
                    load_graph(UNIFIED_CATALOG_GRAPH=GRAPH)

    def test_sdk_version_guard_rejects_old_newer_missing_and_wrong_graph_versions(self):
        for selection, expected, wrong in [
            ({}, "1.108.1", "1.108.3"),
            ({"UNIFIED_CATALOG_GRAPH": GRAPH}, "1.108.3", "1.108.1"),
        ]:
            helper = load_graph(**selection)
            candidate = helper["CANDIDATES"]["localProviders"]["litellm"]
            check = helper["_check_sdk_version"]
            with self.subTest(selection=selection):
                with patch("importlib.metadata.version", return_value=expected) as version:
                    check(candidate)
                    version.assert_called_once_with("litellm")
                for mismatch in (wrong, "1.110.3", "1.108.3+local"):
                    with self.subTest(mismatch=mismatch), patch("importlib.metadata.version", return_value=mismatch):
                        with self.assertRaisesRegex(AssertionError, "SDK differs"):
                            check(candidate)
                with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError("litellm")):
                    with self.assertRaises(importlib.metadata.PackageNotFoundError):
                        check(candidate)

    def test_graph_selection_does_not_enable_transport_without_original_opt_in(self):
        helper = load_graph(UNIFIED_CATALOG_GRAPH=GRAPH)
        with patch.dict(os.environ, {"UNIFIED_CATALOG_CANDIDATES": "0"}):
            with self.assertRaisesRegex(RuntimeError, "explicit test opt-in"):
                helper["install"]()


if __name__ == "__main__":
    unittest.main()
