"""Small HTTP/SSE adapter for terminal and native Unified clients.

This module never loads a runtime or owns a Foundation execution lock. Callers
retain their client ID and command IDs; transport reconnect only reads state.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import aiohttp


class SessionClientError(Exception):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        super().__init__(payload.get("error", f"Unified returned HTTP {status}"))


async def _lines(content):
    # SSE snapshots can contain long transcripts. StreamReader.readline has a
    # small line limit; parse chunks without truncating a single JSON record.
    fragments = []
    async for chunk in content.iter_chunked(65536):
        parts = chunk.split(b"\n")
        for part in parts[:-1]:
            fragments.append(part)
            yield b"".join(fragments).decode("utf-8").rstrip("\r")
            fragments.clear()
        if parts[-1]:
            fragments.append(parts[-1])


class SessionClient:
    def __init__(self, base_url, token, client_id, *, kind="tui", ssl=None):
        self.base_url = base_url.rstrip("/")
        self.client_id, self.kind = client_id, kind
        self.headers = {"Authorization": "Bearer " + token, "X-Amplifier-Client": client_id}
        self.ssl = ssl
        self.http = None

    async def __aenter__(self):
        self.http = aiohttp.ClientSession(headers=self.headers)
        try:
            await self.attach()
        except BaseException:
            await self.http.close()
            raise
        return self

    async def __aexit__(self, *exc):
        # Detach only. Work continues on the host.
        await self.http.close()

    async def _json(self, method, path, payload=None):
        async with self.http.request(method, self.base_url + path, json=payload, ssl=self.ssl,
                                     timeout=aiohttp.ClientTimeout(total=60)) as response:
            data = await response.json()
            if response.status >= 400:
                raise SessionClientError(response.status, data)
            return data

    async def attach(self, *, resume_client_id=None):
        return await self._json("POST", "/api/clients/attach", {
            "clientId": self.client_id, "kind": self.kind, "protocolVersion": 1,
            **({"resumeClientId": resume_client_id} if resume_client_id else {})})

    async def sessions(self, *, offset=0, limit=100):
        return await self._json("GET", f"/api/sessions?offset={offset}&limit={limit}")

    async def create_session(self, args, *, command_id):
        return await self._json("POST", "/api/actions",
            {"action": "session.create", "args": args, "id": command_id})

    async def snapshot(self, session_id):
        return await self._json("GET", "/api/sessions/" + quote(session_id, safe=""))

    async def command(self, session_id, action, args, *, command_id):
        # Never synthesize a replacement ID on a retry or reconnect.
        return await self._json("POST", "/api/sessions/" + quote(session_id, safe="") + "/commands",
                                {"id": command_id, "action": action, "args": args})

    async def snapshots(self, session_id, *, reconnect=True):
        """Yield complete authoritative snapshots; replace, do not append them.

        Transient disconnects back off to five seconds. Authentication/protocol
        errors surface immediately. Cancelling the iterator never stops a run.
        """
        path = "/api/sessions/" + quote(session_id, safe="") + "/events"
        delay = .25
        while True:
            try:
                async with self.http.get(self.base_url + path, ssl=self.ssl,
                        timeout=aiohttp.ClientTimeout(total=None, sock_read=45)) as response:
                    if response.status >= 400:
                        raise SessionClientError(response.status, await response.json())
                    lines, event = [], ""
                    async for line in _lines(response.content):
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            lines.append(line[5:].lstrip())
                        elif not line:
                            if event == "snapshot" and lines:
                                snapshot = json.loads("\n".join(lines))
                                delay = .25
                                yield snapshot
                                if snapshot.get("deleted"):
                                    return
                            lines, event = [], ""
            except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, TimeoutError):
                if not reconnect:
                    raise
            if not reconnect:
                return
            await asyncio.sleep(delay)
            delay = min(5, delay * 2)
