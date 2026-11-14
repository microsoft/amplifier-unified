"""Named cooperative media compositions, joined to actual construction callbacks.

This is not isolation from malicious Python. The assembly owner selects reviewed
source bytes and exact plans; public Foundation receipts prove the corresponding
actual construction and single execution, never a module-name allowlist.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
from pathlib import Path
import sys
from types import CodeType, FunctionType, MethodType


class NamedMediaGraph:
    """Host-only selection. Never construct from session/config/imported metadata."""
    def __init__(self, name, *, plans, sources):
        if not isinstance(name, str) or not name or not plans or not sources:
            raise ValueError("A named graph needs reviewed plans and sources")
        if len(sources) > 128:
            raise ValueError("Named media graph source budget exceeded")
        self.name = name
        self.plans = copy.deepcopy(tuple(plans))
        self.sources = {str(Path(path).resolve()): digest for path, digest in sources.items()}
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
        return any(config == plan for plan in self.plans)

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
                value = value.__func__
            if not isinstance(value, FunctionType):
                return False
            if id(value) in seen:
                return True
            seen.add(id(value))
            filename = str(Path(value.__code__.co_filename).resolve())
            if value.__code__ not in self._codes.get(filename, ()):
                return False
            # Core's selected Python loader returns a closure whose default is
            # the actual module mount. Check that object too, not only loader.py.
            captures = list(value.__defaults__ or ()) + list((value.__kwdefaults__ or {}).values())
            for cell in value.__closure__ or ():
                try:
                    captures.append(cell.cell_contents)
                except ValueError:
                    return False
            return all(check(item) for item in captures if inspect.isfunction(item) or inspect.ismethod(item))
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
