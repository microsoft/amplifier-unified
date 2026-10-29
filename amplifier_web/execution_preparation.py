"""Disposable, content-checked canonical encoding of execution fragments.

Private pickle bytes are only compared, NEVER unpickled, stored or accepted
from another source. Custom reducers are rejected before invocation. JSON and
its content hash remain the existing storage format and authority.
"""
from collections import OrderedDict
import hashlib
import json
import io
import pickle
import sys

from .resource_files import Prepared, prepare


class Unsupported(Exception):
    pass


class _BoundedBytes(io.BytesIO):
    LIMIT = 8 * 1024 * 1024
    pickler = None

    def write(self, value):
        if (self.tell() + len(value) > self.LIMIT
                or self.pickler is not None and self.pickler.__sizeof__() > self.LIMIT * 4):
            raise Unsupported
        return super().write(value)


class _BuiltinsOnly(pickle.Pickler):
    # The C pickler handles exact builtins directly; every custom type reaches
    # this hook before __reduce__, __getstate__ or a buffer method can run.
    def reducer_override(self, value):
        raise Unsupported


def _witness(value):
    if sys.implementation.name != 'cpython':
        raise Unsupported
    output = _BoundedBytes()
    output.pickler = _BuiltinsOnly(output, protocol=4)
    try:
        output.pickler.dump(value)
        return output.getvalue()
    finally:
        output.pickler = None  # Do not leave the writer/pickler cycle for GC.


class ExecutionPreparation:
    CHUNK = 128
    # Separate encoding and accounting budgets: at most 128 MiB retained here.
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self.accounting = OrderedDict()
        self.accounting_bytes = 0

    def retired(self, owner, rows):
        """Private persistence-only projection; callers must not mutate it.

        Content is witnessed on every use, including edits through borrowed
        aliases. These detached derived rows carry no storage/commit authority.
        They exist only while this service prepares its synchronous JSON save.
        """
        from .session_projection import accounting_projection
        def project():
            return accounting_projection({'retiredUsageNodes': rows})
        if (type(owner) is not str or len(owner) > 200
                or type(rows) is not list or len(rows) < self.CHUNK):
            return None  # Keep the original projection and key/hook behavior.
        try:
            witness = _witness(rows)
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            return None
        # Witness first: custom keys/values must not gain extra lookup hooks.
        if any(type(row) is not dict or type(row.get('id')) is not str
               or row.get('sessionId') is not None and type(row['sessionId']) is not str
               or type(row.get('revision', 0)) is not int for row in rows):
            return None
        old = self.accounting.get(owner)
        if old is not None and old[0] == witness:
            self.accounting.move_to_end(owner)
            return old[1]
        result = project()
        # Charge the detached graph once, including shared scalar objects, on a
        # miss. Oversized graphs use the existing uncached projection path.
        pending, seen = [result], set()
        charge = sys.getsizeof(witness) + 256
        while pending and charge <= self.MAX_BYTES:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            charge += sys.getsizeof(value)
            if charge > self.MAX_BYTES:
                break
            if type(value) is dict:
                pending.extend(value.keys())
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
            # deepcopy may preserve tuple identity; never retain a borrowed
            # container that would prevent cold-display retirement.
            elif type(value) not in (str, int, float, bool, type(None)):
                charge = self.MAX_BYTES + 1
                break
        if old is not None:
            self.accounting_bytes -= self.accounting.pop(owner)[2]
        if charge <= self.MAX_BYTES and not pending:
            while self.accounting and self.accounting_bytes + charge > self.MAX_BYTES:
                self.accounting_bytes -= self.accounting.popitem(last=False)[1][2]
            self.accounting[owner] = (witness, result, charge)
            self.accounting_bytes += charge
        return result

    def _part(self, key, value):
        try:
            witness = _witness(value)
        except (Unsupported, pickle.PicklingError, RecursionError, TypeError, ValueError):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        old = self.entries.get(key)
        if old is not None and old[0] == witness:
            self.entries.move_to_end(key)
            return old[1]
        # Synchronous builtin traversal: no await or custom hook can mutate
        # the graph between witnessing it and applying the original encoder.
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        charge = sys.getsizeof(witness) + sys.getsizeof(text) + sys.getsizeof(key) + 256
        if old is not None:
            self.bytes -= self.entries.pop(key)[2]
        if charge <= self.MAX_BYTES:
            while self.entries and self.bytes + charge > self.MAX_BYTES:
                self.bytes -= self.entries.popitem(last=False)[1][2]
            self.entries[key] = (witness, text, charge)
            self.bytes += charge
        return text

    def prepare(self, owner, tree):
        if (type(owner) is not str or len(owner) > 200 or type(tree) is not dict
                or any(type(key) is not str or len(key) > 200 for key in tree)):
            return prepare(tree)
        fields = []
        for name in sorted(tree):
            value = tree[name]
            if type(value) is list and len(value) >= self.CHUNK:
                parts = [self._part((owner, name, start), value[start:start + self.CHUNK])[1:-1]
                         for start in range(0, len(value), self.CHUNK)]
                text = '[' + ','.join(parts) + ']'
            else:
                text = self._part((owner, name, None), value)
            fields.append(json.dumps(name, ensure_ascii=False) + ':' + text)
        text = '{' + ','.join(fields) + '}'
        encoded = text.encode()
        return Prepared(text, hashlib.sha256(encoded).hexdigest(), len(encoded))

    def retain(self, owners):
        for key in tuple(self.entries):
            if key[0] not in owners:
                self.bytes -= self.entries.pop(key)[2]
        for owner in tuple(self.accounting):
            if owner not in owners:
                self.accounting_bytes -= self.accounting.pop(owner)[2]
