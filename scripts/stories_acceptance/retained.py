import asyncio, importlib.util, json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "joined_helpers", Path(__file__).with_name("import_story.py")
)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


async def main():
    b = h.BASE
    report = json.loads((b / "joined-report.json").read_text())
    story = report["story"]["result"]
    ids = {"story_id": story["story_id"]}
    token = h.TOKEN.read_text().strip()
    async with h.SessionClient(h.URL, token, h.run) as c:
        steps = [
            h.appstep(
                "smartTools.readResult",
                {"operationId": report["operationId"]},
                "read-import",
            ),
            h.appstep(
                "smartTools.call",
                {
                    "id": h.SERVER,
                    "name": "stories_get_story",
                    "arguments": ids,
                    "catalogRevision": report["serverRevision"],
                    "sessionId": h.sid,
                },
                "read-story",
            ),
        ]
        results, timing = await h.turn(c, steps, "retained-turn")
        assert all(x["success"] for x in results), results
        op = results[1]["output"]["operationId"]
        for _ in range(100):
            receipt = await c._json("GET", "/api/smart-tools/operations/" + op)
            if receipt["status"] in ["completed", "failed"]:
                break
            await asyncio.sleep(0.1)
        assert receipt["status"] == "completed" and not receipt["result"].get(
            "isError"
        ), receipt
        (b / "retained-receipt.json").write_text(json.dumps(receipt, indent=2))
        read, t = await h.turn(
            c,
            [
                h.appstep(
                    "smartTools.readResult",
                    {"operationId": op},
                    "read-retained-receipt",
                )
            ],
            "read-retained-turn",
        )
        assert read[0]["success"], read
        report.update(retainedReadViaNativeWorker=True, retainedReadSeconds=timing)
        (b / "joined-report.json").write_text(json.dumps(report, indent=2))


asyncio.run(main())
