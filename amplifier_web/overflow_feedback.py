"""Bounded identity translation, never classification, fitting or inference."""
import copy
import hashlib
import json
from dataclasses import dataclass
from amplifier_core.llm_errors import ContextLengthError


@dataclass
class _Feedback:
    original: object
    original_snapshot: object
    prepared: object
    prepared_snapshot: object
    error: Exception | None
    options: str
    dispatch_options: dict
    owner: object
    method: object
    binding: str


class OverflowFeedback:
    def __init__(self):
        self.entries = []

    def forget(self, request):
        self.entries = [row for row in self.entries if row.original is not request]

    @staticmethod
    def fingerprint(request):
        return hashlib.sha256(request.model_dump_json().encode('utf-8')).digest()

    @staticmethod
    def freeze(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)

    def capture(self, request, prepared, options, dispatch_options, owner, *, binding=None):
        """Witness dispatch inputs before any provider await; retain no body copy."""
        try:
            method = getattr(owner, 'recover_context_overflow', None)
            if not callable(method):
                return None
            return _Feedback(request, self.fingerprint(request), prepared, self.fingerprint(prepared),
                             None, self.freeze(options), copy.deepcopy(dispatch_options),
                             owner, method, self.freeze(binding))
        except Exception:
            return None  # Opaque inputs cannot be proven unchanged.

    def remember(self, row, error):
        if row is None or not isinstance(error, ContextLengthError):
            return
        row.error = error
        self.forget(row.original)
        self.entries = ([item for item in self.entries if item.error is not error] + [row])[-4:]

    def recover(self, request, error, *, context_estimate, request_options, owner, binding=None):
        row = next((row for row in self.entries
                    if row.original is request and row.error is error), None)
        if row is None:
            return None
        self.entries = [item for item in self.entries if item is not row]
        # Even a refused matching attempt is not replayable.
        try:
            unchanged = (row.owner is owner and row.binding == self.freeze(binding)
                and row.options == self.freeze(request_options or {})
                and self.fingerprint(request) == row.original_snapshot
                and self.fingerprint(row.prepared) == row.prepared_snapshot)
        except Exception:
            return None
        if not unchanged:
            return None
        # This synchronous optional protocol belongs to the native loop/provider.
        # The provider still validates and consumes its own private server proof.
        return row.method(row.prepared, error, context_estimate=context_estimate,
                          request_options=row.dispatch_options)