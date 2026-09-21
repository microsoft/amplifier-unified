"""Real community recipe executor -> Unified child -> Rust Core, no model calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from standalone_children_probe import (
    Approvals, Bundle, BundleModuleResolver, ProbePrepared, Runtime, SessionStore,
    created, decisions, install_children,
)
import importlib.util

recipes = Path(os.environ["WARM_RECIPES_PATH"])
sys.path.insert(0, str(recipes / "modules/tool-recipes"))
from amplifier_module_tool_recipes.executor import RecipeExecutor
from amplifier_module_tool_recipes.models import Recipe, Step
from amplifier_module_tool_recipes.session import SessionManager


async def run():
    async def ask(prompt, choices):
        return "deny"

    plan = {
        "session": {"orchestrator": {"module": "loop-live"}, "context": {"module": "context-simple"}},
        "agents": {"worker": {"instruction": "Write a fixture report."}},
    }
    paths = {
        name: Path(importlib.util.find_spec("amplifier_module_" + name.replace("-", "_")).origin).parent
        for name in ("loop-live", "context-simple")
    }
    bundle = Bundle(name="recipe-fixture", session=plan["session"], agents=plan["agents"])
    prepared = ProbePrepared(plan, BundleModuleResolver(paths), bundle)
    approvals = Approvals(None, ask)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp).resolve()
        parent = await prepared.create_session(session_id="recipe-parent", approval_system=approvals, session_cwd=workspace)
        parent.coordinator.register_capability("model_role_resolver", "shared-model-resolver")
        store = SessionStore(workspace / "children")
        registry = await install_children(parent, prepared, Runtime(parent.session_id), store, approvals)
        executor = RecipeExecutor(parent.coordinator, SessionManager(workspace / "recipe-data"))
        recipe = Recipe(
            name="report-after-checks", description="Exercise the failing recipe seam", version="1.0.0",
            steps=[
                Step(id="checks", type="bash", command="printf 'fixture checks passed'", output="checks"),
                Step(id="report", agent="worker", prompt="Report on {{checks}}", output="report"),
            ],
        )
        try:
            result = await executor.execute_recipe(recipe, {}, workspace)
            assert result["report"] == "Finished **Report on fixture checks passed**", result
            child_row = registry.snapshot()[0]
            assert child_row["status"] == "completed"
            messages, metadata = store.load(child_row["sessionId"])
            assert metadata["parent_id"] == parent.session_id
            assert metadata["workspace"] == str(workspace)
            assert metadata["recipe_name"] == "report-after-checks"
            assert metadata["recipe_step"] == "report"
            assert messages[-1]["content"] == result["report"]
            assert decisions == ["deny"], "Recipe child must retain the app approval system"

            # An explicit isolation request must fail before any child executes.
            count = len(created)
            isolated = Step(id="isolated", agent="worker", prompt="Do not execute", spawn_mode="subprocess")
            try:
                await executor.execute_step(isolated, {})
            except ValueError as exc:
                assert "Subprocess child sessions are not supported" in str(exc), exc
            else:
                raise AssertionError("Subprocess mode was silently executed in-process")
            assert len(created) == count
        finally:
            await parent.cleanup()
    print(json.dumps({"recipe_report_completed": True, "approval_preserved": True, "subprocess_not_downgraded": True}))


if __name__ == "__main__":
    asyncio.run(run())
