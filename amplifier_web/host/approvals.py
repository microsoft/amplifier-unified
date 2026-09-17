"""Approval protocols adapted to the app's asynchronous human decision surface."""
from __future__ import annotations

import asyncio
import inspect


class Approvals:
    def __init__(self, runtime, ask):
        self.runtime = runtime
        self.ask = ask

    async def request_approval(self, prompt, options=None, timeout=None, default="deny"):
        provider_request = not isinstance(prompt, str)
        request = prompt if provider_request else None
        if provider_request:
            prompt = f"{request.tool_name}: {request.action}"
            if request.details:
                import json
                prompt += "\n" + json.dumps(request.details, ensure_ascii=False, default=str)
            timeout = request.timeout
            options = ["allow", "deny"]
        options = list(options or ["allow", "deny"])
        if not callable(self.ask):
            raise RuntimeError("This operation requires approval, but no user approval surface is connected")
        # A default of allow is not human authorization. Timeouts never approve.
        result = self.ask(prompt, options)
        if not inspect.isawaitable(result):
            raise TypeError("The app approval callback must be asynchronous")
        try:
            decision = await asyncio.wait_for(result, timeout) if timeout is not None else await result
        except TimeoutError:
            if provider_request:
                from amplifier_core import ApprovalResponse
                return ApprovalResponse(approved=False, reason="User approval timed out", remember=False)
            return "deny"
        if decision not in options:
            raise ValueError("The approval response is not one of the offered choices")
        if provider_request:
            from amplifier_core import ApprovalResponse
            return ApprovalResponse(approved=decision in {"allow", "approve"}, remember=False)
        return decision
