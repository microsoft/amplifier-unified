"""Optional Core hook adapter; importing the standalone package needs no Core."""
import asyncio
import os
import time

from .publisher import Publisher
from .reader import DEFAULT_DIRECTORY
from .schema import plan

__amplifier_module_type__ = "hook"
EVENTS = ("session:start", "session:resume", "session:end", "prompt:submit", "prompt:complete",
          "llm:request", "llm:response", "llm:error", "tool:pre", "tool:post", "tool:error")
# Optional producers advertise these through the standard contribution channel.
OPTIONAL_EVENTS = {"approval:required", "approval:resolved", "question:asked", "question:answered"}


def accepted_plan(result, proposed=None, *, include_text=False):
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if not isinstance(result, dict) or result.get("success") is False:
        return None
    result = result.get("output", result)
    if not isinstance(result, dict):
        return None
    items = result.get("todos")
    if items is None and result.get("status") in {"created", "updated"}:
        items = proposed
    safe = plan(items, include_text=include_text)
    if safe["state"] != "observed":
        return None
    return [{"status": row["status"], **({"content": row["text"]} if "text" in row else {})}
            for row in safe["items"]]


class Observer:
    def __init__(self, publisher, session_id, *, where=None):
        self.publisher, self.session_id = publisher, session_id
        self.value = {"where": where or {}, "now": {"status": "idle", "tools": [], "since": time.time()}}
        self.proposals = {}
        self.pending = {'approval': set(), 'question': set()}
        self.uncertain = set()

    def event(self, event, data):
        if data.get("session_id", self.session_id) != self.session_id:
            return
        now = self.value["now"]
        if event in {"prompt:submit", "llm:request"}:
            now.update(status="thinking", since=time.time())
            if event == "prompt:submit":
                now["turnId"] = data.get("turn_id") or data.get("input_id")
                self.value["error"] = False
        elif event in {"prompt:complete", "session:start", "session:resume"}:
            now.update(status="idle", tools=[], since=time.time())
        elif event in {"tool:pre", "tool:post", "tool:error"}:
            key = data.get("tool_call_id") or data.get("call_id")
            tool = data.get("tool_name") or data.get("tool")
            if not isinstance(key, str):
                return  # No invented association between independent tool calls.
            now["tools"] = [row for row in now["tools"] if row["id"] != key]
            if event == "tool:pre":
                if len(now["tools"]) < 32:
                    now["tools"].append({"id": key, "name": tool, "startedAt": time.time()})
                args = data.get("tool_input") or data.get("input") or {}
                if tool == "todo" and isinstance(args, dict) and args.get("action") in {"create", "update"} and len(self.proposals) < 32:
                    self.proposals[key] = accepted_plan({"todos": args.get("todos")}, include_text=self.publisher.include_text)
            else:
                proposed = self.proposals.pop(key, None)
                if tool == "todo":
                    # A failed/invalid latest plan is unavailable, not the previous plan.
                    self.value["planItems"] = (accepted_plan(data.get("result"), proposed,
                                                            include_text=self.publisher.include_text)
                                               if event == "tool:post" else None)
            now.update(status="tool" if now["tools"] else "thinking", since=time.time())
        elif event == "llm:error":
            now.update(status="error", since=time.time())
            self.value["error"] = True
        elif event == "llm:response":
            usage = data.get("usage") or {}
            self.value["usage"] = {target: usage.get(source) for source, target in
                (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"), ("total_tokens", "totalTokens"), ("cost_usd", "costUsd"))} if isinstance(usage, dict) else {}
            self.value["usageScope"] = "last_call"
            for key in ("provider", "model"):
                self.value["where"][key] = data.get(key)
        elif event in OPTIONAL_EVENTS:
            kind, phase = event.split(':')
            identity = data.get(kind + '_id') or data.get('id')
            if not isinstance(identity, str) or len(identity) > 200:
                self.uncertain.add(kind)
            elif phase in {'required', 'asked'}:
                if len(self.pending[kind]) < 32:
                    self.pending[kind].add(identity)
                else:
                    self.uncertain.add(kind)
            else:
                self.pending[kind].discard(identity)
            now['pendingQuestions'] = None if 'question' in self.uncertain else len(self.pending['question'])
            now['pendingApprovals'] = None if 'approval' in self.uncertain else len(self.pending['approval'])
            now.update(status=('waiting_approval' if self.pending['approval'] else
                               'waiting_person' if self.pending['question'] else
                               'unknown' if self.uncertain else 'thinking'), since=time.time())
        if self.pending['approval']:
            now['status'] = 'waiting_approval'
        elif self.pending['question']:
            now['status'] = 'waiting_person'
        elif self.uncertain:
            now['status'] = 'unknown'
        self.publisher.submit(self.session_id, self.value)
        if event == "session:end":
            self.publisher.end(self.session_id)


async def mount(coordinator, config=None):
    config = config or {}
    # Unified already owns canonical lifecycle/attention, including events after
    # a worker dies. Do not compete with that adapter when a bundle includes us.
    if coordinator.config.get("application_host") in {"Amplifier Web", "Amplifier Unified"}:
        return {"name": "hooks-status-emitter", "status": "host-owned"}
    if coordinator.get_capability("status.emitter"):
        return {"name": "hooks-status-emitter", "status": "already-mounted"}
    directory = config.get("directory") or os.environ.get("AMPLIFIER_STATUS_DIR") or DEFAULT_DIRECTORY
    session = coordinator.session
    from importlib.metadata import version, PackageNotFoundError
    try:
        host_version = version('amplifier-app-cli')
    except PackageNotFoundError:
        host_version = 'unknown'
    publisher = observer = None
    # Module validation can mount hooks on a throwaway coordinator without ever
    # starting a session. Defer publication/thread creation to a real lifecycle.
    coordinator.register_capability("status.emitter", True)
    subscribed = set()

    async def receive(event, data):
        nonlocal publisher, observer
        from amplifier_core import HookResult
        try:
            if observer is None:
                if event == "session:end":
                    return HookResult()
                publisher = Publisher(directory, host="cli", version=host_version,
                                      include_text=config.get("include_plan_text") is True)
                observer = Observer(publisher, session.session_id, where={"parentSessionId": session.parent_id,
                                   "cwd": os.getcwd(), "workspace": os.getcwd(), "bundle": config.get("bundle")})
                coordinator.register_capability("status.emitter", observer)
            if event in {"session:start", "session:resume"}:
                # Subscribers discover optional names at the normal session
                # boundary, after all producer modules have mounted.
                contributions = await asyncio.wait_for(coordinator.collect_contributions("observability.events"), timeout=.1)
                for names in contributions[:64]:
                    if isinstance(names, (list, tuple)):
                        for name in names[:128]:
                            if name in OPTIONAL_EVENTS:
                                subscribe(name)
            observer.event(event, data)
            if event == "session:end":
                await asyncio.to_thread(publisher.close)
        except Exception:
            # Telemetry failure must neither alter execution nor expose payloads.
            pass
        return HookResult()

    def subscribe(event):
        if event not in subscribed:
            coordinator.hooks.register(event, receive, name="status-emitter", priority=200)
            subscribed.add(event)

    for event in EVENTS:
        subscribe(event)
    return {"name": "hooks-status-emitter", "version": "0.1.0"}
