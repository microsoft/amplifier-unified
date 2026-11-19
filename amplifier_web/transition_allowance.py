"""One additional context-preparation allowance, bound to a single ordinary Send."""

MAX_ADDITIONAL_CALLS = 32


def offer_id(session_id, record):
    from .context_continuity import digest
    if (not record or record.get("kind") != "model-transition" or record.get("state") != "refused"
            or record.get("code") not in {"summary_work_paused", "transition_budget_exhausted"}
            or record.get("additionalAllowance") or record.get("candidateSha256") or record.get("installedCandidate")
            or not record.get("progress") or not record.get("calls")
            or len(record["calls"]) != record.get("maxCalls") or record.get("completedParts") != len(record["calls"])
            or any(call.get("state") != "completed" for call in record["calls"])):
        return None
    # Source and target, confirmed reservations and checkpoint, current selection,
    # account, options, tools and prior Stop are already bound by this journal.
    return digest({"sessionId": session_id, "journal": {key: record.get(key) for key in (
        "commandId", "state", "code", "identity", "sourceRevision", "sourceCheckpointDigest", "foreground",
        "contract", "interruptionRevision", "maxCalls", "calls", "completedParts", "progress")}})


def valid_request(request):
    return (isinstance(request, dict) and set(request) == {"offerId", "additionalCalls"}
            and isinstance(request["offerId"], str) and len(request["offerId"]) == 64
            and all(char in "0123456789abcdef" for char in request["offerId"])
            and type(request["additionalCalls"]) is int and 1 <= request["additionalCalls"] <= MAX_ADDITIONAL_CALLS)


def valid_grant(record):
    grant = record.get("additionalAllowance")
    return (isinstance(grant, dict) and set(grant) == {
        "offerId", "additionalCalls", "authorizationId", "interruptionRevision", "originalMaxCalls"}
        and valid_request({key: grant[key] for key in ("offerId", "additionalCalls")})
        and isinstance(grant["authorizationId"], str) and 0 < len(grant["authorizationId"]) <= 200
        and type(grant["interruptionRevision"]) is int and grant["interruptionRevision"] >= 0
        and type(grant["originalMaxCalls"]) is int and grant["originalMaxCalls"] == record["maxCalls"])


def invocation(record):
    """Previously approved room cannot spill over into a subsequent ordinary turn."""
    used = len(record["calls"])
    maximum = record["maxCalls"]
    foreground = record["foreground"]
    grant = record.get("additionalAllowance")
    if (grant and grant["authorizationId"] == foreground["inputId"]
            and grant["interruptionRevision"] == foreground.get("allowanceStopRevision")
            and foreground.get("recoveryAllowance") == {key: grant[key] for key in ("offerId", "additionalCalls")}):
        return {"max_calls": max(0, maximum + grant["additionalCalls"] - used),
                "additional_call_allowance": {"version": 1, "additionalCalls": grant["additionalCalls"],
                    "originalMaxCalls": maximum, "confirmedCalls": used, "authorizationId": grant["authorizationId"]}}
    return {"max_calls": max(0, maximum - used)}
