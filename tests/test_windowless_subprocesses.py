"""Source-level coverage complements actual native console observations.

These named helpers capture child output; none is an interactive terminal.
The native guard/payload and catalog tests check GetConsoleWindow, not just flags.
"""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("path,function", [
    ("runtime_qualification.py", "_installed_checkout_environment"),
    ("runtime_qualification.py", "_installed_checkout_dirty"),
    ("runtime_qualification.py", "installed_graph"),
    ("runtime_retained_dependencies.py", "verify"),
    ("host/components.py", "portable_module_sources"),
    ("host/context_source.py", "_git"),
    ("runtime_controls.py", "export_resources"),
    ("update_retention.py", "_recorded_cache"),
    ("application_generations.py", "verify_candidate"),
    ("windows_app_recovery.py", "restore"),
    ("windows_consumer.py", "audit"),
    ("app_features.py", "_managed_browser_path"),
    ("decision_accounts.py", "_catalog_snapshot"),
    ("bundles.py", "git"),
    ("windows_jobs.py", "_start"),
    ("windows_launch_guard.py", "main"),
])
def test_captured_update_children_have_explicit_console_policy(path, function):
    root = Path(__file__).resolve().parents[1] / "amplifier_web"
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function]
    assert len(nodes) == 1, (path, function)
    children = [node for node in ast.walk(nodes[0]) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"subprocess", "asyncio"}
                and node.func.attr in {"run", "Popen", "check_output", "create_subprocess_exec"}]
    assert children, (path, function)
    for child in children:
        flags = next((item.value for item in child.keywords if item.arg == "creationflags"), None)
        assert flags is not None, (path, function, child.lineno)
        assert "CREATE_NO_WINDOW" in ast.unparse(flags), (path, child.lineno)