"""Host-private native job admission witnesses, independent of display turns.

Missing/recovered/foreign service evidence never acquires an origin. The only
supported descendant is a native loop-live job result created by _job_returned.
"""
from copy import deepcopy

MAX_INPUTS = 256


def validate(proof, inputs):
    """Resolve the complete witnessed graph to exactly one initiating input."""
    if (not isinstance(proof, dict) or proof.get("version") != 1
            or not isinstance(inputs, list) or not inputs
            or any(not isinstance(value, str) or not value for value in inputs)
            or len(set(inputs)) != len(inputs)):
        return False
    rows = proof.get("inputs")
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_INPUTS:
        return False
    graph = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("inputId"), str) or not row["inputId"]:
            return False
        if row["inputId"] in graph:
            return False
        graph[row["inputId"]] = row
    visited, visiting, roots = set(), set(), set()
    def visit(identity):
        if identity in visiting or identity not in graph:
            return False
        if identity in visited:
            return True
        row = graph[identity]
        visiting.add(identity)
        if row.get("kind") == "user" and set(row) == {"inputId", "kind"}:
            roots.add(identity)
        elif (row.get("kind") == "service" and row.get("source") == "amplifier-delegate"
              and all(isinstance(row.get(key), str) and row[key]
                      for key in ("callId", "jobId", "originGenerationId"))
              and isinstance(row.get("parentInputIds"), list) and row["parentInputIds"]
              and all(isinstance(value, str) and value for value in row["parentInputIds"])
              and len(set(row["parentInputIds"])) == len(row["parentInputIds"])):
            if not all(visit(parent) for parent in row["parentInputIds"]):
                return False
        else:
            return False
        visiting.remove(identity)
        visited.add(identity)
        return True
    return (all(isinstance(identity, str) and visit(identity) for identity in inputs)
            and visited == set(graph) and roots == {proof.get("initiatingInputId")})


class TaskOrigin:
    def __init__(self):
        self.inputs = {}
        self.jobs = {}

    def remember(self, identity, row):
        # Retain ambiguity, never silently replace an earlier binding.
        if identity in self.inputs and self.inputs[identity] != row:
            self.inputs[identity] = None
        elif len(self.inputs) < 2000:
            self.inputs[identity] = row

    def observe(self, event, runtime, loop):
        if event.get("type") == "input.delivered":
            identity = event.get("input_id")
            command = runtime.accepted.get(identity)
            if command and command.kind in {"user", "steer"} and command.source == "user":
                self.remember(identity, {"inputId": identity, "kind": "user"})
        elif event.get("type") == "job.queued" and loop:
            job_id = event.get("job_id")
            job = loop.jobs.get(job_id)
            generation = runtime.generation or {}
            parents = generation.get("input_ids")
            if (job and len(self.jobs) < 2000
                    and job.get("call_id") == event.get("call_id") and generation.get("id")
                    and isinstance(parents, list) and parents):
                binding = {"callId": job["call_id"], "jobId": job_id,
                           "originGenerationId": generation["id"], "parentInputIds": list(parents)}
                self.jobs[job_id] = (job, binding) if job_id not in self.jobs else None

    def install(self, loop):
        if not callable(getattr(loop, "_job_returned", None)) or getattr(loop, "_unified_task_origin", None):
            return
        loop._unified_task_origin = self
        returned = loop._job_returned
        async def witnessed(job_id, *args, **kwargs):
            before = {command.id for command in loop.pending}
            retained = self.jobs.get(job_id)
            result = await returned(job_id, *args, **kwargs)
            # No text parsing: bind the actual new native Input to the exact
            # retained job object and its admission, not the manager's new turn.
            fresh = [command for command in loop.pending if command.id not in before]
            if (kwargs.get("deliver") is True and retained
                    and loop.jobs.get(job_id) is retained[0] and len(fresh) == 1
                    and fresh[0].kind == "service" and fresh[0].source == "amplifier-delegate"):
                command = fresh[0]
                self.remember(command.id, {"inputId": command.id, "kind": "service",
                                         "source": command.source, **deepcopy(retained[1])})
            return result
        loop._job_returned = witnessed

    def completed(self, event, runtime, loop):
        if (event.get("type") != "generation.finished" or event.get("active_job_ids") != []
                or not loop or loop.pending or not runtime.inbox.empty()):
            return None
        inputs = event.get("input_ids")
        if not isinstance(inputs, list) or not inputs:
            return None
        selected = {}
        def collect(identity):
            if identity in selected:
                return True
            row = self.inputs.get(identity)
            if not row or len(selected) >= MAX_INPUTS:
                return False
            selected[identity] = deepcopy(row)
            return all(collect(parent) for parent in row.get("parentInputIds", []))
        if not all(collect(identity) for identity in inputs):
            return None
        roots = [row["inputId"] for row in selected.values() if row["kind"] == "user"]
        if len(roots) != 1:
            return None
        proof = {"version": 1, "initiatingInputId": roots[0], "inputs": list(selected.values())}
        return proof if validate(proof, inputs) else None