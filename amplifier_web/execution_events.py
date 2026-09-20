"""Public execution metadata, correlated without retaining model payloads."""
from __future__ import annotations

import asyncio
import contextvars
from functools import wraps
import math
import json
import time
import uuid
import weakref

from .execution_details import tool_detail

CALL_PURPOSE = contextvars.ContextVar('amplifier_web_call_purpose',default=None)
CURRENT_CALL = contextvars.ContextVar("amplifier_web_public_call", default=None)


def public_usage(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {"costType": "unavailable"}
    result = {}
    for source, target in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                           ("cache_read_tokens", "cacheReadTokens"), ("cache_write_tokens", "cacheWriteTokens"),
                           ("total_tokens", "totalTokens")):
        number = value.get(source)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0:
            result[target] = int(number)
    if "totalTokens" not in result and "inputTokens" in result and "outputTokens" in result:
        result["totalTokens"] = result["inputTokens"] + result["outputTokens"]
    try:
        cost = float(value["cost_usd"])
        if not math.isfinite(cost) or cost < 0:
            raise ValueError()
        result.update(costUsd=cost, costType="reported")
    except (KeyError, TypeError, ValueError):
        result["costType"] = "unavailable"
    return result


class ExecutionEvents:
    def __init__(self, root_id, emit):
        self.root_id, self.emit = root_id, emit
        self.turn_id = None
        self.calls = {}
        self.children = {}
        self.requests = weakref.WeakKeyDictionary()
        self.nodes = {}

    def publish(self, row):
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

    def instrument_provider(self, sid, provider):
        """Observe the public complete boundary; Core hook callbacks may use new tasks.

        Keeping request identity here avoids pairing concurrent responses by
        arrival order. SDK hook events still supply retry status through the
        task-local call identity inherited by their dispatch tasks.
        """
        if getattr(provider,"_amplifier_web_observed",False):
            return
        original = getattr(provider,"complete",None)
        if not callable(original):
            return
        @wraps(original)
        async def complete(request, **kwargs):
            parent,turn = self.parent(sid)
            purpose=CALL_PURPOSE.get()
            if purpose:parent,turn=None,purpose.get('turnId',turn)
            info = provider.get_info()
            defaults = getattr(info,"defaults",{}) or {}
            row = {"id":"llm:"+str(uuid.uuid4()),"parentId":parent,"turnId":turn,"sessionId":sid,
                   "rootSessionId":self.root_id,"kind":"llm","phase":"running","label":purpose["label"] if purpose else "Model call",
                   "provider":str(getattr(info,"id",type(provider).__name__))[:160],
                   "model":str(getattr(request,"model",None) or defaults.get("model") or defaults.get("default_model") or "")[:160],
                   "startedAt":time.time(),"lifecycle":"background" if purpose and purpose.get("lifecycle")=="background" else "turn"}
            token = CURRENT_CALL.set(row["id"])
            self.publish(row)
            try:
                response = await original(request, **kwargs)
                self.publish({**row,"phase":"completed","endedAt":time.time(),"usage":public_usage(getattr(response,"usage",None))})
                return response
            except BaseException as exc:
                self.publish({**row,"phase":"cancelled" if isinstance(exc,asyncio.CancelledError) else "error","endedAt":time.time()})
                raise
            finally:
                CURRENT_CALL.reset(token)
        # Providers are plugin instances implementing the public complete
        # protocol. No SDK internals or request payloads are inspected.
        try:
            provider.complete = complete
            provider._amplifier_web_observed = True
        except (AttributeError,TypeError):
            return

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
            arguments=data.get("tool_input",{})
            if event=="tool:pre" and isinstance(arguments,dict):
                row.update(endedAt=None, output=None, error=None)
                row["input"] = tool_detail(arguments)
                try: public_arguments = json.loads(row["input"])
                except (ValueError, TypeError): public_arguments = {}
                operation = next((public_arguments.get(key) for key in ("description", "action", "operation", "file_path", "path") if isinstance(public_arguments, dict) and isinstance(public_arguments.get(key), str)), "")
                row["purpose"] = tool_detail(operation)[:200]
                row["summary"] = "Running " + row["label"] + (" · " + row["purpose"] if row["purpose"] else "")
            if event != "tool:pre":
                if row.get("endedAt") is None: row["endedAt"] = now
                result=data.get("tool_result",{})
                if hasattr(result,"model_dump"):result=result.model_dump()
                failed=event=="tool:error" or (isinstance(result,dict) and result.get("success") is False)
                if failed:row["phase"]="error"
                row["summary"] = ("Failed " if failed else "Completed ") + row["label"] + (" · " + row.get("purpose", "") if row.get("purpose") else "")
                if "tool_result" in data: row["output"] = tool_detail(result)
                error = data.get("error") or data.get("error_message") or (result.get("error") if isinstance(result, dict) else None)
                if error is not None: row["error"] = tool_detail(error)

            self.calls[key] = row
            self.publish(row)
        elif event in {"llm:request", "llm:response", "provider:retry"}:
            current = CURRENT_CALL.get()
            if current:
                row = self.nodes.get(current)
                if row and event == "provider:retry":
                    self.publish({**row,"phase":"retrying"})
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
        totals = {key:sum(row.get("usage", {}).get(key, 0) for row in calls)
                  for key in ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens")}
        priced = [row for row in calls if "costUsd" in row.get("usage", {})]
        totals.update(costUsd=sum(row["usage"]["costUsd"] for row in priced) if priced else None,
                      costType="reported" if priced and len(priced) == len(calls) else "partial" if priced else "unavailable")
        # Usage inspection remains a metadata trace; full tool fields are read
        # independently through conversation detail, never duplicated in totals.
        trace=[{key:value for key,value in row.items() if key not in {"input","output","error"}} for row in list(self.nodes.values())[-500:]]
        return {"calls": len(calls), "usage": totals, "trace": trace}
