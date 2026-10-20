"""Approval protocols adapted to the app's asynchronous human decision surface."""
from __future__ import annotations

import asyncio
import contextvars
import inspect

REQUEST_CONTEXT = contextvars.ContextVar('approval_feedback_context', default=None)


class ApprovalDecision(str):
    """Keep string-returning approval consumers compatible while carrying a note."""
    def __new__(cls, decision, comment=None):
        value = super().__new__(cls, decision)
        value.comment = comment
        return value


class Approvals:
    def __init__(self, runtime, ask):
        self.runtime = runtime
        self.ask = ask

    @property
    def interactive(self):
        """Transport availability only; never a permission or remembered grant."""
        return callable(self.ask)

    def for_context(self, get_context):
        if not self.interactive:
            return Approvals(self.runtime, None)
        async def ask(prompt, options):
            token = REQUEST_CONTEXT.set(get_context())
            try:
                return await self.ask(prompt, options)
            finally:
                REQUEST_CONTEXT.reset(token)
        return Approvals(self.runtime, ask)

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
        # Worker/service wire choices are canonical. Preserve the caller's
        # exact labels at this adapter boundary, including denial on timeout.
        aliases = {"allow": "allow", "approve": "allow", "deny": "deny", "reject": "deny"}
        choices = {}
        for option in options:
            canonical = aliases.get(option.casefold()) if isinstance(option, str) else None
            if canonical is None or canonical in choices:
                raise ValueError("Approval choices must be one unambiguous allow and deny pair")
            choices[canonical] = option
        if set(choices) != {"allow", "deny"}:
            raise ValueError("Approval choices must be one unambiguous allow and deny pair")
        if not callable(self.ask):
            raise RuntimeError("This operation requires approval, but no user approval surface is connected")
        # A default of allow is not human authorization. Timeouts never approve.
        result = self.ask(prompt, list(choices))
        if not inspect.isawaitable(result):
            raise TypeError("The app approval callback must be asynchronous")
        try:
            decision = await asyncio.wait_for(result, timeout) if timeout is not None else await result
        except TimeoutError:
            if provider_request:
                from amplifier_core import ApprovalResponse
                return ApprovalResponse(approved=False, reason="User approval timed out", remember=False)
            return choices["deny"]
        if decision not in choices:
            raise ValueError("The approval response is not one of the offered choices")
        if provider_request:
            from amplifier_core import ApprovalResponse
            return ApprovalResponse(approved=decision in {"allow", "approve"}, reason=getattr(decision, "comment", None), remember=False)
        return ApprovalDecision(choices[decision], getattr(decision, "comment", None))
