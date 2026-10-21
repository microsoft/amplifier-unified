import asyncio, json, time
from pathlib import Path
from amplifier_web.session_client import SessionClient
from config import BASE, URL, TOKEN, STORIES, SERVER

identity = json.loads((BASE / "identity.json").read_text())
sid = identity["sessionId"]
run = identity["run"]


def appstep(action, args, tag):
    return {
        "name": "app_control",
        "arguments": {
            "operation": "dispatch",
            "args": {"action": action, "args": args, "id": run + "-" + tag},
        },
    }


def unpack(snapshot):
    text = next(
        m["text"]
        for m in reversed(snapshot["session"]["messages"])
        if m.get("role") == "assistant"
        and m.get("text", "").startswith("STORIES_ACCEPTANCE_DONE ")
    )
    return [json.loads(x["content"]) for x in json.loads(text.split(" ", 1)[1])]


async def turn(c, steps, tag):
    before = len((await c.snapshot(sid))["session"].get("messages", []))
    start = time.monotonic()
    await c.command(
        sid,
        "conversation.send",
        {"text": "STORIES_ACCEPTANCE " + json.dumps({"steps": steps})},
        command_id=run + "-" + tag,
    )
    async with asyncio.timeout(180):
        async for snap in c.snapshots(sid, reconnect=False):
            s = snap["session"]
            ms = s.get("messages", [])
            if s.get("status") == "error":
                (BASE / (tag + "-error.json")).write_text(json.dumps(snap, indent=2))
                raise RuntimeError("worker failed " + tag)
            if (
                s.get("status") == "idle"
                and len(ms) > before
                and ms[-1].get("text", "").startswith("STORIES_ACCEPTANCE_DONE ")
            ):
                (BASE / (tag + "-snapshot.json")).write_text(json.dumps(snap, indent=2))
                return unpack(snap), round(time.monotonic() - start, 3)


async def action(c, name, args, tag):
    r = await c._json(
        "POST", "/api/actions", {"action": name, "args": args, "id": run + "-" + tag}
    )
    if not r.get("operationId"):
        return r.get("result", r)
    for _ in range(200):
        row = await c._json("GET", "/api/smart-tools/operations/" + r["operationId"])
        if row.get("status") == "completed":
            return row["result"]
        if row.get("status") in ["failed", "interrupted"]:
            raise RuntimeError(row)
        await asyncio.sleep(0.1)
    raise RuntimeError("operation timed out")


async def main():
    alloc = unpack(json.loads((BASE / "allocation-snapshot.json").read_text()))[-1][
        "output"
    ]["result"]["items"][0]
    assert alloc["access"]["status"] == "allowed" and not alloc["fileAccessChanged"]
    directory = Path(alloc["directory"])
    payload = {
        "title": "Stories owned storage acceptance",
        "storyboard": {
            "name": "A fictional request",
            "approach": "Show the supported path",
            "tradeoff": "Synthetic content only",
            "panels": [
                {
                    "id": "arrival",
                    "title": "Request arrives",
                    "action": "A fictional team receives a request.",
                    "visual": "Planned request card",
                    "asset_id": "",
                    "narration": "",
                    "notes": "No real customer content.",
                    "evidence_ids": [],
                },
                {
                    "id": "review",
                    "title": "Review retained work",
                    "action": "The reviewer reopens the same saved storyboard.",
                    "visual": "Planned review screen",
                    "asset_id": "",
                    "narration": "",
                    "notes": "No generated images or speech.",
                    "evidence_ids": [],
                },
            ],
        },
        "request_id": run + "-import",
        "fidelity": "outline",
    }
    token = TOKEN.read_text().strip()
    async with SessionClient(URL, token, run) as c:
        results, seconds = await turn(
            c,
            [
                {
                    "name": "write_file",
                    "arguments": {
                        "file_path": str(directory / "library-context.json"),
                        "content": json.dumps(payload),
                    },
                },
                {
                    "name": "read_file",
                    "arguments": {"file_path": str(directory / "library-context.json")},
                },
                {
                    "name": "write_file",
                    "arguments": {
                        "file_path": str(BASE / "unapproved-sibling.txt"),
                        "content": "MUST NOT EXIST",
                    },
                },
            ],
            "preflight",
        )
        assert results[0]["success"] and results[1]["success"], results
        assert (
            not results[2]["success"]
            and results[2]["error"]["code"] == "file_source_access"
        ), results[2]
        assert not (BASE / "unapproved-sibling.txt").exists()
        report = {
            "allocation": alloc,
            "preflightSeconds": seconds,
            "nativeWrite": results[0]["success"],
            "nativeRead": results[1]["success"],
            "siblingDenied": results[2]["error"],
        }
        (BASE / "joined-report.json").write_text(json.dumps(report, indent=2))
        await action(
            c,
            "smartTools.configure",
            {
                "id": SERVER,
                "name": "Stories acceptance",
                "command": str(STORIES / ".venv/bin/stories-mcp"),
                "args": ["--storage", str(directory / "library")],
                "cwd": str(directory),
            },
            "configure",
        )
        connected = await action(c, "smartTools.connect", {"id": SERVER}, "connect")
        (BASE / "connected.json").write_text(json.dumps(connected, indent=2))
        print(
            json.dumps({"preflight": report, "connectedKeys": list(connected)}),
            flush=True,
        )
        state = await c._json("GET", "/api/state")
        server = next(x for x in state["smartTools"]["servers"] if x["id"] == SERVER)
        (BASE / "server.json").write_text(json.dumps(server, indent=2))
        rev = server["catalogRevision"]
        schemas = await action(
            c,
            "smartTools.schemas",
            {
                "id": SERVER,
                "names": [
                    "stories_create_storyboard",
                    "stories_get_story",
                    "stories_get_revision",
                    "stories_get_export",
                ],
                "catalogRevision": rev,
            },
            "schemas",
        )
        (BASE / "schemas.json").write_text(json.dumps(schemas, indent=2))
        result, seconds = await turn(
            c,
            [
                appstep(
                    "smartTools.call",
                    {
                        "id": SERVER,
                        "name": "stories_create_storyboard",
                        "arguments": payload,
                        "catalogRevision": rev,
                        "sessionId": sid,
                    },
                    "import-call",
                )
            ],
            "import-turn",
        )
        assert result[0]["success"], result
        op = result[0]["output"]["operationId"]
        for _ in range(200):
            receipt = await c._json("GET", "/api/smart-tools/operations/" + op)
            if receipt["status"] in ["completed", "failed"]:
                break
            await asyncio.sleep(0.1)
        (BASE / "import-receipt.json").write_text(json.dumps(receipt, indent=2))
        assert receipt["status"] == "completed", receipt
        content = receipt["result"]["structuredContent"]
        assert not receipt["result"].get("isError"), content
        report.update(
            importSeconds=seconds, story=content, operationId=op, serverRevision=rev
        )
        (BASE / "joined-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps({"imported": content}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
