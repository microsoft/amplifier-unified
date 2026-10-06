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
from .provider_wait import observation

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


def _receiving_usage(value, *, failure=False):
    """Project a receipt, not SDK/Core defaults or an attempt subtotal."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(exclude_unset=True) if hasattr(value, "model_fields_set") else dump()
    if not isinstance(value, dict):
        return None
    value = dict(value)
    scoped = False
    counters = ("input_tokens", "output_tokens", "total_tokens",
                "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
    if "attempts" in value or "cost_scope" in value:
        # These producer summaries describe the entire logical call, including
        # unknown attempts. Never aggregate their private attempt receipts here.
        if (not isinstance(value.get("attempts"), list) or not value["attempts"]
                or not all(isinstance(attempt, dict) for attempt in value["attempts"])
                or value.get("cost_scope") not in {"reported_attempts", "known_attempts"}
                or type(value.get("cost_complete")) is not bool
                or value.get("cost_is_estimate") is not True
                or not all(key in value for key in (*counters, "cost_usd"))):
            return None
        if any(value[key] is not None and
               (type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0)
               for key in counters):
            return None
        scoped = True
        if value["cost_complete"] and value["cost_scope"] == "reported_attempts":
            value["cost_type"] = "estimated"
        else:
            value.pop("cost_usd", None)
    elif "usage_complete" in value or "cost_callback_state" in value:
        if (type(value.get("usage_complete")) is not bool
                or value.get("cost_callback_state") not in {"not_invoked", "returned", "unknown"}
                or not all(key in value for key in ("input_tokens", "output_tokens", "cost_usd"))):
            return None
        scoped = True
        if failure:
            for source, target in (("cache_read_input_tokens", "cache_read_tokens"),
                                   ("cache_creation_input_tokens", "cache_write_tokens")):
                if source in value:
                    value[target] = value[source]
        # The provider's complete receipt already used its price calculator.
        # Callback ownership is not billing confirmation; this stays estimated.
        known = all(type(value.get(key)) is int and value[key] >= 0
                    for key in ("input_tokens", "output_tokens",
                                "cache_read_input_tokens", "cache_creation_input_tokens"))
        if (value["usage_complete"] and known and value.get("service_tier") == "standard"
                and value.get("speed") in {"standard", "fast"}):
            value["cost_type"] = "estimated"
        else:
            value.pop("cost_usd", None)
    projected = public_usage(value)
    if scoped:
        for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                               ("total_tokens", "totalTokens"), ("cache_read_tokens", "cacheReadTokens"),
                               ("cache_write_tokens", "cacheWriteTokens"), ("reasoning_tokens", "reasoningTokens")):
            if source in value and value[source] is None:
                projected.pop(target, None)
        projected = with_gross_tokens(projected)
    if not scoped and not any(key != "costType" for key in projected):
        return None
    return projected, scoped


class ExecutionEvents:
    def __init__(self, root_id, emit):
        self.root_id, self.emit = root_id, emit
        self.producer_id = str(uuid.uuid4())
        self.turn_id = None
        self.calls = {}
        self.children = {}
        self.last_foreground_provider = {}
        self.requests = weakref.WeakKeyDictionary()
        self.nodes = {}
        self.admission_guard = None
        self.provider_wrappers = {}
        self.streaming_calls = set()
        self.cumulative_usage = set()

    def _observe_usage(self, call, sid, read, *, failure=False):
        """Synchronous best-effort metadata; only the exact still-open call."""
        row = self.nodes.get(call)
        if (not row or row.get("sessionId") != sid or row.get("kind") != "llm"
                or row.get("endedAt") is not None):
            return
        task = asyncio.current_task()
        cancellations = task.cancelling() if task else 0
        try:
            receipt = _receiving_usage(read(), failure=failure)
        except (Exception, asyncio.CancelledError) as secondary:
            if (isinstance(secondary, asyncio.CancelledError) and task
                    and task.cancelling() > cancellations):
                raise
            return
        if receipt is None:
            return
        usage, scoped = receipt
        previous = row.get("usage", {})
        if not scoped:
            # An unscoped partial cannot invalidate known cumulative evidence,
            # in either arrival order. Replacement is never additive.
            measured = set(previous) - {"costType", "costSource"}
            if call in self.cumulative_usage or not measured.issubset(usage):
                return
        else:
            self.cumulative_usage.add(call)
        self.publish({**row, "usage": usage})

    def publish(self, row):
        # Observe both wrapped and hook-only provider paths. Optional naming or
        # compaction calls retain their own usage but do not relabel the worker.
        if row.get("kind") == "llm" and row.get("provider") and not CALL_PURPOSE.get() and row.get("lifecycle") != "background":
            sid = row.get("sessionId")
            self.last_foreground_provider[sid] = row["provider"]
            child = self.children.get(sid)
            if child:
                observed = {**child, "provider": row["provider"], "model": row.get("model")}
                if observed != child:
                    self.children[sid] = observed
                    self.publish(observed)
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
                   "sessionId": identity, "rootSessionId": self.root_id, "parentSessionId": parent_sid, "kind": "worker",
                   "phase": event.get("status", "running"), "label": event.get("agent") or "Worker",
                   "toolCallId": call_id, "startedAt": previous.get("startedAt", time.time())}
            if row["phase"] in {"completed", "cancelled", "error", "interrupted"}:
                row["endedAt"] = previous.get("endedAt") or time.time()
            else:
                row["endedAt"] = None
            if isinstance(event.get("report"),str) and event["report"]:
                row["summary"]=event["report"][:12000]
            elif previous.get("summary"):row["summary"]=previous["summary"]
            same_run = not event.get("runId") or event.get("runId") == previous.get("runId")
            if not same_run:
                self.last_foreground_provider.pop(identity, None)
            if not same_run or not previous:
                # Snapshot the spawning parent's observation. Later parent
                # model changes must not rewrite the cause of existing work.
                if self.last_foreground_provider.get(parent_sid):
                    row["parentProvider"] = self.last_foreground_provider[parent_sid]
            for key in ("provider", "model", "parentProvider"):
                if same_run and key in previous:
                    row[key] = previous[key]
            for key in ("routing", "runId"):
                if key in event:
                    if key == "routing":
                        from .host.model_selection import public_routing
                        row[key] = public_routing(event[key])
                    else:
                        row[key] = event[key]
                elif same_run and key in previous:
                    row[key] = previous[key]
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
               "purpose": purpose.get("purpose") if purpose else None,
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
        metadata = getattr(request, "metadata", None) or {}
        purpose_token = None
        if isinstance(metadata, dict) and metadata.get("purpose") == "context-compaction":
            # Auxiliary output must never appear as a conversational response.
            # Keep its measured usage in the owning turn with a distinct label.
            purpose_token = CALL_PURPOSE.set({**(CALL_PURPOSE.get() or {}), "label": "Context compaction", "purpose": "context_compaction"})
            label = "Context compaction"
        try:
            return await self._observed_provider_call(sid, provider, request, invoke, label=label)
        finally:
            if purpose_token is not None:
                CALL_PURPOSE.reset(purpose_token)

    async def _observed_provider_call(self, sid, provider, request, invoke, *, label=None):
        row = await self._begin_provider_call(sid, provider, request, label=label)
        token = CURRENT_CALL.set(row["id"])
        owner = CURRENT_PROVIDER.set(id(provider))
        self.publish(row)
        try:
            response = await invoke()
            usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
            self.publish({**self.nodes[row["id"]], "phase": "completed", "endedAt": time.time(), "usage": public_usage(usage)})
            return response
        except BaseException as exc:
            self._observe_usage(row["id"], sid, lambda: getattr(exc, "usage", None), failure=True)
            from .session_health import exception_details
            failure = {} if isinstance(exc, asyncio.CancelledError) else {'failure': exception_details(exc)}
            self.publish({**self.nodes[row["id"]], "phase": "cancelled" if isinstance(exc, asyncio.CancelledError) else "error", "endedAt": time.time(), **failure})
            raise
        finally:
            self.cumulative_usage.discard(row["id"])
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

        original_compact = getattr(provider, "compact_context", None)
        async def compact_context(request, **kwargs):
            return await self.provider_call(sid, provider, request,
                lambda: original_compact(request, **kwargs), label="Context compaction")

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
                        self._observe_usage(row["id"], sid, lambda: usage)
                    yield chunk
            except GeneratorExit:
                phase = "interrupted"
                raise
            except BaseException as exc:
                phase = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                self._observe_usage(row["id"], sid, lambda: getattr(exc, "usage", None), failure=True)
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
                    self.cumulative_usage.discard(row["id"])

        # Providers are plugin instances implementing the public complete
        # protocol. No SDK internals or request payloads are inspected.
        try:
            provider.complete = complete
            if callable(original_compact):
                provider.compact_context = compact_context
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
            if callable(original_compact):
                wrapper.compact_context = compact_context
            if callable(original_stream):
                wrapper.stream = stream
            self.provider_wrappers[id(provider)] = wrapper
            return wrapper
        return provider

    def hook(self, sid, event, data):
        if not isinstance(data, dict):
            return
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
        elif event == "llm:progress":
            # Only the host's task-local boundary owns identity. Never fall back
            # to a payload request/model ID or pair concurrent calls by arrival.
            row = self.nodes.get(CURRENT_CALL.get())
            safe = observation(data)
            if (not row or row.get("sessionId") != sid or row.get("kind") != "llm"
                    or row.get("endedAt") is not None
                    or row.get("phase") not in {"running", "retrying"}
                    or safe is None):
                return
            previous = row.get("providerWait", {})
            if safe["attempt"] < previous.get("attempt", 0):
                return
            if safe["attempt"] == previous.get("attempt"):
                if safe["limits"] != previous["limits"] or safe["observation"] == "attempt_started":
                    return
            wait = {key: safe[key] for key in ("version", "attempt", "limits")}
            wait["observedAt"] = now
            if "lastResponseActivityAt" in previous:
                wait["lastResponseActivityAt"] = previous["lastResponseActivityAt"]
            if safe["observation"] == "response_activity":
                wait["lastResponseActivityAt"] = now
            self.publish({**row, "providerWait": wait})
        elif event in {"llm:request", "llm:response", "provider:retry"}:
            current = CURRENT_CALL.get()
            if current:
                row = self.nodes.get(current)
                if not row or row.get("sessionId") != sid or row.get("endedAt") is not None:
                    return
                if row and event == "provider:retry":
                    self.publish({**row,"phase":"retrying"})
                elif event == "llm:response":
                    self._observe_usage(current, sid, lambda: data.get("usage"),
                                        failure=data.get("status") in {"error", "cancelled"})
                return
            purpose = CALL_PURPOSE.get() or {}
            if purpose: parent, turn = None, purpose.get("turnId", turn)
            scope = {"purpose": purpose.get("purpose"), "label": purpose.get("label", "Model call"), "lifecycle": "background" if purpose.get("lifecycle")=="background" else "turn"}
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
