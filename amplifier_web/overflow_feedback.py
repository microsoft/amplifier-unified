"""Bounded identity translation, never classification, fitting or inference."""
import copy
from dataclasses import dataclass


@dataclass
class _Feedback:
    original: object
    original_snapshot: object
    prepared: object
    prepared_snapshot: object
    error: Exception
    options: dict
    dispatch_options: dict
    owner: object
    method: object
    binding: object


class OverflowFeedback:
    def __init__(self):
        self.entries = []

    def forget(self, request):
        self.entries = [row for row in self.entries if row.original is not request]

    def remember(self, request, prepared, error, options, dispatch_options, owner, *, binding=None):
        from amplifier_core.llm_errors import ContextLengthError
        self.forget(request)
        method = getattr(owner, 'recover_context_overflow', None)
        if not isinstance(error, ContextLengthError) or not callable(method):
            return
        try:
            row = _Feedback(request, copy.deepcopy(request), prepared, copy.deepcopy(prepared),
                            error, copy.deepcopy(options), copy.deepcopy(dispatch_options),
                            owner, method, copy.deepcopy(binding))
        except Exception:
            return  # Opaque inputs cannot be proven unchanged; preserve the error.
        self.entries = ([item for item in self.entries if item.error is not error] + [row])[-4:]

    def recover(self, request, error, *, context_estimate, request_options, owner, binding=None):
        row = next((row for row in self.entries
                    if row.original is request and row.error is error), None)
        if row is None:
            return None
        self.entries = [item for item in self.entries if item is not row]
        # Even a refused matching attempt is not replayable.
        if (row.owner is not owner or row.binding != binding or row.options != (request_options or {})
                or request != row.original_snapshot or row.prepared != row.prepared_snapshot):
            return None
        # This synchronous optional protocol belongs to the native loop/provider.
        # The provider still validates and consumes its own private server proof.
        return row.method(row.prepared, error, context_estimate=context_estimate,
                          request_options=row.dispatch_options)