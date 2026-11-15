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
from types import BuiltinMethodType, CodeType, FunctionType, MethodType, ModuleType


class NamedMediaGraph:
    """Host-only selection. Never construct from session/config/imported metadata."""
    def __init__(self, name, *, plans, sources, bindings=(), observations=(), dependencies=(),
                 owner_rules=(), environments=None, capability_owners=()):
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
        self._environments = copy.deepcopy(environments or {})
        self._owner_rules = tuple(owner_rules)
        self._capability_owners = tuple(capability_owners)
        self._capability_bindings = []
        self._registration_views = []
        self._native_endpoints = []
        self._owners = []
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

    def mint_owner(self, obj, *, kind, associations=(), construction=None):
        """Called only by the selected actual host constructor, not hook evidence.

        A rule is (literal factory kind, exact class, protected field names).
        Mutable implementation state stays with that reviewed source owner;
        protected source/delegate fields cannot be rebound by ordinary settings.
        """
        rule = next((rule for rule in self._owner_rules
                     if rule[0] == kind and type(obj) is rule[1]), None)
        if rule is None:
            return False
        protected = tuple((name, getattr(obj, name)) for name in rule[2])
        self._owners.append((obj, type(obj), protected, tuple(associations), construction))
        return True

    def bind_capability_owners(self, coordinator):
        """Join explicitly selected external constructor outputs once.

        Paths and classes come from the reviewed assembly, not callback evidence.
        Later replacement cannot acquire the original constructor association.
        """
        for kind, capability, keys in self._capability_owners:
            previous = next((row for row in self._capability_bindings
                             if row[0] is coordinator and row[1:3] == (capability, keys)), None)
            try:
                value = coordinator.get_capability(capability)
                for key in keys:
                    value = value[key]
            except (KeyError, TypeError, AttributeError):
                return False
            if previous is not None:
                if previous[3] is not value:
                    return False
                continue
            if not self.mint_owner(value, kind=kind, construction=coordinator):
                return False
            self._capability_bindings.append((coordinator, capability, keys, value))
        return True

    def release_construction(self, coordinator):
        self._owners = [row for row in self._owners if row[4] is not coordinator]
        self._capability_bindings = [row for row in self._capability_bindings if row[0] is not coordinator]
        self._registration_views = [row for row in self._registration_views if row[0] is not coordinator]
        self._native_endpoints = [row for row in self._native_endpoints if row[0] is not coordinator]

    def bind_native_endpoints(self, coordinator):
        # Core's actual native endpoints are captured before the public bridge
        # wraps processing. A Python replacement is never accepted here.
        for owner, name in ((coordinator.hooks, "emit"), (coordinator.hooks, "emit_and_collect"),
                            (coordinator, "process_hook_result")):
            method = getattr(owner, name, None)
            if isinstance(method, BuiltinMethodType) and method.__self__ is owner:
                self._native_endpoints.append((coordinator, owner, name, method))

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
                return ("value", type(item), item)
            if id(item) in seen:
                return ("identity", item)
            seen.add(id(item))
            if type(item) is dict:
                return ("mapping", tuple((key, value(child)) for key, child in sorted(item.items())))
            if type(item) in (list, tuple):
                return ("list" if type(item) is list else "tuple",
                        tuple(value(child) for child in item))
            return ("identity", item)
        return value(vars(obj) if hasattr(obj, "__dict__") else obj)

    def _binding_valid(self, obj):
        for native, bridge, view in self._registration_views:
            if obj is view:
                from .media import HostRegistrationView
                actual = view.participant if type(view) is HostRegistrationView else view
                return (native.get_capability("web.media.hook_bridge") is bridge
                        and bridge.participant("unified-host") is actual)
        for coordinator, capability, keys, selected in self._capability_bindings:
            if selected is obj:
                current = coordinator.get_capability(capability)
                for key in keys:
                    current = current[key]
                if current is not selected:
                    return False
        for original, cls, protected, associations, _ in self._owners:
            if original is obj:
                return (type(obj) is cls and
                        all((type(getattr(obj, name, None)) is type(value) and
                             getattr(obj, name, None) == value)
                            if value is None or type(value) in (str, int, float, bool, bytes)
                            else getattr(obj, name, None) is value for name, value in protected) and
                        all(getattr(owner, name, None) is value for owner, name, value in associations))
        for original, fields in self._bindings:
            if original is obj:
                current = self._fields(obj)
                def equal(left, right):
                    if left[0] != right[0]:
                        return False
                    if left[0] == "identity":
                        return left[1] is right[1]
                    if left[0] == "value":
                        return left[1] is right[1] and left[2] == right[2]
                    if len(left[1]) != len(right[1]):
                        return False
                    if left[0] == "mapping":
                        return all(a == b and equal(x, y) for (a, x), (b, y) in zip(left[1], right[1]))
                    return all(equal(x, y) for x, y in zip(left[1], right[1]))
                return equal(fields, current)
        if any(original is obj for original in self._observations):
            return type(obj) is list and all(type(item) in (str, int, float, bool) for item in obj)
        return False

    def bind_registration_view(self, native, bridge, view):
        from .media import HostRegistrationView
        actual = view.participant if type(view) is HostRegistrationView else view
        if bridge.participant("unified-host") is not actual:
            raise ValueError("Foreign registration view")
        self._registration_views.append((native, bridge, view))

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
            if isinstance(value, BuiltinMethodType):
                return any(value.__self__ is owner and value.__name__ == name
                           for _, owner, name, _ in self._native_endpoints)
            if not isinstance(value, (FunctionType, MethodType)) and callable(value):
                if not self._binding_valid(value):
                    return False
                value = value.__call__
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
            if any(value is dependency for dependency in self._dependencies):
                return True
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
                elif (item is not None and type(item) not in (str, int, float, bool, bytes)
                      and not self._binding_valid(item)
                      and not any(item is dependency for dependency in self._dependencies)):
                    return False
            # Core's selected Python loader returns a closure whose default is
            # the actual module mount. Check that object too, not only loader.py.
            parameters = list(value.__code__.co_varnames[:value.__code__.co_argcount])
            defaults = value.__defaults__ or ()
            captures = list(zip(parameters[len(parameters) - len(defaults):], defaults)) if defaults else []
            captures.extend((value.__kwdefaults__ or {}).items())
            for name, cell in zip(value.__code__.co_freevars, value.__closure__ or ()):
                try:
                    captures.append((name, cell.cell_contents))
                except ValueError:
                    return False
            environment = self._environments.get((filename, value.__qualname__))
            if environment is None:
                # A short qualified name is unambiguous only within the
                # reviewed source set. It must never borrow another file's
                # accepted callback environment.
                owners = {path for path, codes in self._codes.items()
                          if any(code.co_qualname == value.__qualname__ for code in codes)}
                environment = (self._environments.get(value.__qualname__, {})
                               if owners == {filename} else {})
            def exact(left, right):
                if type(left) is not type(right):
                    return False
                if type(left) is dict:
                    return left.keys() == right.keys() and all(exact(left[key], right[key]) for key in left)
                if type(left) in (tuple, list):
                    return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
                return left == right
            if isinstance(environment, list):
                # Alternatives are whole environments, never per-value pools.
                def constants(item):
                    if item is None or type(item) in (str, int, float, bool, bytes):
                        return True
                    if type(item) is dict:
                        return all(type(key) is str and constants(child) for key, child in item.items())
                    if type(item) in (list, tuple):
                        return all(constants(child) for child in item)
                    return False
                actual = {name: item for name, item in captures if constants(item)}
                environment = next((candidate for candidate in environment if exact(candidate, actual)), {})
            for capture_name, item in captures:
                if inspect.isfunction(item) or inspect.ismethod(item):
                    if not check(item):
                        return False
                elif item is None or type(item) in (str, int, float, bool, bytes):
                    if (capture_name not in environment or
                            type(item) is not type(environment[capture_name]) or
                            not exact(item, environment[capture_name])):
                        return False
                elif type(item) in (dict, tuple, list):
                    def plain(child):
                        if child is None or type(child) in (str, int, float, bool, bytes):
                            return True
                        if type(child) is dict:
                            return all(type(key) is str and plain(val) for key, val in child.items())
                        if type(child) in (tuple, list):
                            return all(plain(val) for val in child)
                        return False
                    if not (plain(item) and capture_name in environment and
                            type(item) is type(environment[capture_name]) and
                            exact(item, environment[capture_name])) and not self._binding_valid(item):
                        return False
                elif not (any(item is dependency for dependency in self._dependencies)
                          or self._binding_valid(item)):
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
            if any(not self.accepts_callable(item["original"])
                   for item in evidence.get("mode_registrations", ())):
                return False
            if any(item["state"] != "completed" or
                   not self.accepts_callable(item["original"])
                   for item in evidence.get("contributions", ())):
                return False
            return True
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return False
