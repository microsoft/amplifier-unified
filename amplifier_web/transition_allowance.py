"""One additional context-preparation allowance, bound to a single ordinary Send."""

MAX_ADDITIONAL_CALLS = 32


def offer_id(session_id, record):
    from .context_continuity import digest
    if (not record or record.get("kind") != "model-transition" or record.get("state") != "refused"
            or record.get("code") not in {"summary_work_paused", "transition_budget_exhausted"}
            or record.get("additionalAllowance") or record.get("candidateSha256") or record.get("installedCandidate")
            or not record.get("progress") or not record.get("calls")
            or len(record["calls"]) != record.get("maxCalls") or record.get("completedParts") != len(record["calls"])
            or any(call.get("state") != "completed" or call.get("report", {}).get("method") != "native"
                   for call in record["calls"])):
        return None
    progress = record["progress"]
    summary = progress.get("summary") if isinstance(progress, dict) else None
    if (not isinstance(summary, dict) or summary.get("kind") != "native" or not isinstance(summary.get("message"), dict)
            or progress.get("progress") is not None or progress.get("identity") != record["identity"]
            or progress.get("sha256") != digest({k: v for k, v in progress.items() if k != "sha256"})):
        return None
    # Source and target, confirmed reservations and checkpoint, current selection,
    # account, options, tools and prior Stop are already bound by this journal.
    return digest({"sessionId": session_id, "journal": {key: record.get(key) for key in (
        "commandId", "state", "code", "identity", "sourceRevision", "sourceCheckpointDigest", "foreground",
        "contract", "interruptionRevision", "maxCalls", "calls", "completedParts", "progress")}})


def valid_request(request):
    from .skill_cache_move import valid_choice
    return (isinstance(request, dict) and set(request) in (
                {"offerId", "additionalCalls"}, {"offerId", "additionalCalls", "cacheMove"})
            and ("cacheMove" not in request or valid_choice(request["cacheMove"]))
            and isinstance(request["offerId"], str) and len(request["offerId"]) == 64
            and all(char in "0123456789abcdef" for char in request["offerId"])
            and type(request["additionalCalls"]) is int and 1 <= request["additionalCalls"] <= MAX_ADDITIONAL_CALLS)


def valid_grant(record):
    grant = record.get("additionalAllowance")
    from .context_continuity import digest
    from .skill_cache_move import valid_choice
    keys = {"offerId", "additionalCalls", "authorizationId", "interruptionRevision", "originalMaxCalls"}
    if not isinstance(grant, dict):
        return False
    if "cacheMove" in grant:
        proof = grant["cacheMove"]
        if (not isinstance(proof, dict) or not valid_choice({k: proof.get(k) for k in (
                "cacheMoveId", "fromGeneration", "toGeneration")}) or proof.get("cacheMoveId") != digest({
                key: value for key, value in proof.items() if key != "cacheMoveId"})
                or record["contract"]["sha256"] != proof.get("newContractSha256")
                or not any(p.get("contract", {}).get("sha256") == proof.get("oldContractSha256")
                           for p in record.get("preparations", []))):
            return False
        keys.add("cacheMove")
    return (set(grant) == keys
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
    request = {key: grant[key] for key in ("offerId", "additionalCalls")} if grant else {}
    if grant and "cacheMove" in grant:
        request["cacheMove"] = {key: grant["cacheMove"][key] for key in ("cacheMoveId", "fromGeneration", "toGeneration")}
    if (grant and grant["authorizationId"] == foreground["inputId"]
            and grant["interruptionRevision"] == foreground.get("allowanceStopRevision")
            and foreground.get("recoveryAllowance") == request):
        return {"max_calls": max(0, maximum + grant["additionalCalls"] - used),
                "additional_call_allowance": {"version": 1, "additionalCalls": grant["additionalCalls"],
                    "originalMaxCalls": maximum, "confirmedCalls": used, "authorizationId": grant["authorizationId"]}}
    return {"max_calls": max(0, maximum - used)}
