"""Session-owned kernels; execution and filesystem authority stay in transport.

transport(action, arguments) must authorize the exact call and return an owned
process result. observe(event) durably records cell/kernel evidence. This library
never launches subprocesses or evaluates submitted code in its own interpreter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import time
import uuid
from pathlib import Path


class Kernels:
    def __init__(
        self, transport, observe, runtimes, *, max_kernels=4, max_output_bytes=1_000_000
    ):
        self.transport, self.observe = transport, observe
        self.runtimes = dict(runtimes)
        self.owner = str(uuid.uuid4())
        self.records = {}
        self.max_kernels, self.max_output_bytes = max_kernels, max_output_bytes
        self.admission = asyncio.Lock()
        self.closed = False
        self.final_tasks = set()

    def get(self, identity, generation=None):
        record = self.records.get(identity)
        if record is None:
            raise ValueError(
                "Kernel is not owned by this mounted session; nothing was replayed"
            )
        if generation is not None and generation != record["generation"]:
            raise ValueError(
                "Kernel generation changed; review current state before submitting code"
            )
        return record

    @staticmethod
    def snapshot(record):
        return {
            key: record.get(key)
            for key in (
                "id",
                "language",
                "generation",
                "state",
                "processId",
                "runtime",
                "cellId",
                "reason",
                "processReturncode",
            )
        }

    async def emit(self, record, phase, *, cell=None, **data):
        async with record["observation_lock"]:
            target = cell or record
            target["sequence"] += 1
            event = {
                "schemaVersion": 1,
                "eventId": f"{target['id']}:{target['sequence']}",
                "sequence": target["sequence"],
                "operationId": target["id"],
                "ownerId": self.owner,
                "source": "computation",
                "kind": "kernel-cell" if cell else "kernel",
                "phase": phase,
                "at": time.time(),
                **data,
            }
            await asyncio.wait_for(self.observe(event), 2)

    async def kernel_state(self, record, phase="state", state="running"):
        await self.emit(
            record,
            phase,
            metadata=self.snapshot(record),
            status={
                "state": state,
                "output_complete": state not in {"running", "outcome_unknown"},
                "total_output_bytes": 0,
                "returncode": record.get("processReturncode"),
            },
        )

    async def create(self, language):
        async with self.admission:
            if self.closed or len(self.records) >= self.max_kernels:
                raise ValueError(
                    "Kernel capacity reached or session closed; close an existing kernel first"
                )
            if language not in self.runtimes:
                raise ValueError("Requested runtime is unavailable on this host")
            executable = Path(self.runtimes[language])
            if not executable.is_absolute() or not executable.is_file():
                raise ValueError("Runtime must be a trusted absolute executable path")
            record = {
                "id": str(uuid.uuid4()),
                "language": language,
                "generation": 0,
                "state": "starting",
                "sequence": 0,
                "cell": None,
                "runtime": None,
                "processId": None,
                "cellId": None,
                "lock": asyncio.Lock(),
                "reader": None,
                "observation_lock": asyncio.Lock(),
            }
            self.records[record["id"]] = record
            try:
                await self.kernel_state(record, "started")
            except BaseException:
                self.records.pop(record["id"], None)
                raise
        try:
            policy = await self.transport("list", {})
        except BaseException:
            record.update(
                state="unavailable",
                reason="Runtime admission was denied; no process was started",
            )
            await self.kernel_state(record, "finished", "failed")
            self.records.pop(record["id"], None)
            raise
        if not policy.get("stdin_allowed"):
            record.update(
                state="unavailable",
                reason="Persistent code requires trusted unrestricted stdin authorization",
            )
            await self.kernel_state(record, "finished", "failed")
            self.records.pop(record["id"], None)
            raise ValueError(record["reason"])
        await self.launch(record)
        return self.snapshot(record)

    async def launch(self, record):
        language = record["language"]
        runner = Path(__file__).with_name(
            "python_runner.py" if language == "python" else "node_runner.cjs"
        )
        command = shlex.join([self.runtimes[language], str(runner)])
        record.update(
            state="starting",
            generation=record["generation"] + 1,
            ready=asyncio.Event(),
            buffer="",
            cursor=0,
            reason=None,
            runtime=None,
            processReturncode=None,
            processId=None,
            cell=None,
            cellId=None,
        )
        try:
            result = await self.transport(
                "start", {"command": command, "timeout": 3600}
            )
            record["processId"] = result["process_id"]
            record["reader"] = asyncio.create_task(
                self.pump(record, record["generation"])
            )
            await asyncio.wait_for(record["ready"].wait(), 15)
            if record["state"] != "idle":
                raise ValueError(
                    record.get("reason") or "Kernel failed its startup handshake"
                )
            await self.kernel_state(record)
        except BaseException:
            record.update(
                state="unavailable",
                reason="Kernel startup did not complete; no cell was replayed",
            )
            confirmed = record.get("processId") is None
            if record.get("processId"):
                try:
                    await self.stop_process(record)
                    confirmed = True
                except Exception:  # noqa: BLE001 - Failed cleanup must keep startup failure explicit.
                    record["reason"] += "; cleanup not confirmed"
            await self.kernel_state(
                record, "finished", "failed" if confirmed else "outcome_unknown"
            )
            if confirmed:
                self.records.pop(record["id"], None)
            raise

    async def execute(self, identity, generation, code, timeout=30, question_ids=()):
        record = self.get(identity, generation)
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 16000:
            raise ValueError("A cell must contain 1–16000 UTF-8 bytes of code")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 300
        ):
            raise ValueError("Cell timeout must be 1–300 seconds")
        async with record["lock"]:
            self.get(identity, generation)
            if record["state"] != "idle":
                raise ValueError(
                    "Kernel is busy or unavailable; cells are never implicitly queued or replayed"
                )
            cell = {
                "id": str(uuid.uuid4()),
                "sequence": 0,
                "cursor": 0,
                "bytes": 0,
                "captured": 0,
                "complete": True,
                "done": asyncio.Event(),
                "watchdog": None,
            }
            record.update(state="running", cell=cell, cellId=cell["id"])
            process_id = record["processId"]
            # Durable admission precedes input delivery. Its receipt never proves
            # external side effects finished; failed delivery remains unknown.
            try:
                await self.emit(
                    record,
                    "started",
                    cell=cell,
                    metadata={
                        "kernelId": identity,
                        "generation": generation,
                        "language": record["language"],
                        "runtime": record["runtime"],
                        "processId": record["processId"],
                        "code": code,
                        "codeSha256": hashlib.sha256(code.encode()).hexdigest(),
                        "questionIds": list(question_ids),
                    },
                    status={"state": "running"},
                )
                await self.kernel_state(record)
            except BaseException:
                # No interpreter input has been submitted at this point.
                record.update(state="idle", cell=None, cellId=None)
                raise

        try:
            await self.transport(
                "write",
                {
                    "process_id": process_id,
                    "stdin": json.dumps(
                        {"cellId": cell["id"], "code": code}, ensure_ascii=False
                    )
                    + "\n",
                },
            )
        except BaseException:
            await self.finish(
                record,
                cell,
                "outcome_unknown",
                error="Cell input delivery was not confirmed; never automatically retry it",
            )
            record.update(
                state="unavailable",
                reason="Input delivery uncertain; reset is required",
            )
            await self.kernel_state(record)
            raise
        cell["watchdog"] = asyncio.create_task(self.deadline(record, cell, timeout))
        return {
            "id": cell["id"],
            "kernelId": identity,
            "generation": generation,
            "outputReference": {"operationId": cell["id"], "cursor": 0},
        }

    async def deadline(self, record, cell, timeout):
        try:
            await asyncio.wait_for(cell["done"].wait(), timeout)
        except TimeoutError:
            if cell["done"].is_set():
                return
            try:
                await self.interrupt(
                    record["id"],
                    record["generation"],
                    reason="Cell deadline reached",
                    cell_id=cell["id"],
                )
            except Exception:  # noqa: BLE001 - Transport failures become explicit unknown outcomes.
                # Denied/unconfirmed stop is not cancellation evidence. Keep
                # observing the actual child; its owned process deadline remains.
                record["reason"] = (
                    "Cell deadline reached but interruption was not confirmed"
                )
                await self.kernel_state(record)

    async def finish(
        self,
        record,
        cell,
        state,
        *,
        result=None,
        error=None,
        truncated=False,
        returncode=None,
    ):
        if cell.get("final_task") is None:
            cell["final_task"] = asyncio.create_task(
                self._finish(
                    record,
                    cell,
                    state,
                    result=result,
                    error=error,
                    truncated=truncated,
                    returncode=returncode,
                )
            )
            self.final_tasks.add(cell["final_task"])

            def settled(task):
                self.final_tasks.discard(task)
                if not task.cancelled():
                    task.exception()

            cell["final_task"].add_done_callback(settled)
        # Reader cancellation must not interrupt a durable final receipt halfway.
        return await asyncio.shield(cell["final_task"])

    async def _finish(
        self, record, cell, state, *, result, error, truncated, returncode
    ):
        cell["done"].set()
        task = cell.get("watchdog")
        if task and task is not asyncio.current_task():
            task.cancel()
        if record.get("cell") is cell:
            record["cell"] = None
            if record["state"] == "running":
                record["state"] = "idle"
            await self.kernel_state(record)
        await self.emit(
            record,
            "finished",
            cell=cell,
            status={
                "state": state,
                "output_complete": cell["complete"] and state != "outcome_unknown",
                "total_output_bytes": cell["bytes"],
                "result": result,
                "error": error,
                "resultTruncated": truncated,
                "returncode": returncode,
                "capture_complete": cell["complete"],
            },
        )

    async def packet(self, record, packet):
        if packet.get("type") == "ready" and record["state"] == "starting":
            runtime = packet.get("runtime", {})
            if runtime.get("language") != record["language"]:
                raise ValueError("Runtime handshake language mismatch")
            record.update(runtime=runtime, state="idle")
            record["ready"].set()
            return
        cell = record.get("cell")
        if not cell or packet.get("cellId") != cell["id"]:
            raise ValueError("Output arrived outside its exact admitted cell")
        if packet.get("type") == "output":
            text = packet.get("text")
            stream = packet.get("stream")
            if not isinstance(text, str) or stream not in {"stdout", "stderr"}:
                raise ValueError("Malformed kernel output")
            raw = text.encode("utf-8", "replace")
            cell["bytes"] += len(raw)
            if cell["captured"] + len(raw) > self.max_output_bytes:
                cell["complete"] = False
                return
            cell["captured"] += len(raw)
            # Bounded chunks use the same portable output contract as processes.
            for start in range(0, len(text), 1000):
                part = text[start : start + 1000]
                await self.emit(
                    record,
                    "output",
                    cell=cell,
                    chunk={
                        "cursor": cell["cursor"],
                        "next_cursor": cell["cursor"] + 1,
                        "stream": stream,
                        "text": part,
                        "source_bytes": len(part.encode("utf-8", "replace")),
                        "encoding_loss": bool(packet.get("encoding_loss")),
                        "binary_output_withheld": False,
                    },
                )
                cell["cursor"] += 1
        elif packet.get("type") == "done":
            if not isinstance(packet.get("success"), bool):
                raise ValueError("Malformed cell result")
            await self.finish(
                record,
                cell,
                "completed" if packet["success"] else "failed",
                result=packet.get("result"),
                error=packet.get("error"),
                truncated=packet.get("resultTruncated", False),
            )
        else:
            raise ValueError("Unknown kernel protocol message")

    async def pump(self, record, generation):
        try:
            while generation == record["generation"]:
                page = await self.transport(
                    "wait",
                    {
                        "process_id": record["processId"],
                        "cursor": record["cursor"],
                        "wait_ms": 60000,
                        "max_bytes": 100000,
                    },
                )
                if (
                    page.get("cursor_expired")
                    or page.get("output_truncated")
                    and page.get("earliest_cursor", 0) > record["cursor"]
                ):
                    raise ValueError(
                        "Kernel protocol output expired before it was observed"
                    )
                for chunk in page.get("chunks", []):
                    if (
                        chunk.get("stream") != "stdout"
                        or chunk.get("encoding_loss")
                        or chunk.get("binary_output_withheld")
                    ):
                        raise ValueError("Kernel protocol was bypassed or damaged")
                    record["buffer"] += chunk["text"]
                    if len(record["buffer"].encode("utf-8", "replace")) > 128000:
                        raise ValueError("Kernel protocol message exceeded its bound")
                    while "\n" in record["buffer"]:
                        line, record["buffer"] = record["buffer"].split("\n", 1)
                        if line:
                            await self.packet(record, json.loads(line))
                record["cursor"] = page["next_cursor"]
                if page["state"] not in {"running", "cancel_requested"}:
                    record.update(
                        state="unavailable",
                        reason="Owned process ended; reset is required",
                    )
                    cell = record.get("cell")
                    if cell:
                        await self.finish(
                            record,
                            cell,
                            "cancelled"
                            if page["state"] == "cancelled"
                            else "outcome_unknown",
                            error=record["reason"],
                            returncode=page.get("returncode"),
                        )
                    await self.kernel_state(record)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - Transport failures become explicit unknown outcomes.
            record.update(
                state="unavailable",
                reason="Kernel observation failed; outcome is unknown and reset is required",
            )
            cell = record.get("cell")
            if cell:
                cell["complete"] = False
                await self.finish(
                    record, cell, "outcome_unknown", error=record["reason"]
                )
            await self.kernel_state(record)
        finally:
            record["ready"].set()

    async def stop_process(self, record):
        result = await self.transport("terminate", {"process_id": record["processId"]})
        if not isinstance(result.get("returncode"), int) or result.get("state") in {
            "running",
            "cancel_requested",
            "outcome_unknown",
        }:
            raise ValueError(
                "Original process termination is unknown; clean reset was not attempted"
            )
        record["processReturncode"] = result["returncode"]
        reader = record.get("reader")
        if reader and reader is not asyncio.current_task() and not reader.cancelled():
            try:
                # The process has drained, but its protocol reader may still be
                # committing final chunks. Never label a forced reader stop complete.
                await asyncio.wait_for(asyncio.shield(reader), 2)
            except TimeoutError:
                if record.get("cell"):
                    record["cell"]["complete"] = False
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
        return result

    def begin_control(self, identity, generation, state):
        record = self.get(identity, generation)
        if record["state"] in {"starting", "resetting", "closing", "interrupting"}:
            raise ValueError("A kernel lifecycle change is already in progress")
        record["state"] = state
        return record

    async def interrupt(
        self, identity, generation, *, reason="Interrupted by request", cell_id=None
    ):
        record = self.get(identity, generation)
        if cell_id is not None and (
            record.get("cell") is None or record["cell"]["id"] != cell_id
        ):
            raise ValueError(
                "The exact cell is no longer active; another cell was not interrupted"
            )
        record = self.begin_control(identity, generation, "interrupting")
        cell = record.get("cell")
        try:
            result = await self.stop_process(record)
            record.update(state="unavailable", reason=reason)
            if cell:
                await self.finish(
                    record,
                    cell,
                    "cancelled",
                    error=reason,
                    returncode=result["returncode"],
                )
            await self.kernel_state(record)
            return {**self.snapshot(record), "returncode": result["returncode"]}
        except BaseException:
            record.update(
                state="unavailable",
                reason="Interruption not confirmed; effects remain unknown",
            )
            await self.kernel_state(record)
            raise

    async def reset(self, identity, generation):
        record = self.begin_control(identity, generation, "resetting")
        cell = record.get("cell")
        try:
            await self.stop_process(record)
            if cell:
                await self.finish(
                    record,
                    cell,
                    "cancelled",
                    error="Reset after confirmed process termination",
                )
            await self.launch(record)
            return self.snapshot(record)
        except BaseException:
            record.update(
                state="unavailable",
                reason="Clean reset was not confirmed; no cells were replayed",
            )
            if identity in self.records:
                await self.kernel_state(record)
            raise

    async def close(self, identity, generation):
        record = self.begin_control(identity, generation, "closing")
        cell = record.get("cell")
        try:
            await self.stop_process(record)
            if cell:
                await self.finish(record, cell, "cancelled", error="Kernel closed")
            record["state"] = "closed"
            await self.kernel_state(record, "finished", "completed")
            self.records.pop(identity)
            return self.snapshot(record)
        except BaseException:
            record.update(
                state="unavailable",
                reason="Close not confirmed; process effects remain unknown",
            )
            await self.kernel_state(record)
            raise

    async def shutdown(self):
        self.closed = True
        for record in list(self.records.values()):
            try:
                await asyncio.wait_for(
                    self.close(record["id"], record["generation"]), 3
                )
            except Exception:  # noqa: BLE001 - Transport failures become explicit unknown outcomes.
                record.update(
                    state="unavailable", reason="Host shutdown; process outcome unknown"
                )
                try:
                    await self.kernel_state(record, "finished", "outcome_unknown")
                except Exception:  # noqa: BLE001 - Host restart recovery retains uncertainty if the sink is gone.
                    record["reason"] += "; final receipt was not confirmed"
        readers = [
            record["reader"] for record in self.records.values() if record.get("reader")
        ]
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, *self.final_tasks, return_exceptions=True)
