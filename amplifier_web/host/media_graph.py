"""Named cooperative media compositions, joined to actual construction callbacks.

This is not isolation from malicious Python. The assembly owner selects reviewed
source bytes and exact plans; public Foundation receipts prove the corresponding
actual construction and single execution, never a module-name allowlist.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import CodeType, FunctionType, MethodType, ModuleType


class NamedMediaGraph:
    """Host-only selection. Never construct from session/config/imported metadata."""
    def __init__(self, name, *, plans, sources, bindings=(), observations=(), dependencies=()):
        if not isinstance(name, str) or not name or not plans or not sources:
            raise ValueError("A named graph needs reviewed plans and sources")
        if len(sources) > 128:
            raise ValueError("Named media graph source budget exceeded")
        self._name = name
        self._plans = tuple(json.dumps(plan, sort_keys=True, separators=(",", ":")) for plan in plans)
        self.sources = {str(Path(path).resolve()): digest for path, digest in sources.items()}
        # Actual host/factory receivers explicitly selected by the assembly.
        # Capture their field identities/values, not a generic class-name grant.
        self._bindings = tuple((obj, self._fields(obj)) for obj in bindings)
        self._observations = tuple(observations)
        self._dependencies = tuple(dependencies)
        self._codes = {}
        self._stamps = {}
        total = 0
        for path, expected in self.sources.items():
            source = Path(path)
            data = source.read_bytes()
            total += len(data)
            if len(data) > 2 * 1024 * 1024 or total > 16 * 1024 * 1024:
                raise ValueError("Named media graph source budget exceeded")
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Named media graph source changed")
            code = compile(data, path, "exec", dont_inherit=True, optimize=sys.flags.optimize)
            codes = []
            def collect(value):
                codes.append(value)
                for child in value.co_consts:
                    if isinstance(child, CodeType):
                        collect(child)
            collect(code)
            self._codes[path] = tuple(codes)
            stat = source.stat()
            self._stamps[path] = (stat.st_dev, stat.st_ino, stat.st_size,
                                  stat.st_mtime_ns, stat.st_ctime_ns)

    def accepts_plan(self, config):
        return json.dumps(config, sort_keys=True, separators=(",", ":")) in self._plans

    @property
    def name(self):
        return self._name

    @property
    def plans(self):
        return tuple(json.loads(plan) for plan in self._plans)

    @staticmethod
    def _fields(obj):
        seen = set()
        def value(item):
            if item is None or type(item) in (str, int, float, bool, bytes):
                return ("value", item)
            if id(item) in seen:
                return ("identity", item)
            seen.add(id(item))
            if type(item) is dict:
                return ("mapping", tuple((key, value(child)) for key, child in sorted(item.items())))
            if type(item) in (list, tuple):
                return ("sequence", tuple(value(child) for child in item))
            return ("identity", item)
        return value(vars(obj) if hasattr(obj, "__dict__") else obj)

    def _binding_valid(self, obj):
        for original, fields in self._bindings:
            if original is obj:
                current = self._fields(obj)
                def equal(left, right):
                    if left[0] != right[0]:
                        return False
                    if left[0] == "identity":
                        return left[1] is right[1]
                    if left[0] == "value":
                        return left[1] == right[1]
                    if len(left[1]) != len(right[1]):
                        return False
                    if left[0] == "mapping":
                        return all(a == b and equal(x, y) for (a, x), (b, y) in zip(left[1], right[1]))
                    return all(equal(x, y) for x, y in zip(left[1], right[1]))
                return equal(fields, current)
        if any(original is obj for original in self._observations):
            return type(obj) is list and all(type(item) in (str, int, float, bool) for item in obj)
        return False

    def _unchanged(self):
        for path, stamp in self._stamps.items():
            stat = Path(path).stat()
            if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != stamp:
                return False
        return True

    def accepts_callable(self, callback):
        """Compare live code objects, including captured loader/mount callbacks."""
        seen = set()
        def check(value):
            if isinstance(value, MethodType):
                if not self._binding_valid(value.__self__):
                    return False
                value = value.__func__
            if not isinstance(value, FunctionType):
                return False
            if id(value) in seen:
                return True
            seen.add(id(value))
            filename = str(Path(value.__code__.co_filename).resolve())
            if value.__code__ not in self._codes.get(filename, ()):
                return False
            for name in value.__code__.co_names:
                if name not in value.__globals__:
                    continue
                item = value.__globals__[name]
                if isinstance(item, (FunctionType, MethodType)):
                    if not check(item):
                        return False
                elif isinstance(item, type) or isinstance(item, ModuleType):
                    if not any(item is dependency for dependency in self._dependencies):
                        return False
                elif callable(item) and not any(item is dependency for dependency in self._dependencies):
                    return False
            # Core's selected Python loader returns a closure whose default is
            # the actual module mount. Check that object too, not only loader.py.
            captures = list(value.__defaults__ or ()) + list((value.__kwdefaults__ or {}).values())
            for cell in value.__closure__ or ():
                try:
                    captures.append(cell.cell_contents)
                except ValueError:
                    return False
            for item in captures:
                if inspect.isfunction(item) or inspect.ismethod(item):
                    if not check(item):
                        return False
                elif item is None or type(item) in (str, int, float, bool, bytes):
                    continue
                elif type(item) is tuple and all(x is None or type(x) in (str, int, float, bool, bytes) for x in item):
                    continue
                elif not self._binding_valid(item):
                    return False
            return True
        return check(callback)

    def accepts_construction(self, graph):
        """Public evidence contains strong objects, not names or passive hashes."""
        try:
            if not self._unchanged():
                return False
            evidence = graph.participants_evidence()
            if not evidence["loads"]:
                return False
            if any(not item["mounted"] or not self.accepts_callable(item["mount"])
                   for item in evidence["loads"]):
                return False
            if any(not item["completed"] or not self.accepts_callable(item["callback"])
                   for item in evidence["ready"]):
                return False
            if any(not self.accepts_callable(item["original"])
                   for item in evidence["registrations"]):
                return False
            return True
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return False
