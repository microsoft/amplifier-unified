"""The app consumes an external bundle and changes only the requested test policy."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import amplifier_foundation as foundation
import pytest
import yaml


@pytest.mark.asyncio
async def test_external_profile_loads_and_comparison_changes_only_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "external-bundle.md"
    source.write_text("""---
bundle:
  name: external-test
session:
  orchestrator:
    module: loop-live
    config:
      background_delegate: false
  context:
    module: context-managed
    config:
      engine: boundary
tools:
  - module: tool-example
providers:
  - module: provider-example
    config:
      default_model: selected-model
---
Preserve these external instructions.
""")
    spec = importlib.util.spec_from_file_location("work_acceptance", Path(__file__).resolve().parents[1] / "acceptance.py")
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    plans = []
    for profile in ("work", "baseline"):
        directory = tmp_path / profile
        directory.mkdir()
        args = SimpleNamespace(bundle=str(source), profile=profile, scenario="compaction")
        path, _ = await acceptance.make_bundle(directory, args)
        loaded = await foundation.load_bundle(str(path), strict=True)
        assert loaded.instruction == "Preserve these external instructions."
        assert loaded.providers[0]["config"]["default_model"] == "selected-model"
        plan = yaml.safe_load(path.read_text().split("---", 2)[1])
        assert plan["session"]["context"]["config"]["max_tokens"] == 24000
        assert plan["session"]["context"]["module"] == ("context-managed" if profile == "work" else "context-simple")
        plan.pop("bundle")
        plan["session"].pop("context")
        plans.append(plan)
    assert plans[0] == plans[1]
