"""Value-free observation of the canonical configuration predicates, never a gate."""
from contextlib import contextmanager
from contextvars import ContextVar


_CURRENT = ContextVar("configuration_predicate_observation", default=None)
_CODES = frozenset({
    "not_observed", "record_unavailable", "native_evidence_unavailable",
    "recovery_source", "operation_revision", "operation_phase", "configuration_task",
    "operation_fence", "operator_authority", "target_idle", "target_ownership",
    "native_target", "continuation_intent", "placement_binding", "original_target",
    "original_authority", "retained_authority", "original_payload",
    "recovery_authority", "native_evidence", "native_terminal", "native_effect",
    "native_binding", "resume_effect", "saved_terminal", "saved_target",
    "saved_plan", "saved_comparison", "update_hold", "update_activity",
    "update_binding", "update_attempts", "update_snapshot",
})


class PredicateFailure(ValueError):
    """Same refusal as before, with an internal fixed discriminator."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code if code in _CODES else "not_observed"


def checkpoint(code):
    row = _CURRENT.get()
    if row is not None:
        row["_stage"] = code if code in _CODES else "not_observed"
        if code == "saved_comparison":
            row["savedComparison"] = "unknown"


def saved_comparison(matches):
    row = _CURRENT.get()
    if row is not None:
        row["savedComparison"] = "match" if matches else "mismatch"


@contextmanager
def observation():
    """Instrument one existing call without suppressing or interpreting its errors."""
    row = {"availability": "not_observed", "firstFailure": None,
           "savedComparison": "not_observed"}
    token = _CURRENT.set(row)
    try:
        yield row
    except Exception as error:
        row["availability"] = "blocked" if isinstance(error, PredicateFailure) else "unavailable"
        code = error.code if isinstance(error, PredicateFailure) else row.get("_stage", "not_observed")
        row["firstFailure"] = code if isinstance(code, str) and code in _CODES else "not_observed"
        raise
    else:
        row["availability"] = "observed"
    finally:
        row.pop("_stage", None)
        _CURRENT.reset(token)