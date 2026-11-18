#!/usr/bin/env python3
"""Compute the files and distributions used by the Windows launcher.

This is the measured static, name-based closure, applied to a working tree.
A loaded module runs its module-level code and imports. A reached definition
loads its imports and reaches the package names it mentions; a class reaches
all its methods. The app-host cuts run in the app generation's Python.
This over-counts names that are not called and cannot follow dynamic dispatch.
No application code is imported or run.
"""
import argparse
import ast
from pathlib import Path
import pprint
import re
import sys
import tomllib

PKG = "amplifier_web"
ENTRIES = {
    "core": [
        ("amplifier_web.windows_service", "_run"),
        ("amplifier_web.windows_consumer", "main"),
        ("amplifier_web.cli", None),
        ("amplifier_web.cli", "_connection_options"),
        ("amplifier_web.host_dependencies", "prepare_environment"),
        ("amplifier_web.application_generations", "delegate"),
        ("amplifier_web.application_generations", "verify_candidate"),
        ("amplifier_web.application_generations", "directory"),
    ],
    "rare": [
        ("amplifier_web.reset", "run"),
        ("amplifier_web.cli", "_parse"),
        ("amplifier_web.status_protocol.cli", "main"),
    ],
}
CUTS = {
    ("amplifier_web.windows_service", "_host"),
    ("amplifier_web.windows_service", "_serve_host"),
}
IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "PIL": "pillow",
    "amplifier_memory": "amplifier-memory",
    "jsonschema": "jsonschema",
}
TARGET = "amplifier_web/windows_launcher_files.py"
BEGIN = "# BEGIN LAUNCHER FILE LISTS"
END = "# END LAUNCHER FILE LISTS"
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
COMMAND = "python scripts/windows_launcher_files.py --write"


class Index:
    """Module name -> (path, tree, is_package), parsed only when needed."""

    def __init__(self, root):
        self.root, self.paths, self.trees, self.lines = Path(root), {}, {}, {}
        for file in sorted((self.root / PKG).rglob("*.py")):
            path = file.relative_to(self.root).as_posix()
            parts = path[:-3].split("/")
            package = parts[-1] == "__init__"
            self.paths[".".join(parts[:-1] if package else parts)] = (path, package)

    def __contains__(self, name):
        return name in self.paths

    def __getitem__(self, name):
        path, package = self.paths[name]
        if name not in self.trees:
            source = (self.root / path).read_text(encoding="utf-8")
            self.lines[name] = source.splitlines()
            try:
                self.trees[name] = ast.parse(source)
            except SyntaxError:
                self.trees[name] = ast.parse("")
        return path, self.trees[name], package


def is_main_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


class Closure:
    """The measurement's import and name rules, without executing a module."""

    def __init__(self, root, entries, cuts=CUTS):
        self.modules, self.cuts = Index(root), cuts
        self.loaded, self.reached, self.third, self.places = set(), set(), {}, set()
        self.scopes, self.defs, self.dynamic_aliases = {}, {}, {}
        for module, symbol in entries:
            if symbol is None:
                self.load(module)
            else:
                self.reach(module, symbol)

    def bindings(self, module, node):
        """Resolve imports to package modules or symbols, and record outside imports."""
        _, _, package = self.modules[module]
        found = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PKG or alias.name.startswith(PKG + "."):
                    self.load(alias.name)
                    found.append((alias.asname or PKG, ("module", alias.name if alias.asname else PKG)))
                else:
                    self.third.setdefault(module, set()).add(alias.name.split(".")[0])
            return found
        base = node.module or ""
        if node.level:
            parts = module.split(".") if package else module.split(".")[:-1]
            parts = parts[:len(parts) - (node.level - 1)]
            base = ".".join(parts + ([node.module] if node.module else []))
        if not (base == PKG or base.startswith(PKG + ".")):
            self.third.setdefault(module, set()).add(base.split(".")[0])
            return found
        self.load(base)
        for alias in node.names:
            child = base + "." + alias.name
            if child in self.modules:
                self.load(child)
                found.append((alias.asname or alias.name, ("module", child)))
            else:
                found.append((alias.asname or alias.name, ("symbol", base, alias.name)))
        return found

    def load(self, module):
        if module in self.loaded or module not in self.modules:
            return
        self.loaded.add(module)
        if "." in module:
            self.load(module.rsplit(".", 1)[0])
        tree = self.modules[module][1]
        scope, defs, level = {}, {}, []
        self.scopes[module], self.defs[module] = scope, defs
        for node in tree.body:
            if isinstance(node, DEFINITIONS):
                defs[node.name] = node
                scope[node.name] = ("symbol", module, node.name)
            elif not is_main_guard(node):
                level.append(node)
        self.process(module, level, module_level=True)

    def reach(self, module, symbol):
        if (module, symbol) in self.cuts or (module, symbol) in self.reached:
            return
        self.reached.add((module, symbol))
        self.load(module)
        if module not in self.modules:
            return
        if module + "." + symbol in self.modules:
            return self.load(module + "." + symbol)
        node = self.defs[module].get(symbol)
        if node is not None:
            return self.process(module, [node])
        target = self.scopes[module].get(symbol)
        if target and target[0] == "symbol" and (target[1], target[2]) != (module, symbol):
            self.reach(target[1], target[2])
        elif target and target[0] == "module":
            self.load(target[1])

    def dynamic_places(self, module, nodes, module_level):
        path = self.modules[module][0]
        kinds = {"__import__", "import_module", "spec_from_file_location"}
        aliases = dict(self.dynamic_aliases.get(module, {}))
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module in {"importlib", "importlib.util", "builtins"}:
                    for alias in sub.names:
                        if alias.name in kinds:
                            aliases[alias.asname or alias.name] = alias.name
        if module_level:
            self.dynamic_aliases[module] = aliases
        for node in nodes:
            enclosing = "<module>" if module_level else node.name
            for sub in ast.walk(node):
                kind = None
                if isinstance(sub, ast.Call):
                    name = (sub.func.id if isinstance(sub.func, ast.Name) else
                            sub.func.attr if isinstance(sub.func, ast.Attribute) else "")
                    name = aliases.get(name, name)
                    if name in kinds:
                        kind = name
                elif isinstance(sub, ast.Name) and sub.id == "__file__":
                    kind = "__file__"
                elif isinstance(sub, ast.Constant) and sub.value in ("-m", "-c"):
                    kind = sub.value
                if kind:
                    line = self.modules.lines[module][sub.lineno - 1].strip()[:120]
                    self.places.add(f"{path}::{enclosing}::{kind}::{line}")

    def process(self, module, nodes, module_level=False):
        self.dynamic_places(module, nodes, module_level)
        local = self.scopes[module] if module_level else {}
        walked = [sub for node in nodes for sub in ast.walk(node)]
        for sub in walked:
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                for name, target in self.bindings(module, sub):
                    local[name] = target

        def target_of(name):
            return local.get(name) or self.scopes[module].get(name)

        for sub in walked:
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                target = target_of(sub.id)
                if target and target[0] == "symbol":
                    self.reach(target[1], target[2])
            elif isinstance(sub, ast.Attribute):
                chain, value = [sub.attr], sub.value
                while isinstance(value, ast.Attribute):
                    chain.append(value.attr)
                    value = value.value
                if not isinstance(value, ast.Name):
                    continue
                target = target_of(value.id)
                if not target or target[0] != "module":
                    continue
                current = target[1]
                for attr in reversed(chain):
                    if current + "." + attr in self.modules:
                        current += "." + attr
                        self.load(current)
                    else:
                        self.reach(current, attr)
                        break

    def files(self):
        return sorted(self.modules[name][0] for name in self.loaded)

    def third_party(self):
        return sorted(set().union(*self.third.values()) - set(sys.stdlib_module_names)) if self.third else []


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _packages(root, imports):
    rows = {}
    for row in tomllib.loads((Path(root) / "uv.lock").read_text(encoding="utf-8")).get("package", []):
        # Several lock rows can have the same name under different markers.
        rows.setdefault(_normalize(row["name"]), set()).update(
            _normalize(dep["name"]) for dep in row.get("dependencies", []))
    seen, work = set(), []
    for name in imports:
        distribution = _normalize(IMPORT_TO_DIST.get(name, name))
        if distribution not in rows:
            raise ValueError(f"Cannot resolve import {name!r} in uv.lock.")
        work.append(distribution)
    while work:
        name = work.pop()
        if name in seen:
            continue
        if name not in rows:
            raise ValueError(f"Cannot resolve distribution {name!r} in uv.lock.")
        seen.add(name)
        work.extend(rows[name])
    return sorted(seen)


def _constants(root):
    path = Path(root) / TARGET
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"FILES", "PACKAGES", "DYNAMIC"}}


def compute(root) -> dict:
    """Return sorted files, distribution names and dynamic places for this tree."""
    closure = Closure(root, ENTRIES["core"] + ENTRIES["rare"])
    dynamic = _constants(root).get("DYNAMIC", {})
    files = set(closure.files())
    for place in closure.places:
        if dynamic.get(place) is not None:
            files.update(dynamic[place])
    return {"files": sorted(files), "packages": _packages(root, closure.third_party()),
            "places": sorted(closure.places)}


def _dynamic_entries(source):
    """Keep each surviving decision's text, including its reason comment."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "DYNAMIC"
                              for target in node.targets))
    entries = {}
    for key, value in zip(assignment.value.keys, assignment.value.values):
        # The writer puts each key at the start of its own line.
        text = "".join(lines[key.lineno - 1:value.end_lineno])
        entries[ast.literal_eval(key)] = text
    return entries


def _write(root, result):
    path = Path(root) / TARGET
    source = path.read_text(encoding="utf-8")
    before, rest = source.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    entries = _dynamic_entries(source)
    block = "\nFILES = " + pprint.pformat(tuple(result["files"]), width=100) + "\n\n"
    block += "PACKAGES = " + pprint.pformat(tuple(result["packages"]), width=100) + "\n\nDYNAMIC = {\n"
    for place in result["places"]:
        block += entries.get(place, f"    {place!r}: None,\n")
    block += "}\n"
    path.write_text(before + BEGIN + block + END + after, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = compute(root)
        if args.write:
            _write(root, result)
            # New decisions can add data files at the next write.
            print("Wrote launcher lists. Decide each new DYNAMIC entry, then run " + COMMAND + ".")
            return 0
        constants = _constants(root)
        dynamic = constants.get("DYNAMIC", {})
        if (list(constants.get("FILES", ())) != result["files"]
                or list(constants.get("PACKAGES", ())) != result["packages"]
                or set(dynamic) != set(result["places"])
                or any(value is None for value in dynamic.values())):
            print("Launcher lists or dynamic decisions are out of date. Run " + COMMAND + ".")
            return 1
        print("Launcher lists and dynamic decisions match.")
        return 0
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Cannot compute launcher lists: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())