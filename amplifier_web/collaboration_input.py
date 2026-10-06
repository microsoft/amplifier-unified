"""Guarded peer input through ordinary native queue and anchored steering."""
from amplifier_operations.coordination import peer_input


async def admit(controls, runtime, args, activation, authorize, stop_epoch=lambda: 0):
    # This runs under the worker's ordinary mutation/ownership lock.
    epoch = stop_epoch()
    permission = await authorize(args)
    if not permission.get("admitted"):
        return {"accepted": False, "reason": permission.get("reason")}
    task = controls.tasks.record()
    if (task or {}).get("id") != args.get("taskId") or (task or {}).get("revision") != args.get("taskRevision"):
        return {"accepted": False, "reason": "The saved task changed before native admission."}
    if not await controls.tasks.continuation_allowed():
        return {"accepted": False, "reason": "The saved task or current budget prevents automatic input."}
    goal = controls.coordinator.session_state.get("goal") or {}
    if goal.get("cap") is not None and goal.get("turns_used", 0) >= goal["cap"]:
        return {"accepted": False, "reason": "The saved task's turn budget is exhausted."}
    try:
        controls.require_idle()
    except ValueError as exc:
        return {"accepted": False, "reason": str(exc)}
    # Recheck after awaiting the boundary and use the host's saved original,
    # never a caller-provided envelope/text.
    permission = await authorize(args)
    if not permission.get("admitted"):
        return {"accepted": False, "reason": permission.get("reason")}
    if not await controls.tasks.continuation_allowed() or stop_epoch() != epoch:
        return {"accepted": False, "reason": "Stop, task or budget changed during peer admission."}
    current = controls.tasks.record() or {}
    if (current.get("id"), current.get("revision")) != (args.get("taskId"), args.get("taskRevision")):
        return {"accepted": False, "reason": "The task changed during final authorization."}
    goal = controls.coordinator.session_state.get("goal") or {}
    if goal.get("cap") is not None and goal.get("turns_used", 0) >= goal["cap"]:
        return {"accepted": False, "reason": "The saved task's turn budget is exhausted."}
    try:
        controls.require_idle()
    except ValueError as exc:
        return {"accepted": False, "reason": str(exc)}
    message = permission["message"]
    text = peer_input(message["peerEnvelope"], message["text"])
    if len(text) > runtime.max_input_chars:
        return {"accepted": False, "reason": "Peer input exceeds this adapter's input limit; use a durable reference."}
    from amplifier_module_loop_live.runtime import Input
    identity = await runtime.submit(Input("user", text, id=args["inputId"], activation=activation))
    return {"accepted": True, "inputId": identity, "mode": "queue", "completed": False}


def _steering_capability(controls):
    capability = controls.coordinator.get_capability("live.steering")
    if (isinstance(capability, dict) and type(capability.get("version")) is int
            and capability["version"] == 1 and capability.get("mode") == "request_boundary"
            and callable(capability.get("submit"))):
        return capability
    return None


async def steer(controls, runtime, args, activation, authorize, stop_epoch=lambda: 0):
    """Admit only to the named live generation, under worker ownership locks.

    Acceptance is not application or completion. The runtime's disposition and
    subsequent applied/held/unknown events own that evidence; never resend.
    """
    def rejected(reason, *, supported=True):
        return {"accepted": False, "supported": supported, "effect": "none", "reason": reason}

    epoch = stop_epoch()
    if _steering_capability(controls) is None:
        return rejected("This runtime does not support anchored request-boundary steering.", supported=False)
    target = args.get("targetGenerationId")
    if not isinstance(target, str) or not 1 <= len(target) <= 128:
        return rejected("An exact current generation identity is required for steering.")
    for _ in range(2):
        permission = await authorize(args)
        if not permission.get("admitted"):
            return rejected(permission.get("reason"))
        if not await controls.tasks.continuation_allowed():
            return rejected("The saved task or current budget prevents automatic input.")
        # Every read below occurs after the awaited host and task guard. Nothing
        # awaits between this last boundary and capability submission.
        current = controls.tasks.record() or {}
        if (current.get("id"), current.get("revision")) != (args.get("taskId"), args.get("taskRevision")):
            return rejected("The saved task changed before native steering admission.")
        goal = controls.coordinator.session_state.get("goal") or {}
        if goal.get("cap") is not None and goal.get("turns_used", 0) >= goal["cap"]:
            return rejected("The saved task's turn budget is exhausted.")
        if stop_epoch() != epoch:
            return rejected("Stop changed during peer steering admission.")
        if getattr(runtime, "closed", False):
            return rejected("The recipient runtime is closed.")
        generation = runtime.generation or {}
        if generation.get("id") != target:
            return rejected("The anchored generation is no longer active.")
        capability = _steering_capability(controls)
        if capability is None:
            return rejected("This runtime does not support anchored request-boundary steering.", supported=False)
    # Only the host's current saved message/envelope reaches the model.
    message = permission["message"]
    text = peer_input(message["peerEnvelope"], message["text"])
    if len(text) > runtime.max_input_chars:
        return rejected("Peer input exceeds this adapter's input limit; use a durable reference.")
    from amplifier_module_loop_live.runtime import Input
    outcome = await capability["submit"](Input("steer", text, id=args["inputId"], activation=activation), target)
    if not isinstance(outcome, dict):
        # Admission may already have happened. An invalid response is unknown,
        # not a definite no-effect refusal and never an invitation to replay.
        raise RuntimeError("The steering adapter returned an unknown admission outcome; do not replay.")
    return {**outcome, "supported": True, "mode": "steer", "completed": False}