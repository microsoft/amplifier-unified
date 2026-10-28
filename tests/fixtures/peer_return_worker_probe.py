"""Real AppService -> Worker processes -> Core/Foundation -> loop-live journey.

Run in the prepared guest:
    python tests/fixtures/peer_return_worker_probe.py /tmp/new-peer-return-probe

Only provider decisions and synthetic artifact production are scripted. All
agent actions traverse the real app_control tool/Worker bridge. No admission,
subscription, native checkpoint, sealing, queue drain or lifecycle is mocked.
Observer waits only inspect state: they never call drain, emit events, subscribe,
or send completion messages. The Stop barrier delays a provider response only.

Limits: non-streaming fake provider; no real model/network, browser, restart,
compaction, task-change matrix, or real filesystem-tool efficacy. File bytes are
produced by fixture Python in each root's own execution folder. The host retains
them through coordination.reply and participants read through coordination.artifact.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import traceback


SOURCE = Path(__file__).resolve().parents[2]
REVIEW = "original-review"
CORRECTIONS = ("correction-1", "correction-2")
STOP_REQUEST = "late-review"
AUTHOR_SECRET = "author-private-context-marker"
REVIEWER_SECRET = "reviewer-private-context-marker"
FINAL = "AUTHOR_FINAL_ACCEPTED"
REPORTS = {
    "report-1": "Review v1: correct the first missing invariant.\n",
    "report-2": "Review v2: first correction checked; correct the adjacent invariant.\n",
    "report-3": "Review v3: both corrections checked; final disposition accepted.\n",
    "corrected-1": "Candidate v1: first invariant corrected.\n",
    "corrected-2": "Candidate v2: first and adjacent invariants corrected.\n",
    "late-report": "Late result remains readable after the author's Stop.\n",
}


def reference(name):
    digest = hashlib.sha256(REPORTS[name].encode()).hexdigest()
    return f"{name}.txt@sha256:{digest}"


def qualification_runtime():
    """Check prerequisites before importing product code; do not install/resolve."""
    problems, installed, paths = [], {}, {}
    if sys.version_info < (3, 13):
        problems.append("Python >=3.13 required")
    for distribution, module in (
        ("amplifier-core", "amplifier_core"),
        ("amplifier-foundation", "amplifier_foundation"),
        ("amplifier-module-loop-live", "amplifier_module_loop_live"),
        ("amplifier-module-context-simple", "amplifier_module_context_simple"),
    ):
        try:
            dist = importlib.metadata.distribution(distribution)
            spec = importlib.util.find_spec(module)
            if spec is None or spec.origin is None:
                raise RuntimeError(f"{module} is not importable")
            paths[module] = Path(spec.origin).parent.parent
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
            installed[distribution] = {
                "version": dist.version,
                "commit": direct.get("vcs_info", {}).get("commit_id"),
            }
        except (ImportError, importlib.metadata.PackageNotFoundError, RuntimeError) as exc:
            problems.append(f"{distribution}: {exc}")
    version = installed.get("amplifier-core", {}).get("version", "")
    numbers = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not numbers or tuple(map(int, numbers.groups())) < (2, 0, 2):
        problems.append(f"Core >=2.0.2 required; observed {version or 'missing'}")
    if problems:
        raise RuntimeError(
            "PEER_RETURN_QUALIFICATION_PREREQUISITE: use the prepared offline "
            "Python >=3.13 / Core >=2.0.2 dev runner with pinned loop-live and "
            "context-simple. No installation or fallback was attempted. "
            + "; ".join(problems)
        )
    return installed, paths


def message_text(message):
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content or []
                   if getattr(block, "type", None) == "text")


def tool_output(request, identity):
    row = next((row for row in reversed(request.messages)
                if row.role == "tool" and row.tool_call_id == identity), None)
    assert row is not None, f"Missing actual native app_control result: {identity}"
    value = json.loads(message_text(row))
    # The native loop may serialize ToolResult or its output, as in the existing
    # collaboration_native_probe fixture. Neither path supplies synthetic success.
    if isinstance(value, dict) and "success" in value and "output" in value:
        assert value["success"] is True, value
        value = value["output"]
    assert value.get("accepted") is True, value
    return value


class PeerReturnProvider:
    name = "peer-return-fixture"

    def __init__(self, config):
        # Provider metadata discovery constructs with an empty configuration.
        # Only the mounted runtime instance receives the scenario and role.
        self.role = config.get("role", "metadata-only")
        self.scenario = Path(config["scenario"]) if config.get("scenario") else None
        self.plan = None
        self.pending = None
        self.sequence = 0

    def get_info(self):
        from amplifier_core import ProviderInfo
        return ProviderInfo(id=self.name, display_name="Offline peer return",
                            capabilities=["tools"], defaults={"model": self.role})

    async def list_models(self):
        return []

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    def record(self, kind, **data):
        row = {"kind": kind, "role": self.role, "pid": os.getpid(),
               "cwd": os.getcwd(), "inputId": self.input_id, **data}
        with (self.scenario.parent / f"{self.role}.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def call(self, action, args, identity=None):
        self.sequence += 1
        identity = identity or f"{self.role}:{self.input_id}:{self.sequence}"
        value = yield (identity, action, args)
        return value

    def send(self, identity, recipient, text, reply_to=None):
        args = {"sessionId": recipient, "mode": "queue", "text": text,
                "returnNotification": True}
        if reply_to:
            args["replyToRequestId"] = reply_to
        receipt = yield from self.call("coordination.send", args, identity)
        assert receipt["requestId"] == receipt["inputId"] == identity
        assert receipt["senderSessionId"] == self.sid
        assert receipt["target"]["sessionId"] == recipient
        assert receipt["returnNotification"] is True
        assert receipt["subscription"]["status"] == "waiting", receipt
        assert receipt["returnState"]["returnPolicy"] == "requested"
        assert receipt["returnState"]["subscription"]["registered"] is True
        self.record("send", receipt=receipt)

    def stage(self, identity, revision, name, *, final):
        path = Path.cwd() / f"{name}.txt"
        assert not path.exists(), path
        path.write_text(REPORTS[name], encoding="utf-8")
        reply = yield from self.call("coordination.reply", {
            "requestId": identity, "expectedResponseRevision": revision,
            "kind": "result" if final else "defer",
            "outcome": "success" if final else "deferred",
            "text": REPORTS[name].strip(), "references": [reference(name)],
        })
        report = reply["result"]
        assert report["status"] == "staged" and report["qualified"] is False, report
        assert report["responseRevision"] == revision + 1
        assert report["artifactVerification"] == "verified"
        artifact, = report["artifacts"]
        assert artifact["status"] == "retained"
        assert artifact["sha256"] == reference(name).split(":")[-1]
        self.record("staged", requestId=identity, report=report)
        # Subsequent reads must use retained bytes, not the producer's live file.
        path.unlink()

    def result(self, identity, revision, *, qualified):
        value = (yield from self.call("coordination.result", {"requestId": identity}))["result"]
        assert value["responseRevision"] == revision, value
        assert value["qualified"] is qualified, value
        state = value["returnState"]
        assert state["returnPolicy"] == "requested"
        assert state["subscription"]["registered"] is True
        assert state["response"]["responseRevision"] == revision
        assert state["qualification"]["independentArtifactVerification"] is False
        self.record("result", requestId=identity, result=value)
        return value

    def artifact(self, identity, revision, name):
        value = (yield from self.call("coordination.artifact", {
            "sessionId": self.sid, "requestId": identity,
            "responseRevision": revision, "reference": reference(name),
        }))["result"]
        assert value["status"] == "retained", value
        assert value["text"] == REPORTS[name], value
        assert value["independentArtifactVerification"] is False
        self.record("artifact", requestId=identity, revision=revision, name=name, artifact=value)

    def check_old_reports(self, result):
        revisions = sorted(result["revisions"], key=lambda row: row["responseRevision"])
        assert [row["responseRevision"] for row in revisions] == list(
            range(1, result["responseRevision"] + 1))
        for report in revisions:
            number = report["responseRevision"]
            assert report["references"] == [reference(f"report-{number}")]
            assert report["status"] == "sealed", report
            assert report["qualified"] is (number == 3)
            assert report["nativeTerminal"]["generationId"] == report["generationId"]
            self.record("sealed-report", report=report)
            yield from self.artifact(REVIEW, number, f"report-{number}")
            if self.role == "author":
                exact = (yield from self.call("coordination.read", {
                    "sessionId": self.values["reviewer"]["id"], "scope": "all",
                    "messageId": report["terminalMessageId"],
                }))["result"]["message"]
                assert exact["text"] == report["nativeTerminal"]["nativeText"]
                assert exact["generationId"] == report["generationId"]

    def journey(self, envelope, text):
        author, reviewer = self.values["author"]["id"], self.values["reviewer"]["id"]
        if envelope is None:
            if text == REVIEWER_SECRET:
                assert self.role == "reviewer"
                return "REVIEWER_CONTEXT_RETAINED"
            if text == AUTHOR_SECRET:
                assert self.role == "author"
                return "AUTHOR_CONTEXT_RETAINED"
            if text == "begin-review":
                assert self.role == "author"
                yield from self.send(REVIEW, reviewer, "Review the candidate; return corrections and final disposition.")
                return "AUTHOR_WAITING_FOR_REVIEW"
            assert text == "stop-probe" and self.role == "author", text
            yield from self.send(STOP_REQUEST, reviewer, "Produce the bounded late-result fixture.")
            return "AUTHOR_WAITING_FOR_LATE_RESULT"

        assert envelope["recipientSessionId"] == self.sid
        assert envelope["senderSessionId"] == (reviewer if self.role == "author" else author)
        assert envelope["mode"] == "queue"
        assert "humanResume" not in envelope and "task" not in envelope
        if self.input_id in CORRECTIONS:
            assert self.role == "author"
            number = CORRECTIONS.index(self.input_id) + 1
            yield from self.stage(self.input_id, 0, f"corrected-{number}", final=True)
            return f"AUTHOR_CORRECTED_{number}"
        if self.input_id == REVIEW:
            assert self.role == "reviewer"
            initial = (yield from self.call("coordination.result", {"requestId": REVIEW}))["result"]
            # The original send, not a later standalone subscription, must have
            # persisted this wait before the recipient can execute even one turn.
            assert initial["returnState"]["subscription"]["status"] == "waiting"
            assert initial["receipt"]["subscription"]["lastClaimedRevision"] == 0
            assert initial["responseRevision"] == 0
            yield from self.stage(REVIEW, 0, "report-1", final=False)
            yield from self.send(CORRECTIONS[0], author, "Correct the first invariant.", REVIEW)
            return "REVIEWER_REQUESTED_CORRECTION_1"
        if self.input_id == STOP_REQUEST:
            assert self.role == "reviewer"
            yield from self.stage(STOP_REQUEST, 0, "late-report", final=True)
            # complete() waits here before emitting any terminal response.
            yield "late-terminal-barrier"
            return "REVIEWER_LATE_RESULT"

        dependency = envelope.get("replyToRequestId")
        if dependency in CORRECTIONS:
            assert self.role == "reviewer"
            number = CORRECTIONS.index(dependency) + 1
            correction = yield from self.result(dependency, 1, qualified=True)
            assert correction["receipt"]["response"]["status"] == "sealed"
            yield from self.artifact(dependency, 1, f"corrected-{number}")
            original = yield from self.result(REVIEW, number, qualified=False)
            yield from self.check_old_reports(original)
            yield from self.stage(REVIEW, number, f"report-{number + 1}", final=number == 2)
            if number == 1:
                yield from self.send(CORRECTIONS[1], author, "Correct the adjacent invariant.", REVIEW)
            return f"REVIEWER_REREVIEWED_{number}"
        assert dependency == REVIEW and self.role == "author", envelope
        revision, = [n for n in (1, 2, 3)
                     if reference(f"report-{n}") in envelope["references"]]
        if revision < 3:
            # Negative reports legitimately notify while still staged. The
            # envelope is enough to acknowledge the interim disposition here;
            # do not race artifact reads against its terminal checkpoint or poll
            # for it. The next reviewer turn and final author turn read all exact
            # sealed revisions and retained bytes.
            return f"AUTHOR_RECEIVED_REVIEW_{revision}"
        result = yield from self.result(REVIEW, revision, qualified=True)
        assert result["returnState"]["notification"]["requestId"] == self.input_id
        yield from self.check_old_reports(result)
        for number, correction in enumerate(CORRECTIONS, 1):
            yield from self.result(correction, 1, qualified=True)
            yield from self.artifact(correction, 1, f"corrected-{number}")
        return FINAL

    async def complete(self, request, **kwargs):
        try:
            return await self._complete(request, **kwargs)
        except Exception:
            if hasattr(self, "input_id"):
                self.record("fixture-error", traceback=traceback.format_exc())
            raise

    async def _complete(self, request, **kwargs):
        from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall, Usage
        assert self.scenario is not None, "Metadata-only provider cannot execute"
        self.values = json.loads(self.scenario.read_text())
        self.sid = self.values[self.role]["id"]
        assert os.getcwd() == self.values[self.role]["directory"]
        full_text = "\n".join(message_text(row) for row in request.messages)
        assert (REVIEWER_SECRET if self.role == "author" else AUTHOR_SECRET) not in full_text
        assert (AUTHOR_SECRET if self.role == "author" else REVIEWER_SECRET) in full_text
        if self.plan is None:
            # Delivery appends current reference observations as user-role
            # messages. They are not a newly admitted task input.
            incoming = next(
                row for row in reversed(request.messages)
                if row.role == "user" and not (
                    (row.metadata or {}).get("coordinationObservation")
                    or (row.metadata or {}).get("workPlaintextObservation")
                    or (row.metadata or {}).get("reactionObservation")
                )
            )
            text = message_text(incoming)
            envelope = None
            if text.startswith("Host-attributed peer task input."):
                envelope = json.loads(text.split("\n", 2)[1])
                self.input_id = envelope["inputId"]
            else:
                human_inputs = {
                    AUTHOR_SECRET: "author-seed", REVIEWER_SECRET: "reviewer-seed",
                    "begin-review": "author-start", "stop-probe": "author-stop-probe",
                }
                assert text in human_inputs, f"Unexpected fixture input: {text!r}"
                self.input_id = human_inputs[text]
            self.record("input", envelope=envelope)
            self.plan = self.journey(envelope, text)
            value = None
        else:
            value = tool_output(request, self.pending)
        try:
            step = self.plan.send(value)
            if step == "late-terminal-barrier":
                self.record("barrier")
                (self.scenario.parent / "late-result-staged").touch()
                async with asyncio.timeout(30):
                    while not (self.scenario.parent / "release-late-result").exists():
                        await asyncio.sleep(.02)
                step = self.plan.send(None)
        except StopIteration as done:
            self.plan = self.pending = None
            self.record("final", text=done.value)
            return ChatResponse(content=[TextBlock(text=done.value)],
                                usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
        self.pending, action, args = step
        self.record("tool", action=action, args=args, commandId=self.pending)
        return ChatResponse(content=[], tool_calls=[ToolCall(
            id=self.pending, name="app_control", arguments={
                "operation": "dispatch", "args": {
                    "action": action, "args": args, "id": self.pending,
                },
            },
        )], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))


async def mount(coordinator, config=None):
    await coordinator.mount("providers", PeerReturnProvider(config), name=PeerReturnProvider.name)


def audit(root):
    return [json.loads(line) for role in ("author", "reviewer")
            if (path := root / f"{role}.jsonl").exists()
            for line in path.read_text().splitlines()]


def identity(session):
    return {key: copy.deepcopy(session.get(key)) for key in (
        "id", "workspace", "location", "workingDirectory", "executionRevision",
        "bundle", "selection", "assignmentId", "collaboration", "parentId", "parentSessionId",
        "runtimeSessionId", "nativeIdentity", "nativeProject",
    )}


async def run(root):
    installed, paths = qualification_runtime()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "review-workspace"
    workspace.mkdir()
    os.environ.update(
        AMPLIFIER_HOME=str(root / "shared"), AMPLIFIER_WEB_HOME=str(root / "app"),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / "legacy"),
        AMPLIFIER_SESSION_STATE_HOME=str(root / "ownership"),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root / "captures"),
        PYTHONPATH=str(SOURCE), UV_OFFLINE="1",
    )
    (root / "shared").mkdir()
    (root / "shared" / "settings.yaml").write_text("bundle:\n  app: []\n")
    sys.path.insert(0, str(SOURCE))
    try:
        from amplifier_web.bundles import SNAPSHOT_VERSION
        from amplifier_web.management import Management
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.service import AppService
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_web.automatic_history import directory
        from amplifier_web.session_identity import project
    except ImportError as exc:
        raise RuntimeError(
            "PEER_RETURN_QUALIFICATION_PREREQUISITE: prepared native/application "
            f"runtime cannot import its required dependencies: {exc}"
        ) from exc

    # Load this file as the local provider module, without importing product code
    # in pytest or copying/editing any existing fixture.
    provider = root / "provider" / "amplifier_module_provider_peer_return_fixture"
    provider.mkdir(parents=True)
    (provider / "__init__.py").write_text(
        "from importlib.util import spec_from_file_location, module_from_spec\n"
        f"_spec = spec_from_file_location('peer_return_script', {str(Path(__file__).resolve())!r})\n"
        "_module = module_from_spec(_spec)\n_spec.loader.exec_module(_module)\n"
        "PeerReturnProvider = _module.PeerReturnProvider\n"
        "mount = _module.mount\n"
    )
    scenario = root / "scenario.json"
    bundles = {}
    for role in ("author", "reviewer"):
        bundle = root / f"{role}.yaml"
        bundle.write_text(
            f"bundle:\n  name: peer-return-{role}\n  version: {SNAPSHOT_VERSION}\n"
            "session:\n  orchestrator:\n    module: loop-live\n"
            f"    source: {json.dumps(str(paths['amplifier_module_loop_live']))}\n"
            "    config:\n      use_streaming: false\n  context:\n    module: context-simple\n"
            f"    source: {json.dumps(str(paths['amplifier_module_context_simple']))}\n"
            "providers:\n  - module: provider-peer-return-fixture\n"
            f"    source: {json.dumps(str(provider.parent))}\n"
            f"    config:\n      role: {role}\n      scenario: {json.dumps(str(scenario))}\n"
        )
        bundles[role] = bundle

    class ObservedApp(AppService):
        async def on_runtime_event(self, kind, payload):
            # Observation only. Never fabricate or suppress a native event.
            result = await super().on_runtime_event(kind, payload)
            events.append((kind, copy.deepcopy(payload)))
            return result

    events = []
    runtime = RuntimeManager(
        command=[sys.executable, "-m", "amplifier_web.runtime_worker"],
        startup_timeout=45,
    )
    app = ObservedApp(root / "app", runtime, workspace=workspace)
    app.management = Management(app)
    runtime.app_bridge, runtime.home = app.app_bridge, root / "app"

    def terminal(text):
        return next((p for kind, p in events if kind == "runtime.generation"
                     and p.get("event") == "generation.finished" and p.get("text") == text), None)

    async def observed(predicate, label):
        try:
            async with asyncio.timeout(90):
                while not predicate():
                    errors = [(kind, p) for kind, p in events
                              if kind == "runtime.error" or p.get("event") == "generation.failed"]
                    assert not errors, (
                        label, errors,
                        [row for row in audit(root) if row["kind"] == "fixture-error"],
                    )
                    await asyncio.sleep(.02)
        except TimeoutError as exc:
            raise AssertionError(f"Timed out observing {label}; last events: {events[-5:]}") from exc

    try:
        async with asyncio.timeout(140):
            roots = {}
            for role in ("author", "reviewer"):
                created = await app.dispatch("session.create", {
                    "title": f"Existing {role}", "bundle": str(bundles[role]),
                    **({"location": {"kind": "managed"}} if role == "author"
                       else {"workspace": str(workspace)}),
                    "select": False,
                }, command_id=f"fixture-create-{role}", include_state=False)
                roots[role] = app._session(created["sessionId"])
            original_ids = {row["id"] for row in app.state["sessions"]}
            assert original_ids == {row["id"] for row in roots.values()}
            scenario.write_text(json.dumps({
                role: {"id": row["id"], "directory": row.get("workingDirectory") or row["workspace"]}
                for role, row in roots.items()
            }))
            assert roots["author"]["workspace"] != roots["reviewer"]["workspace"]
            assert all(not row.get("collaboration") for row in roots.values())

            # Establish independent native history before starting the exchange.
            await app.dispatch("conversation.send", {
                "sessionId": roots["reviewer"]["id"], "text": REVIEWER_SECRET,
            }, command_id="reviewer-seed", include_state=False)
            await observed(lambda: terminal("REVIEWER_CONTEXT_RETAINED"), "reviewer seed")
            await app.dispatch("conversation.send", {
                "sessionId": roots["author"]["id"], "text": AUTHOR_SECRET,
            }, command_id="author-seed", include_state=False)
            await observed(lambda: terminal("AUTHOR_CONTEXT_RETAINED")
                           and all(row.get("status") == "idle" for row in roots.values()),
                           "independent roots seeded and idle")
            before = {role: identity(row) for role, row in roots.items()}
            worker_pids = {role: runtime.workers[row["id"]]["process"].pid
                           for role, row in roots.items()}
            assert len(set(worker_pids.values())) == 2
            await app.dispatch("conversation.send", {
                "sessionId": roots["author"]["id"], "text": "begin-review",
            }, command_id="author-start", include_state=False)
            await observed(lambda: terminal(FINAL), "two corrections and final return")
            await observed(lambda: all(row.get("status") == "idle" for row in roots.values()),
                           "both roots idle")
            assert {role: identity(row) for role, row in roots.items()} == before
            assert {row["id"] for row in app.state["sessions"]} == original_ids

            # A separate intentional human input reuses these same roots. It is
            # not a manual completion message for any prior request.
            await app.dispatch("conversation.send", {
                "sessionId": roots["author"]["id"], "text": "stop-probe",
            }, command_id="author-stop-probe", include_state=False)
            await observed(lambda: (root / "late-result-staged").exists()
                           and terminal("AUTHOR_WAITING_FOR_LATE_RESULT")
                           and roots["author"].get("status") == "idle", "late provider barrier")
            await app.dispatch("conversation.stop", {"sessionId": roots["author"]["id"]},
                               command_id="human-stop-author", include_state=False)
            (root / "release-late-result").touch()
            await observed(lambda: terminal("REVIEWER_LATE_RESULT"), "late native terminal")
            await observed(lambda: app.collaboration.receipt(STOP_REQUEST)["subscription"]["status"]
                           == "suppressed", "Stop suppression")

            # Only passive reads below. No drain/enqueue/subscription or synthesized
            # completion can make these checks pass.
            receipts = [json.loads(encoded) for (encoded,) in app.db.execute(
                "SELECT receipt FROM commands WHERE "
                "json_extract(receipt,'$.commandAction')='coordination.send'"
            )]
            notifications = [row for row in receipts if row.get("dependencyRequestId")]
            assert Counter((row["dependencyRequestId"], row["dependencyOutcomeRevision"])
                           for row in notifications) == Counter({
                (REVIEW, 1): 1, (REVIEW, 2): 1, (REVIEW, 3): 1,
                (CORRECTIONS[0], 1): 1, (CORRECTIONS[1], 1): 1,
            })
            assert len(receipts) == 9  # Four explicit requests, five automatic returns.
            for row in receipts:
                assert row["delivery"] == "accepted" and row["admission"]["accepted"], row
                assert row["target"]["sessionId"] in original_ids
                assert row["senderSessionId"] in original_ids
                assert row["senderLocation"] != row["recipientLocation"]
            for row in notifications:
                assert row["origin"] == "system"
                assert row["dependencySatisfied"] is (
                    row["dependencyRequestId"] != REVIEW or row["dependencyOutcomeRevision"] == 3)
                assert row["target"]["sessionId"] == (
                    roots["author"]["id"] if row["dependencyRequestId"] == REVIEW
                    else roots["reviewer"]["id"])
            expected = {(row["target"]["sessionId"], row["inputId"]) for row in receipts}
            delivered = Counter((p["sessionId"], p["inputId"]) for kind, p in events
                                if kind == "runtime.status" and p.get("event") == "input.delivered")
            assert delivered == Counter({
                **dict.fromkeys(expected, 1),
                (roots["reviewer"]["id"], "reviewer-seed"): 1,
                (roots["author"]["id"], "author-seed"): 1,
                (roots["author"]["id"], "author-start"): 1,
                (roots["author"]["id"], "author-stop-probe"): 1,
            }), delivered
            finished = Counter((p["sessionId"], input_id) for kind, p in events
                               if kind == "runtime.generation" and p.get("event") == "generation.finished"
                               for input_id in p.get("input_ids", []))
            assert finished == delivered
            generations = {(p["sessionId"], input_id): p for kind, p in events
                           if kind == "runtime.generation" and p.get("event") == "generation.finished"
                           for input_id in p.get("input_ids", [])}
            for completed in generations.values():
                assert completed["disposition"] == "manager_turn_finished"
                assert not completed.get("active_job_ids")
                assert completed["nativeTerminal"]["messageId"]
            for row in receipts:
                if row.get("origin") == "agent":
                    source_input, = row["sourceInputIds"]
                    assert row["sourceGenerationId"] == generations[
                        (row["senderSessionId"], source_input)]["generation_id"]

            result = (await app.dispatch("coordination.result", {"requestId": REVIEW},
                                        include_state=False))["result"]
            revisions = {row["responseRevision"]: row for row in result["revisions"]}
            assert sorted(revisions) == [1, 2, 3] and result["qualified"]
            assert len({row["terminalMessageId"] for row in revisions.values()}) == 3
            assert len({row["generationId"] for row in revisions.values()}) == 3
            rows = audit(root)
            old_artifacts, old_corrections = {}, {}
            for row in rows:
                if row["kind"] == "sealed-report":
                    assert row["report"] == revisions[row["report"]["responseRevision"]]
                elif row["kind"] == "artifact":
                    key = (row["requestId"], row["revision"], row["name"])
                    assert old_artifacts.setdefault(key, row["artifact"]) == row["artifact"]
                elif row["kind"] == "result" and row["requestId"] in CORRECTIONS:
                    report = row["result"]["receipt"]["response"]
                    assert old_corrections.setdefault(row["requestId"], report) == report
                elif row["kind"] == "input" and row["envelope"]:
                    assert roots["author"]["workspace"] not in json.dumps(row["envelope"])
            assert len(old_artifacts) == 5 and set(old_corrections) == set(CORRECTIONS)
            assert len({row["outputId"] for row in old_artifacts.values()}) == 5
            notification_by_request = {
                row["dependencyRequestId"]: row for row in notifications
                if row["dependencyRequestId"] in CORRECTIONS
            }
            request_by_id = {row["requestId"]: row for row in receipts}
            assert request_by_id[REVIEW]["sourceInputIds"] == ["author-start"]
            assert request_by_id[CORRECTIONS[0]]["sourceInputIds"] == [REVIEW]
            assert request_by_id[CORRECTIONS[1]]["sourceInputIds"] == [
                notification_by_request[CORRECTIONS[0]]["inputId"]]
            for revision, report in revisions.items():
                assert report["requestId"] == REVIEW and report["status"] == "sealed"
                assert report["recipientSessionId"] == roots["reviewer"]["id"]
                assert report["references"] == [reference(f"report-{revision}")]
                assert report["admittedInputIds"] == [
                    REVIEW if revision == 1 else
                    notification_by_request[CORRECTIONS[revision - 2]]["inputId"]]
                # Verify the exact native row, not just a host display message.
                reader = {**roots["reviewer"], "nativeProject": project(roots["reviewer"])}
                messages = SessionHistoryStore(directory(reader)).load_messages()
                anchor = report["nativeTerminal"]
                native = messages[anchor["nativeIndex"]]
                assert native["role"] == "assistant"
                content = native["content"]
                text = content if isinstance(content, str) else "".join(
                    block.get("text", "") for block in content if block.get("type") == "text")
                assert text == anchor["nativeText"]
            for role, session in roots.items():
                reader = {**session, "nativeProject": project(session)}
                messages = SessionHistoryStore(directory(reader)).load_messages()
                user_rows = [row for row in messages if row.get("role") == "user"]
                # Foundation retains the host's pre-user system reminder with
                # user role. It is not another admitted input. Refuse all other
                # unprovenanced rows rather than silently dropping them.
                for row in user_rows:
                    metadata = row.get("metadata") or {}
                    if not metadata.get("amplifier_input", {}).get("id"):
                        assert metadata.get("ephemeral") is True, row
                        assert metadata.get("reminder_placement") == "pre_user", row
                native_inputs = Counter(
                    (row.get("metadata") or {}).get("amplifier_input", {}).get("id")
                    for row in user_rows
                    if (row.get("metadata") or {}).get("amplifier_input", {}).get("id"))
                assert native_inputs == Counter({i: n for (sid, i), n in delivered.items()
                                                 if sid == session["id"]}), native_inputs
                assert session["workspace"] == before[role]["workspace"]
                assert not session.get("parentId") and not session.get("collaboration")
            assert {row["id"] for row in app.state["sessions"]} == original_ids
            assert not any(row["path"] == roots["author"]["workspace"] for row in app.state["workspaces"])
            assert not app.db.execute("SELECT 1 FROM commands WHERE "
                                     "json_extract(receipt,'$.commandAction')='coordination.subscribe'").fetchone()
            tools = [row for row in rows if row["kind"] == "tool"]
            assert {row["action"] for row in tools} == {
                "coordination.send", "coordination.reply", "coordination.result",
                "coordination.artifact", "coordination.read"}
            assert [row["commandId"] for row in tools if row["action"] == "coordination.send"] == [
                REVIEW, STOP_REQUEST, *CORRECTIONS]  # Audit groups author then reviewer.
            late = (await app.dispatch("coordination.result", {"requestId": STOP_REQUEST},
                                      include_state=False))["result"]
            assert late["qualified"] and late["receipt"]["response"]["status"] == "sealed"
            assert late["returnState"]["subscription"]["status"] == "suppressed"
            assert late["returnState"]["notification"]["receiptPresent"] is False
            pids = {row["pid"] for row in rows}
            assert pids == set(worker_pids.values()) and os.getpid() not in pids
            assert all(row["pid"] == worker_pids[row["role"]] for row in rows)
            evidence = {
                "status": "passed", "workerProcesses": len(pids), "correctionRounds": 2,
                "originalResponseRevisions": sorted(revisions), "automaticReturns": len(notifications),
                "lateReturnSuppressed": True, "provider": "scripted-offline", "streaming": False,
                "runtime": installed, "roots": before,
                "limits": ["No real model/network or browser", "No restart/compaction",
                           "Fixture-produced files, not a filesystem tool", "Stop only; no task-change matrix"],
            }
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print(json.dumps(evidence))
    finally:
        (root / "observed-events.json").write_text(json.dumps(events, indent=2) + "\n")
        await app.close()


async def main():
    task = asyncio.current_task()
    if os.name != "nt":
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    await run(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    asyncio.run(main())
