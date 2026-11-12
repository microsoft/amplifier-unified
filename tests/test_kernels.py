"""Host action/approval, question and restart boundaries for persistent cells."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError, AppService


@pytest.fixture
async def host(tmp_path):
    pytest.importorskip("amplifier_module_tool_bash")
    from fixtures.computation_runtime import Runtime

    app = AppService(tmp_path, workspace=tmp_path)
    await app.dispatch("session.create", {})
    app.runtime = Runtime(app, tmp_path)
    yield app
    await app.close()


async def host_done(app, sid, identity):
    value = (await app.dispatch("operations.read", {"sessionId": sid, "id": identity}))[
        "result"
    ]
    async with asyncio.timeout(15):
        while value["state"] in {"running", "queued", "cancel_requested"}:
            value = (
                await app.dispatch(
                    "operations.wait",
                    {
                        "sessionId": sid,
                        "id": identity,
                        "afterRevision": value["revision"],
                        "waitMs": 1000,
                    },
                )
            )["result"]
    return value


async def test_host_agent_and_ui_share_code_approval_state_and_journal(host):
    sid = host._session()["id"]
    await host.dispatch("view.update", {"patch": {"draft": "Keep unsent text"}})
    created = await host.app_bridge(
        "dispatch", {"action": "kernels.create", "args": {"language": "python"}}, sid
    )
    kernel = created["result"]
    code = "value = 41; print('preserved'); value + 1"
    result = await host.app_bridge(
        "dispatch",
        {
            "action": "kernels.execute",
            "args": {
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": code,
            },
        },
        sid,
    )
    record = await host_done(host, sid, result["result"]["id"])
    assert record["evidence"]["result"] == "42"
    assert record["metadata"]["code"] == code
    pre = [
        row
        for row in host.runtime.calls
        if row["tool_name"] == "compute" and row["tool_input"]["action"] == "execute"
    ][-1]
    assert pre["tool_input"]["code"] == code
    assert pre["tool_input"]["generation"] == kernel["generation"]
    assert pre["actor"] == "agent"
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "process"
    ]
    assert host.state["selectedSessionId"] == sid
    assert host.state["view"]["draft"] == "Keep unsent text"
    with pytest.raises(AppError, match="calling conversation"):
        await host.app_bridge(
            "dispatch",
            {
                "action": "kernels.status",
                "args": {"sessionId": "foreign", "kernelId": kernel["id"]},
            },
            sid,
        )


async def test_exact_cell_and_generation_cannot_be_rewritten_after_approval(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]

    async def rewrite(data):
        if data["tool_name"] == "compute":
            data["tool_input"]["code"] = "unexpected_side_effect = True"
        return SimpleNamespace(action="continue")

    host.runtime.hook = rewrite
    with pytest.raises(AppError, match="cannot redirect"):
        await host.dispatch(
            "kernels.execute",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": "1 + 1",
            },
        )
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "kernel-cell"
    ]
    host.runtime.hook = None


async def test_required_question_blocks_only_exact_dependent_cell(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    answered = False

    def guard(session_id, question_id):
        assert session_id == sid and question_id == "q1"
        if not answered:
            raise AppError("Question unanswered", 409, code="question_unanswered")
        return {"value": "yes"}

    host.questions.answer_for_dependency = guard
    args = {
        "sessionId": sid,
        "kernelId": kernel["id"],
        "generation": kernel["generation"],
        "code": "1 + 1",
    }
    with pytest.raises(AppError, match="unanswered"):
        await host.dispatch("kernels.execute", {**args, "questionIds": ["q1"]})
    independent = (await host.dispatch("kernels.execute", args))["result"]
    assert (await host_done(host, sid, independent["id"]))["state"] == "completed"
    answered = True
    dependent = (
        await host.dispatch("kernels.execute", {**args, "questionIds": ["q1"]})
    )["result"]
    assert (await host_done(host, sid, dependent["id"]))["state"] == "completed"


async def test_saved_kernel_restart_reads_are_passive_and_never_control_unknown(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    host.operations.journal.recover()
    count = len(host.runtime.calls)
    value = (
        await host.dispatch(
            "kernels.status", {"sessionId": sid, "kernelId": kernel["id"]}
        )
    )["result"]
    assert value["state"] == "unavailable"
    with pytest.raises(AppError, match="original kernel is unavailable"):
        await host.dispatch(
            "kernels.reset",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
            },
        )
    assert len(host.runtime.calls) == count


async def test_completed_cell_cancel_cannot_interrupt_a_new_cell(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    args = {
        "sessionId": sid,
        "kernelId": kernel["id"],
        "generation": kernel["generation"],
    }
    first = (await host.dispatch("kernels.execute", {**args, "code": "1+1"}))["result"]
    await host_done(host, sid, first["id"])
    second = (
        await host.dispatch(
            "kernels.execute", {**args, "code": "import time; time.sleep(30)"}
        )
    )["result"]
    await host.dispatch("operations.cancel", {"sessionId": sid, "id": first["id"]})
    assert host.operations.status(sid, second["id"])["state"] == "running"
    with pytest.raises(AppError, match="no longer active"):
        await host.dispatch("kernels.interrupt", {**args, "cellId": first["id"]})
    await host.dispatch("operations.cancel", {"sessionId": sid, "id": second["id"]})
    assert (await host_done(host, sid, second["id"]))["state"] == "cancelled"


async def test_question_dependency_rechecked_after_code_approval(host):
    sid = host._session()["id"]
    kernel = (
        await host.dispatch("kernels.create", {"sessionId": sid, "language": "python"})
    )["result"]
    answered = True

    def guard(*_):
        if not answered:
            raise AppError("Question was superseded", 409)
        return {"value": "yes"}

    host.questions.answer_for_dependency = guard

    async def approval(data):
        nonlocal answered
        if data["tool_name"] == "compute":
            answered = False
        return SimpleNamespace(action="continue")

    host.runtime.hook = approval
    with pytest.raises(AppError, match="superseded"):
        await host.dispatch(
            "kernels.execute",
            {
                "sessionId": sid,
                "kernelId": kernel["id"],
                "generation": kernel["generation"],
                "code": "1+1",
                "questionIds": ["q1"],
            },
        )
    assert not [
        row for row in host.operations.journal.list(sid) if row["kind"] == "kernel-cell"
    ]
    assert host.runtime.controls.kernels.get(kernel["id"])["state"] == "idle"
    host.runtime.hook = None


async def test_only_explicit_create_prepares_owner_never_passive_reads_or_old_cells(host):
    ensure = AsyncMock()
    host.management = SimpleNamespace(ensure_runtime=ensure, setup_manager=None, global_setup_manager=None, workspace_setup_managers={}, provider_catalog=SimpleNamespace(close=AsyncMock()))
    sid = host._session()['id']
    kernel = (await host.dispatch('kernels.create', {'sessionId': sid, 'language': 'python'}))['result']
    ensure.assert_awaited_once()
    assert ensure.await_args.args[0]['id'] == sid
    await host.dispatch('kernels.list', {'sessionId': sid})
    await host.dispatch('kernels.status', {'sessionId': sid, 'kernelId': kernel['id']})
    cell = (await host.dispatch('kernels.execute', {'sessionId': sid, 'kernelId': kernel['id'],
        'generation': kernel['generation'], 'code': '42'}))['result']
    await host_done(host, sid, cell['id'])
    ensure.assert_awaited_once()
    host.operations.journal.recover()
    with pytest.raises(AppError, match='original kernel is unavailable'):
        await host.dispatch('kernels.execute', {'sessionId': sid, 'kernelId': kernel['id'],
            'generation': kernel['generation'], 'code': '42'})
    ensure.assert_awaited_once()
