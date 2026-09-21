"""Opt-in live Work worker/process coordination against a fresh private host.

Uses the configured provider/model/effort unchanged, reviewed local module
sources, a synthetic Git repository and actual Chromium controls. No provider
request is mocked and no user conversation is opened or replayed.
"""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from acceptance import (
    Observation,
    assistant_text,
    finished,
    installed_revisions,
    private_json,
    setup,
)
from browser_acceptance import until, wait_js
from fixture_hygiene import redact_generated_credentials
from parity_acceptance import profile


def prepare_workspace(workspace):
    """Each real process leaves one append-only receipt; gates are test inputs."""
    script = """from pathlib import Path
import sys, time
root=Path(__file__).parent
name=sys.argv[1]
if name not in {'A','B','PROCESS'}: raise ValueError('Unknown synthetic job')
with (root/(name+'.runs')).open('a') as file: file.write('run\\n')
(root/(name+'.started')).touch()
print(name+'_STARTED',flush=True)
deadline=time.monotonic()+300
while not (root/(name+'.release')).exists():
    if time.monotonic()>deadline: raise TimeoutError('Synthetic gate expired')
    time.sleep(.1)
print('RESULT_'+name+'_VERIFIED',flush=True)
"""
    (workspace / "gated_job.py").write_text(script)
    (workspace / "followup.txt").write_text("FOLLOWUP-ALDER-9281\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Acceptance Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Synthetic coordination fixture",
        ],
        check=True,
    )


async def run(args):
    from aiohttp import web
    from playwright.async_api import async_playwright

    import amplifier_web.runtime_worker as worker
    from amplifier_web.server import create_app
    from amplifier_web.session_client import SessionClient

    folder, provider = setup(args)
    workspace = folder / "workspace"
    prepare_workspace(workspace)
    bundle = await profile(folder, args)
    # Explicitly keep native transport disabled for the existing Terra selection.
    import yaml

    contents = bundle.read_text().split("---", 2)[1]
    plan = yaml.safe_load(contents)
    plan["session"]["orchestrator"]["config"]["native_provider"] = False
    bundle.write_text("---\n" + yaml.safe_dump(plan, sort_keys=False) + "---\n")
    report = {
        "schema_version": 1,
        "provider_instance": args.provider,
        "model": provider["config"].get("default_model"),
        "effort": provider["config"].get("reasoning_effort"),
        "installed": installed_revisions(),
        "evidence": "real configured provider, composed Work include graph, real workers/process and Chromium",
        "checks": {},
        "passed": False,
        "native_transport": False,
        "audio": "not tested",
    }
    app = await create_app(
        folder / "app",
        workspace=workspace,
        voice=False,
        background_updates=False,
        preload_providers=False,
    )
    service = app["service"]
    service.runtime.command = [sys.executable, str(Path(worker.__file__).resolve())]
    observation, browser, waiter, page = Observation(), None, None, None
    original = service.on_runtime_event

    async def observed(kind, data):
        observation.add(kind, data)
        await original(kind, data)

    def public_answer(sid):
        # Native transcript adoption can retire the in-memory message projection.
        # Observe only public assistant messages, never raw thinking blocks.
        return (
            assistant_text(service, sid)
            + "\n"
            + "\n".join(
                event.get("text", "")
                for event in observation.events
                if event.get("kind") == "assistant.message"
                and event.get("sessionId") == sid
            )
        )

    service.on_runtime_event = observed
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    app["allowed_origins"] = app["allowed_origins"] | {url}
    try:
        async with SessionClient(
            url, app["control_token"], "coordination-fixture"
        ) as client:
            created = await client.create_session(
                {
                    "title": "Synthetic live coordination",
                    "bundle": str(bundle),
                    "workspace": str(workspace),
                },
                command_id="create",
            )
            sid = created["state"]["selectedSessionId"]
            report["session_id"] = sid
            await service.runtime.start(service._session(sid), service.on_runtime_event)
            config = await service.runtime.control(sid, "configuration.inspect")
            private_json(folder / "configuration.json", config)
            native = await service.runtime.control(sid, "native.status")
            report["checks"]["native_disabled"] = native.get("enabled") is False
            print(json.dumps({"phase": "runtime-ready"}), flush=True)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 950}
                )
                captured = []

                async def authorize(route):
                    if route.request.method == "POST" and route.request.url.endswith(
                        "/api/actions"
                    ):
                        body = route.request.post_data_json
                        if body and body.get("action") == "coordination.followup":
                            captured.append(body)
                    await route.continue_(
                        headers={
                            **route.request.headers,
                            "Authorization": "Bearer " + app["control_token"],
                        }
                    )

                await context.route(url + "/**", authorize)
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(url)
                await wait_js(page, "window.amplifier?.getState()?.client?.id")
                await page.evaluate(
                    "id=>window.amplifier.dispatch('session.select',{id})", sid
                )
                composer = page.get_by_role("textbox", name="Message Amplifier")
                await composer.fill("UNSENT-COORDINATION-DRAFT")
                executable = shlex.quote(sys.executable)
                job_script = shlex.quote(str(workspace / "gated_job.py"))
                prompt = (
                    "Bounded synthetic coordination acceptance. Do not create goals, schedules, extra workers or change providers. "
                    "First start exactly one managed process with bash action=start, command "
                    f'"{executable} {job_script} PROCESS", timeout=300. Keep its process_id; do not rerun it. '
                    "Then delegate exactly two independent tasks with agent=self, async=true, persistent=true. "
                    f"Worker A instruction: run `{executable} {job_script} A` with bash exactly once, timeout=300; "
                    "wait for actual output and report it briefly. Do not delegate or rerun it. Remain available for a follow-up. "
                    f"Worker B instruction: run `{executable} {job_script} B` with bash exactly once, timeout=300; "
                    "wait for actual output and report it briefly. Do not delegate or rerun it. Remain available for a follow-up. "
                    "Keep both persistent workers open until I explicitly ask to finish them. Initial delivery color BLUE. "
                    "Acknowledge the starts without blocking on waits; accept side questions while the jobs are gated. "
                    "The harness alone releases the gates. Do not create, edit or delete release files."
                )
                await client.command(
                    sid,
                    "conversation.send",
                    {"text": prompt, "preserveDraft": True},
                    command_id="start",
                )
                await until(
                    lambda: (
                        all(
                            (workspace / (name + ".started")).exists()
                            for name in ("A", "B", "PROCESS")
                        )
                        or any(e["kind"] == "runtime.error" for e in observation.events)
                    ),
                    150,
                )
                if not all(
                    (workspace / (name + ".started")).exists()
                    for name in ("A", "B", "PROCESS")
                ):
                    raise RuntimeError("Actual workers/process did not all start")
                print(
                    json.dumps({"phase": "two-workers-and-process-active"}), flush=True
                )
                workers = lambda: [
                    r
                    for r in service._session(sid).get("workers", [])
                    if r.get("kind") == "session" and r.get("persistent")
                ]
                await until(lambda: len(workers()) == 2, 20)
                report["checks"]["two_actual_persistent_workers"] = len(workers()) == 2
                correction = "Change delivery color to ORANGE. While both workers and the managed process remain active, what is 17 + 25? Answer now and continue the same task; keep both workers open."
                started = time.monotonic()
                await client.command(
                    sid,
                    "conversation.send",
                    {"text": correction, "preserveDraft": True},
                    command_id="correction",
                )
                await observation.wait(lambda: "42" in public_answer(sid), 110)
                report["checks"]["side_answer_while_all_pending"] = not any(
                    (workspace / (name + ".release")).exists()
                    for name in ("A", "B", "PROCESS")
                )
                report["side_answer_seconds"] = round(time.monotonic() - started, 3)
                targets = [{"sessionId": sid, "workerId": r["id"]} for r in workers()]
                initial = await client._json(
                    "POST",
                    "/api/actions",
                    {
                        "action": "coordination.wait",
                        "id": "snapshot",
                        "args": {"targets": targets, "waitMs": 0},
                    },
                )
                cursors = [
                    {**r["target"], "afterCursor": r["nextCursor"]}
                    for r in initial["result"]["targets"]
                ]
                await page.get_by_role(
                    "button", name="Session details", exact=True
                ).click()
                await page.get_by_role(
                    "button", name="Tasks and workers", exact=True
                ).click()
                # Target rows load asynchronously after opening this panel.
                await wait_js(
                    page,
                    'document.querySelectorAll(".a-coordination-target input[type=checkbox]").length >= 3',
                )
                # Watch both actual sessions plus their parent-owned job receipts.
                for checkbox in (
                    await page.locator(".a-coordination-target")
                    .get_by_role("checkbox")
                    .all()
                ):
                    await checkbox.check()
                waiter = asyncio.create_task(
                    client._json(
                        "POST",
                        "/api/actions",
                        {
                            "action": "coordination.wait",
                            "id": "wait-one",
                            "args": {"targets": cursors, "waitMs": 55000},
                        },
                    )
                )
                await asyncio.sleep(0.25)
                report["checks"][
                    "wait_stays_pending_without_change"
                ] = not waiter.done()
                print(json.dumps({"phase": "cursor-wait-armed"}), flush=True)
                (workspace / "A.release").touch()
                received = (await waiter)["result"]
                waiter = None
                private_json(folder / "first-wait.json", received)
                reports = [
                    (row, receipt)
                    for row in received["targets"]
                    for receipt in row["results"]
                ]
                report["checks"]["wait_woke_on_one_worker"] = (
                    not received["timedOut"]
                    and len(reports) == 1
                    and "RESULT_A_VERIFIED" in reports[0][1]["text"]
                )
                if not report["checks"]["wait_woke_on_one_worker"]:
                    raise RuntimeError(
                        "Wait did not return the first actual worker report exactly once"
                    )
                target_a = reports[0][0]["target"]
                first_report_id = reports[0][1]["id"]
                print(json.dumps({"phase": "first-worker-report-observed"}), flush=True)
                await page.locator(
                    '[data-report-id="' + first_report_id + '"]'
                ).wait_for(timeout=30000)
                card = page.locator(".a-coordination-target").filter(
                    has=page.locator('[data-report-id="' + first_report_id + '"]')
                )
                await card.get_by_text("Follow up", exact=True).click()
                await card.locator("textarea").fill(
                    f"Read {workspace / 'followup.txt'} once using a file tool, report the exact token, and remain available. Do not rerun gated_job.py or repeat any prior effect."
                )
                await card.get_by_role(
                    "button", name="Send follow-up", exact=True
                ).click()
                await page.get_by_text("Follow-up accepted.", exact=True).wait_for()
                await until(
                    lambda: any(
                        "FOLLOWUP-ALDER-9281" in r.get("text", "")
                        for w in workers()
                        for r in w.get("reportReceipts", [])
                    ),
                    90,
                )
                print(json.dumps({"phase": "ui-followup-completed"}), flush=True)
                report["checks"]["explicit_ui_followup_exact_target"] = (
                    len(captured) == 1
                    and captured[0]["args"]["workerId"] == target_a["workerId"]
                )
                report["checks"]["selected_task_preserved"] = await page.evaluate(
                    "id=>window.amplifier.getState().selectedSessionId===id", sid
                )
                report["checks"]["draft_preserved"] = (
                    await composer.input_value() == "UNSENT-COORDINATION-DRAFT"
                )
                advanced = [
                    {**r["target"], "afterCursor": r["nextCursor"]}
                    for r in received["targets"]
                ]
                after_followup = (
                    await client._json(
                        "POST",
                        "/api/actions",
                        {
                            "action": "coordination.wait",
                            "id": "followup-wait",
                            "args": {"targets": advanced, "waitMs": 0},
                        },
                    )
                )["result"]
                report["checks"]["consumed_report_not_redelivered"] = (
                    first_report_id
                    not in [
                        v["id"] for r in after_followup["targets"] for v in r["results"]
                    ]
                )
                advanced = [
                    {**r["target"], "afterCursor": r["nextCursor"]}
                    for r in after_followup["targets"]
                ]
                await page.screenshot(
                    path=str(folder / "coordination-active.png"), full_page=True
                )
                await context.set_offline(True)
                (workspace / "B.release").touch()
                (workspace / "PROCESS.release").touch()
                await until(
                    lambda: any(
                        "RESULT_B_VERIFIED" in r.get("text", "")
                        for w in workers()
                        for r in w.get("reportReceipts", [])
                    ),
                    90,
                )
                await context.set_offline(False)
                await page.reload()
                await wait_js(page, "window.amplifier?.getState()?.client?.id")
                async with SessionClient(
                    url, app["control_token"], "coordination-reconnected"
                ) as reconnected:
                    next_page = (
                        await reconnected._json(
                            "POST",
                            "/api/actions",
                            {
                                "action": "coordination.wait",
                                "id": "reconnect-wait",
                                "args": {"targets": advanced, "waitMs": 0},
                            },
                        )
                    )["result"]
                    new_reports = [
                        v for r in next_page["targets"] for v in r["results"]
                    ]
                    report["checks"]["reconnect_delivers_only_new_report"] = (
                        len(new_reports) == 1
                        and "RESULT_B_VERIFIED" in new_reports[0]["text"]
                    )
                    retry = await reconnected._json("POST", "/api/actions", captured[0])
                    report["checks"]["same_followup_command_receipt"] = (
                        retry.get("commandId") == captured[0]["id"]
                    )
                report["checks"]["reconnect_draft_preserved"] = (
                    await composer.input_value() == "UNSENT-COORDINATION-DRAFT"
                )
                report["checks"]["reconnect_selection_preserved"] = await page.evaluate(
                    "id=>window.amplifier.getState().selectedSessionId===id", sid
                )
                await client.command(
                    sid,
                    "conversation.send",
                    {
                        "text": "Both worker gates and the managed process are now released. Inspect the actual process exit/output with its original process_id. Finish both persistent workers only when they are idle and their reports have arrived. Do not rerun anything. Briefly report both worker outputs, the follow-up token, the process output and corrected color, then say COORDINATION_COMPLETE.",
                        "preserveDraft": True,
                    },
                    command_id="finish",
                )
                await finished(
                    observation, service, sid, "COORDINATION_COMPLETE", timeout=150
                )
                history = await service.runtime.control(sid, "history.snapshot")
                private_json(folder / "history.json", history)
                report["checks"]["correction_saved_once"] = (
                    sum(
                        isinstance(m.get("content"), str)
                        and m["content"].endswith(correction)
                        for m in history["messages"]
                    )
                    == 1
                )
                report["checks"]["effects_executed_once"] = all(
                    (workspace / (name + ".runs")).read_text().splitlines() == ["run"]
                    for name in ("A", "B", "PROCESS")
                )
                report["checks"]["followup_not_replayed"] = all(
                    len(
                        [
                            r
                            for r in w.get("reportReceipts", [])
                            if "FOLLOWUP-ALDER-9281" in r.get("text", "")
                        ]
                    )
                    == 1
                    for w in workers()
                    if w["id"] == target_a["workerId"]
                )
                answer = public_answer(sid)
                report["checks"]["final_verified_summary"] = all(
                    value in answer
                    for value in (
                        "RESULT_A_VERIFIED",
                        "RESULT_B_VERIFIED",
                        "RESULT_PROCESS_VERIFIED",
                        "FOLLOWUP-ALDER-9281",
                        "ORANGE",
                    )
                )
                records = service.operations.journal.list(sid)
                report["checks"]["managed_process_durable_completion"] = (
                    len(records) == 1
                    and records[0]["state"] == "completed"
                    and records[0]["returncode"] == 0
                )
                report["checks"]["managed_output_durable"] = any(
                    "RESULT_PROCESS_VERIFIED"
                    in json.dumps(service.operations.journal.read(sid, row["id"]))
                    for row in records
                )
                nodes = service._session(sid).get("execution", {}).get("nodes", [])
                calls = [n for n in nodes if n.get("kind") == "llm"]
                report["models_observed"] = sorted(
                    {n.get("model") for n in calls if n.get("model")}
                )
                report["checks"]["model_unchanged"] = report["models_observed"] == [
                    report["model"]
                ]
                report["checks"]["no_browser_errors"] = not errors
                report["calls_observed"] = len(calls)
                report["checks"]["workers_finished"] = all(
                    w["status"] == "completed" for w in workers()
                )
                await page.screenshot(
                    path=str(folder / "coordination-complete.png"), full_page=True
                )
                report["passed"] = all(report["checks"].values())
    except Exception as exc:  # noqa: BLE001 -- retain bounded acceptance failure evidence
        report["error_type"] = type(exc).__name__
        if page:
            try:
                await page.screenshot(path=str(folder / "failure.png"), full_page=True)
                (folder / "failure-ui.txt").write_text(
                    await page.locator("body").inner_text()
                )
            except Exception as capture_error:  # noqa: BLE001 -- browser may already be closed
                report["diagnostic_capture_error"] = type(capture_error).__name__
        (folder / "error.txt").write_text(str(exc))
        for sid, row in service.runtime.workers.items():
            private_json(
                folder / (sid + "-worker-diagnostics.json"), row.get("stderr", [])
            )
    finally:
        for name in ("A", "B", "PROCESS"):
            (workspace / (name + ".release")).touch()
        if waiter:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        if browser:
            await browser.close()
        await runner.cleanup()
        private_json(folder / "report.json", report)
        private_json(folder / "events.json", observation.events)
        report["credential_cleanup"] = redact_generated_credentials(folder, provider)
        report["checks"]["generated_credentials_cleaned"] = (
            report["credential_cleanup"]["remainingMatches"] == 0
        )
        report["passed"] = (
            report["passed"] and report["checks"]["generated_credentials_cleaned"]
        )
        private_json(folder / "report.json", report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report["checks"],
                "error_type": report.get("error_type"),
                "report": str(folder / "report.json"),
            }
        ),
        flush=True,
    )
    return report["passed"]


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--provider", required=True)
    parser.add_argument(
        "--settings", type=Path, default=Path.home() / ".amplifier/settings.yaml"
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("--allow-live is required for paid provider calls")
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
