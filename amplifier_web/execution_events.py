"""Public execution metadata, correlated without retaining model payloads."""
from __future__ import annotations

import asyncio
import contextvars
from functools import wraps
import math
import inspect
import json
import time
import uuid
import weakref
from .token_usage import with_gross_tokens

CALL_PURPOSE = contextvars.ContextVar('amplifier_web_call_purpose',default=None)
CURRENT_CALL = contextvars.ContextVar("amplifier_web_public_call", default=None)
CURRENT_PROVIDER = contextvars.ContextVar("amplifier_web_public_provider", default=None)


def public_usage(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {"costType": "unavailable"}
    result = {}
    for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                           ("cache_read_tokens", "cacheReadTokens"), ("cache_write_tokens", "cacheWriteTokens"),
                           ("total_tokens", "totalTokens"), ("reasoning_tokens", "reasoningTokens")):
        number = value.get(source)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0:
            result[target] = int(number)
    if "totalTokens" not in result and "inputTokens" in result and "outputTokens" in result:
        result["totalTokens"] = result["inputTokens"] + result["outputTokens"]
    try:
        if isinstance(value["cost_usd"], bool):
            raise ValueError()
        cost = float(value["cost_usd"])
        if not math.isfinite(cost) or cost < 0:
            raise ValueError()
        result.update(costUsd=cost, costType="estimated" if value.get("cost_type") == "estimated" else "reported")
        if value.get("cost_source"):
            result["costSource"] = str(value["cost_source"])[:300]
    except (KeyError, TypeError, ValueError):
        result["costType"] = "unavailable"
    return with_gross_tokens(result)


class ExecutionEvents:
    def __init__(self, root_id, emit):
        self.root_id, self.emit = root_id, emit
        self.producer_id = str(uuid.uuid4())
        self.turn_id = None
        self.calls = {}
        self.children = {}
        self.requests = weakref.WeakKeyDictionary()
        self.nodes = {}
        self.admission_guard = None
        self.provider_wrappers = {}
        self.streaming_calls = set()

    def publish(self, row):
        row = {**row, "revision": self.nodes.get(row["id"], {}).get("revision", 0) + 1}
        row = {**row, "liveObservation": True}
        self.nodes[row["id"]] = row
        self.emit({"type": "execution.event", "event": dict(row)})

    def lifecycle(self, event):
        kind = event.get("type")
        if kind == "input.delivered" and event.get("source", "user") == "user":
            self.turn_id = event.get("input_id") or self.turn_id
        elif kind == "child.updated":
            identity = event.get("sessionId")
            if not identity:
                return
            call_id = event.get("callId")
            parent_sid = event.get("parentSessionId") or self.root_id
            parent = self.calls.get((parent_sid, call_id), {})
            previous = self.children.get(identity, {})
            row = {"id": "worker:" + identity, "parentId": parent.get("id") or previous.get("parentId"),
                   "turnId": previous.get("turnId") or parent.get("turnId") or self.turn_id,
                   "sessionId": identity, "rootSessionId": self.root_id, "kind": "worker",
                   "phase": event.get("status", "running"), "label": event.get("agent") or "Worker",
                   "toolCallId": call_id, "startedAt": previous.get("startedAt", time.time())}
            if row["phase"] in {"completed", "cancelled", "error", "interrupted"}:
                row["endedAt"] = previous.get("endedAt") or time.time()
            else:
                row["endedAt"] = None
            if isinstance(event.get("report"),str) and event["report"]:
                row["summary"]=event["report"][:12000]
            elif previous.get("summary"):row["summary"]=previous["summary"]
            self.children[identity] = row
            self.publish(row)

    def parent(self, sid):
        child = self.children.get(sid)
        return (child["id"], child.get("turnId")) if child else (None, self.turn_id)

    async def _begin_provider_call(self, sid, provider, request, *, label=None):
        parent,turn = self.parent(sid)
        purpose=CALL_PURPOSE.get()
        if purpose:parent,turn=None,purpose.get('turnId',turn)
        info = provider.get_info()
        if inspect.isawaitable(info):
            info = await info
        defaults = (info.get("defaults", {}) if isinstance(info, dict) else getattr(info,"defaults",{})) or {}
        provider_id = info.get("id") if isinstance(info, dict) else getattr(info, "id", type(provider).__name__)
        row = {"id":"llm:"+str(uuid.uuid4()),"parentId":parent,"turnId":turn,"sessionId":sid,
               "rootSessionId":self.root_id,"producerId":self.producer_id,"kind":"llm","phase":"running","label":label or (purpose["label"] if purpose else "Model call"),
               "provider":str(provider_id)[:160],
               "model":str(getattr(request,"model",None) or defaults.get("model") or defaults.get("default_model") or "")[:160],
               "startedAt":time.time(),"lifecycle":"background" if purpose and purpose.get("lifecycle")=="background" else "turn"}
        if self.admission_guard:
            await self.admission_guard(row)
        return row

    async def provider_call(self, sid, provider, request, invoke, *, label=None):
        """Observe one host-owned provider action through the same admission gate.

        Optional provider actions such as explicit compaction do not implement
        complete(), but still consume model capacity. Retain only their usage
        and call identity, exactly like ordinary completion calls.
        """
        row = await self._begin_provider_call(sid, provider, request, label=label)
        token = CURRENT_CALL.set(row["id"])
        owner = CURRENT_PROVIDER.set(id(provider))
        self.publish(row)
        try:
            response = await invoke()
            usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
            self.publish({**row, "phase": "completed", "endedAt": time.time(), "usage": public_usage(usage)})
            return response
        except BaseException as exc:
            from .session_health import exception_details
            failure = {} if isinstance(exc, asyncio.CancelledError) else {'failure': exception_details(exc)}
            self.publish({**row, "phase": "cancelled" if isinstance(exc, asyncio.CancelledError) else "error", "endedAt": time.time(), **failure})
            raise
        finally:
            CURRENT_CALL.reset(token)
            CURRENT_PROVIDER.reset(owner)

    def instrument_provider(self, sid, provider):
        """Observe the public complete boundary; Core hook callbacks may use new tasks.

        Keeping request identity here avoids pairing concurrent responses by
        arrival order. SDK hook events still supply retry status through the
        task-local call identity inherited by their dispatch tasks.
        """
        if id(provider) in self.provider_wrappers:
            return self.provider_wrappers[id(provider)]
        if getattr(provider,"_amplifier_web_observed",False):
            return provider
        original = getattr(provider,"complete",None)
        if not callable(original):
            return provider
        @wraps(original)
        async def complete(request, **kwargs):
            if CURRENT_PROVIDER.get() == id(provider):
                return await original(request, **kwargs)
            return await self.provider_call(sid, provider, request, lambda: original(request, **kwargs))

        original_stream = getattr(provider, "stream", None)
        async def stream(request, **kwargs):
            if CURRENT_PROVIDER.get() == id(provider):
                iterator = original_stream(request, **kwargs)
                try:
                    async for chunk in iterator:
                        yield chunk
                finally:
                    if callable(getattr(iterator, "aclose", None)):
                        await iterator.aclose()
                return
            row = await self._begin_provider_call(sid, provider, request)
            self.streaming_calls.add(row["id"])
            self.publish(row)
            iterator = None
            phase = "completed"
            async def scoped(awaitable):
                # Do not leak context across a yield or retain a token that
                # another task (timeout/close) would have to reset.
                token = CURRENT_CALL.set(row["id"])
                owner = CURRENT_PROVIDER.set(id(provider))
                try:
                    return await awaitable
                finally:
                    CURRENT_CALL.reset(token)
                    CURRENT_PROVIDER.reset(owner)
            try:
                iterator = original_stream(request, **kwargs).__aiter__()
                while True:
                    try:
                        chunk = await scoped(anext(iterator))
                    except StopAsyncIteration:
                        break
                    # Stream usage is a whole-call cumulative snapshot, not a
                    # token delta. A later snapshot replaces the receipt.
                    usage = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                    if usage is not None:
                        self.publish({**self.nodes[row["id"]], "usage": public_usage(usage)})
                    yield chunk
            except GeneratorExit:
                phase = "interrupted"
                raise
            except BaseException as exc:
                phase = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                raise
            finally:
                try:
                    if callable(getattr(iterator, "aclose", None)):
                        await scoped(iterator.aclose())
                except BaseException:
                    phase = "error"
                    raise
                finally:
                    self.publish({**self.nodes[row["id"]], "phase": phase, "endedAt": time.time()})
                    self.streaming_calls.discard(row["id"])

        # Providers are plugin instances implementing the public complete
        # protocol. No SDK internals or request payloads are inspected.
        try:
            provider.complete = complete
            if callable(original_stream):
                provider.stream = stream
            provider._amplifier_web_observed = True
        except (AttributeError,TypeError):
            # Immutable provider instances still get the same authoritative
            # complete boundary via the host's ordinary provider registry.
            class ObservedProvider:
                _amplifier_web_observed = True
                def __getattr__(self, name): return getattr(provider, name)
            wrapper = ObservedProvider()
            wrapper.original = provider
            wrapper.complete = complete
            if callable(original_stream):
                wrapper.stream = stream
            self.provider_wrappers[id(provider)] = wrapper
            return wrapper
        return provider

    def hook(self, sid, event, data):
        if data.get("session_id", sid) != sid:
            return
        parent, turn = self.parent(sid)
        now = time.time()
        if event.startswith("tool:"):
            call = data.get("tool_call_id")
            if not call:
                return
            key = (sid, call)
            row = self.calls.get(key) or {"id": f"tool:{sid}:{call}", "parentId": parent, "turnId": turn,
                "sessionId": sid, "rootSessionId": self.root_id, "kind": "tool", "toolCallId": call,
                "label": str(data.get("tool_name") or "Tool")[:120], "startedAt": now}
            row = dict(row)
            row["phase"] = {"tool:pre": "running", "tool:post": "completed", "tool:error": "error"}[event]
            # The observer supplies transient lifecycle metadata only. Tool
            # inputs/results are read from their native event-log records.
            row["liveObservation"] = True
            if event == "tool:pre":
                row["endedAt"] = None
            else:
                if row.get("endedAt") is None: row["endedAt"] = now
                result = data.get("result") if "result" in data else data.get("tool_result", {})
                if hasattr(result, "model_dump"): result = result.model_dump()
                if event == "tool:error" or isinstance(result, dict) and result.get("success") is False:
                    row["phase"] = "error"

            self.calls[key] = row
            self.publish(row)
        elif event in {"llm:request", "llm:response", "provider:retry"}:
            current = CURRENT_CALL.get()
            if current:
                row = self.nodes.get(current)
                if row and event == "provider:retry":
                    self.publish({**row,"phase":"retrying"})
                elif row and current in self.streaming_calls and event == "llm:response" and data.get("usage") is not None:
                    self.publish({**row, "usage": public_usage(data["usage"])})
                return
            purpose = CALL_PURPOSE.get() or {}
            if purpose: parent, turn = None, purpose.get("turnId", turn)
            scope = {"label": purpose.get("label", "Model call"), "lifecycle": "background" if purpose.get("lifecycle")=="background" else "turn"}
            task = asyncio.current_task()
            if task is None:
                return
            row = self.requests.get(task)
            if event == "llm:request":
                row = {"id": "llm:" + str(uuid.uuid4()), "parentId": parent, "turnId": turn,
                    "sessionId": sid, "rootSessionId": self.root_id, "kind": "llm", "phase": "running",
                    **scope, "startedAt": now,
                    **{key:str(data[key])[:160] for key in ("provider", "model") if data.get(key)}}
                self.requests[task] = row
            elif row is None:
                # Providers may not implement request hooks. A response is still
                # useful, but never attach it to an unrelated concurrent call.
                if event != "llm:response":
                    return
                row = {"id": "llm:" + str(uuid.uuid4()), "parentId": parent, "turnId": turn,
                    "sessionId": sid, "rootSessionId": self.root_id, "kind": "llm", **scope}
            row = dict(row)
            if event == "llm:response":
                row.update(phase="error" if data.get("status") == "error" else "completed", endedAt=now,
                    usage=public_usage(data.get("usage")),
                    **{key:str(data[key])[:160] for key in ("provider", "model") if data.get(key)})
                self.requests.pop(task, None)
            elif event == "provider:retry":
                row["phase"] = "retrying"
            self.publish(row)

    def usage(self):
        calls = [row for row in self.nodes.values() if row["kind"] == "llm"]
        usages = [with_gross_tokens(row.get("usage", {})) for row in calls]
        totals = {key:sum(usage.get(key, 0) for usage in usages)
                  for key in ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens", "grossInputTokens", "grossTotalTokens")}
        priced = [row for row in calls if "costUsd" in row.get("usage", {})]
        totals.update(costUsd=sum(row["usage"]["costUsd"] for row in priced) if priced else None,
                      costType="reported" if priced and len(priced) == len(calls) else "partial" if priced else "unavailable")
        # Usage inspection remains a metadata trace; full tool fields are read
        # independently through conversation detail, never duplicated in totals.
        trace=[{key:value for key,value in row.items() if key not in {"input","output","error"}} for row in list(self.nodes.values())[-500:]]
        return {"calls": len(calls), "usage": totals, "trace": trace}
