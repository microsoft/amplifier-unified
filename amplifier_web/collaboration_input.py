"""Guarded idle peer admission through the existing native input path."""
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