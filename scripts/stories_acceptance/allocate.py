import asyncio, json, uuid
from amplifier_web.session_client import SessionClient
from config import BASE, URL, TOKEN, FIXTURE

RUN = "stories-" + uuid.uuid4().hex[:10]


async def main():
    token = TOKEN.read_text().strip()
    async with SessionClient(URL, token, RUN) as c:
        created = await c.create_session(
            {
                "title": "Stories joined acceptance " + RUN,
                "workspace": str(BASE / "workspace"),
                "bundle": str(FIXTURE / "bundle/bundle.md"),
            },
            command_id=RUN + "-create",
        )
        print("Created owned synthetic chat", flush=True)
        sid = (
            created.get("result", {}).get("sessionId")
            or created["state"]["selectedSessionId"]
        )
        state = await c._json("GET", "/api/state")
        workspace = next(
            x for x in state["workspaces"] if x["path"] == str(BASE / "workspace")
        )
        args = {
            "workspaceId": workspace["id"],
            "sessionId": sid,
            "kind": "scratch-directory",
            "label": "Stories acceptance retained store",
        }
        steps = [
            {
                "name": "app_control",
                "arguments": {
                    "operation": "list_actions",
                    "args": {"prefix": "workspace.resources."},
                },
            },
            {
                "name": "app_control",
                "arguments": {
                    "operation": "dispatch",
                    "args": {
                        "action": "workspace.resources.provision",
                        "args": args,
                        "id": RUN + "-allocate",
                    },
                },
            },
        ]
        prompt = "STORIES_ACCEPTANCE " + json.dumps({"steps": steps})
        await c.command(
            sid, "conversation.send", {"text": prompt}, command_id=RUN + "-send"
        )
        (BASE / "identity.json").write_text(
            json.dumps(
                {"run": RUN, "sessionId": sid, "workspaceId": workspace["id"]}, indent=2
            )
        )
        async with asyncio.timeout(180):
            async for snap in c.snapshots(sid, reconnect=False):
                session = snap.get("session", {})
                messages = session.get("messages", [])
                if session.get("status") == "error":
                    (BASE / "failed.json").write_text(json.dumps(snap, indent=2))
                    raise RuntimeError("worker failed")
                if session.get("status") == "idle" and any(
                    "STORIES_ACCEPTANCE_DONE" in m.get("text", "")
                    for m in messages
                    if m.get("role") == "assistant"
                ):
                    (BASE / "allocation-snapshot.json").write_text(
                        json.dumps(snap, indent=2)
                    )
                    print("Native allocation passed", flush=True)
                    break


asyncio.run(main())
