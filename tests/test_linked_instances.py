import asyncio
import base64
import io
import json
import os
import socket

import aiohttp
import pytest
from aiohttp import web
from PIL import Image

from amplifier_web import linked_instances, tls
from amplifier_web.auth import SESSION_COOKIE, new_session
from amplifier_web.errors import AppError
from amplifier_web.linked_instances import (
    LOCAL_TLS, OFFER_TTL, PEER_PREFIX, PREFIX, DirectTransport, LinkedInstances, _encode_offer, _parse_offer,
    agent_route, failure,
)
from amplifier_web.server import create_app
from test_service import Runtime


@pytest.mark.parametrize("fault", [
    "lost-reply", "peer-write", "source-write", "malformed", "wrong-receipt", "incomplete-refusal",
])
async def test_two_https_hosts_pair_deduplicate_fence_and_revoke(tmp_path, monkeypatch, fault):
    enrollment_requests = 0
    spoil_next_reply = False

    @web.middleware
    async def observe_enrollment(request, handler):
        nonlocal enrollment_requests, spoil_next_reply
        if request.path != PEER_PREFIX + "/enroll":
            return await handler(request)
        enrollment_requests += 1
        response = await handler(request)
        if not spoil_next_reply:
            return response
        spoil_next_reply = False
        assert response.status == 200
        if fault == "malformed":
            return web.Response(body=b"{", content_type="application/json")
        if fault == "incomplete-refusal":
            return web.json_response({"code": "link_offer_expired"}, status=410)
        return web.json_response({"result": {**json.loads(response.body)["result"], "instanceId": "0" * 32}})

    async def start(root, sock, origin, *, issuer):
        conf = tls.setup_local_ca(root, {
            "bind": ["127.0.0.1"], "port": sock.getsockname()[1], "public_origins": [origin],
            "linked_instances": True,
        })
        runtime = Runtime()
        app = await create_app(root, workspace=root, runtime=runtime, voice=False,
                               background_updates=False, preload_providers=False,
                               background_work=False, server_config=conf)
        if issuer:
            app.middlewares.insert(0, observe_enrollment)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.SockSite(runner, sock, ssl_context=tls.ssl_context(root, conf))
        await site.start()
        return runner, conf, app, runtime

    roots = [tmp_path / "owner-a", tmp_path / "owner-b"]
    for root in roots:
        root.mkdir()
    sockets = [socket.socket() for _ in roots]
    runners = []
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        a, b = ports = [f"https://127.0.0.1:{sock.getsockname()[1]}" for sock in sockets]
        hosts = []
        for i, (root, sock, origin) in enumerate(zip(roots, sockets, ports, strict=True)):
            hosts.append(await start(root, sock, origin, issuer=i == 0))
            runners.append(hosts[-1][0])
        _, conf_a, app_a, runtime_a = hosts[0]
        _, conf_b, app_b, runtime_b = hosts[1]
        # CA verification for the owner browser-facing lane, independent from
        # the application's certificate pin on peer connections.
        import ssl
        contexts = [ssl.create_default_context(cafile=tls.tls_dir(root) / "ca.crt") for root in roots]
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as client:
            async def request(index, operation, data, *, auth="owner", origin=True, raw=False, headers=None):
                dest, context, app = ports[index], contexts[index], hosts[index][2]
                req_headers, cookies = dict(headers or {}), {}
                if auth == "owner":
                    cookies[SESSION_COOKIE] = new_session(app["session_secret"])
                elif auth:
                    req_headers["Authorization"] = "Bearer " + auth
                if origin:
                    req_headers["Origin"] = dest if origin is True else origin
                path = operation if raw else PREFIX + "/" + operation
                async with client.post(dest + path, ssl=context, headers=req_headers, cookies=cookies,
                                       json=data, allow_redirects=False) as response:
                    assert response.headers["Cache-Control"] == "no-store"
                    return response.status, await response.json()

            assert (await request(0, "list", {}, auth=None))[0] == 401
            assert (await request(0, "list", {}, origin=False))[0] == 403
            assert (await request(0, "list", {}, origin=b))[0] == 403
            id_a = (await request(0, "list", {}))[1]["result"]["instanceId"]
            id_b = (await request(1, "list", {}))[1]["result"]["instanceId"]
            assert id_b != id_a
            assert app_a["control_token"] != app_b["control_token"]
            assert (await request(0, "list", {}, auth=app_b["control_token"]))[0] == 401
            make = {"commandId": "offer-a", "origin": a}
            status, offer = await request(0, "offer", make)
            assert status == 200
            offer = offer["result"]
            assert offer["code"] == offer["qrPayload"]
            with Image.open(io.BytesIO(base64.b64decode(offer["qrPngBase64"]))) as qr:
                assert qr.format == "PNG" and qr.width == qr.height < 500
            assert (await request(0, "offer", make))[1]["result"] == offer
            parsed = _parse_offer(offer["code"])
            attempts = enrollment_requests
            status, wrong_pin = await request(1, "join", {"commandId": "wrong-pin",
                    "code": _encode_offer({**parsed, "fingerprint": "0" * 64})})
            assert status == 502 and wrong_pin["code"] == "link_verification_failed"
            assert enrollment_requests == attempts
            status, wrong_instance = await request(1, "join", {"commandId": "wrong-instance",
                    "code": _encode_offer({**parsed, "instanceId": "0" * 32})})
            assert status == 502 and wrong_instance["code"] == "link_verification_failed"
            assert (await request(0, "list", {}))[1]["result"]["links"] == []

            links_a = app_a["service"].linked_instances
            links_b = app_b["service"].linked_instances
            with monkeypatch.context() as expired:
                expired.setattr(links_a, "clock", lambda: offer["expiresAt"] + 1)
                status, refusal = await request(1, "join", {"commandId": "expired-join", "code": offer["code"]})
                assert status == 410 and refusal["code"] == "link_offer_expired"
            assert links_a.list()["links"] == []

            # Inject a failure after the real replacement, at the same boundary
            # as a directory sync failure. Do not roll the replacement back.
            target_path = (links_a if fault == "peer-write" else links_b).store.path
            write_private = linked_instances.write_private
            failed_write = False

            def write_then_fail(path, data, *, owned_root):
                nonlocal failed_write
                write_private(path, data, owned_root=owned_root)
                if path == target_path and not failed_write and json.loads(data)["links"]:
                    failed_write = True
                    raise OSError("synthetic failure after ledger replacement")

            class DropReply(DirectTransport):
                async def enroll(self, route, payload):
                    await super().enroll(route, payload)
                    raise failure("link_unavailable")

            join = {"commandId": "join-a", "code": offer["code"]}
            attempts = enrollment_requests
            with monkeypatch.context() as fault_patch:
                if fault in {"peer-write", "source-write"}:
                    fault_patch.setattr(linked_instances, "write_private", write_then_fail)
                elif fault == "lost-reply":
                    fault_patch.setattr(links_b, "transport", DropReply())
                else:
                    spoil_next_reply = True
                status, unconfirmed = await request(1, "join", join)
                assert status == 503 and unconfirmed["code"] == "link_join_unconfirmed"
                assert not unconfirmed["accepted"]
            assert enrollment_requests == attempts + 1
            assert failed_write == (fault in {"peer-write", "source-write"})
            incoming = (await request(0, "list", {}))[1]["result"]["links"]
            assert len(incoming) == 1 and incoming[0]["instanceId"] == id_b

            # Expiration doesn't invalidate an exact saved enrollment. A fresh
            # command on the consumed code is not a substitute for that replay.
            clock = links_a.clock
            links_a.clock = lambda: offer["expiresAt"] + 1
            if fault != "source-write":
                assert (await request(1, "list", {}))[1]["result"]["links"] == []
                status, used = await request(1, "join", {**join, "commandId": "not-the-original"})
                assert status == 409 and used["code"] == "link_offer_used"
                with monkeypatch.context() as unavailable:
                    unavailable.setattr(links_a, "enabled", False)
                    status, refusal = await request(1, "join", join)
                    assert status == 503 and refusal["code"] == "link_join_unconfirmed"

            # A fresh invitation must still be refused (by the issuer unless
            # the joining host had already replaced its own ledger).
            repeat_code = (await request(0, "offer", {**make, "commandId": "fresh-offer"}))[1]["result"]["code"]
            peer_conflict = {"commandId": "fresh-join-during-uncertainty", "code": repeat_code}
            status, refused = await request(1, "join", peer_conflict)
            assert status == 409 and refused["code"] == "link_already_linked"
            assert (await request(0, "list", {}))[1]["result"]["links"] == incoming
            with links_b.store.transaction() as db:
                assert "pending" not in db["commands"][peer_conflict["commandId"]]
            attempts = enrollment_requests
            reopened = LinkedInstances(roots[1], conf_b)
            assert reopened.list()["instanceId"] == id_b
            assert (await reopened.join(join))["grantId"] == incoming[0]["grantId"]
            assert enrollment_requests == attempts + (fault != "source-write")
            links_a.clock = clock
            replies = await asyncio.gather(request(1, "join", join), request(1, "join", join))
            assert replies[0] == replies[1] and replies[0][0] == 200
            outgoing = replies[0][1]["result"]
            grant = outgoing["grantId"]
            assert outgoing == {"grantId": grant, "instanceId": id_a, "direction": "outgoing",
                                "scope": "status", "origin": a, "revoked": False}
            local_conflict = {"commandId": "new-join", "code": repeat_code}
            assert (await request(1, "join", local_conflict))[1]["code"] == "link_already_linked"
            assert (await request(0, "list", {}))[1]["result"]["links"] == incoming
            assert (await request(1, "revoke", {"commandId": "join-a", "grantId": grant}))[1]["code"] == "link_command_conflict"
            found = await request(1, "status", {"instanceId": id_a})
            assert found == (200, {"result": {"instanceId": id_a,
                                           "generationId": app_a["service"].linked_instances.generation,
                                           "availability": "online"}})
            assert (await request(0, "status", {"instanceId": id_b}))[1]["code"] == "link_not_found"
            assert await agent_route(links_b, "get_state", {"target": {"instanceId": id_a}}) == found[1]["result"]
            with links_b.store.transaction() as db:
                credential = "ali_" + db["links"][grant]["credential"]
            assert credential[4:] not in app_a["service"].linked_instances.store.path.read_text()
            if os.name != "nt":
                assert links_b.store.path.stat().st_mode & 0o777 == 0o600
            assert (await request(0, "offer", make, auth=credential))[0] == 403
            assert (await request(0, "/api/actions", {"action": "session.create"}, auth=credential, raw=True))[0] == 403
            assert (await request(0, f"{PEER_PREFIX}/status", {"grantId": grant, "instanceId": id_a,
                           "requesterId": id_b}, raw=True, auth=app_a["control_token"], origin=False))[1]["code"] == "link_revoked"
            assert runtime_a.started == runtime_b.started == []

            reverse_code = (await request(1, "offer", {"commandId": "reverse-offer", "origin": b}))[1]["result"]["code"]
            status, reverse = await request(0, "join", {"commandId": "reverse-join", "code": reverse_code})
            assert status == 200 and reverse["result"]["direction"] == "outgoing"
            assert reverse["result"]["grantId"] != grant
            assert (await request(0, "status", {"instanceId": id_b}))[0] == 200

            revoke = {"commandId": "revoke-a", "grantId": grant}
            revoked = await request(0, "revoke", revoke)
            assert revoked[1]["result"]["revoked"] is True
            assert await request(0, "revoke", revoke) == revoked
            assert (await request(1, "status", {"instanceId": id_a}))[1]["code"] == "link_revoked"
            restored_a = LinkedInstances(roots[0], conf_a)
            assert restored_a.list()["instanceId"] == id_a
            assert restored_a.generation != found[1]["result"]["generationId"]
            assert next(row for row in restored_a.list()["links"] if row["grantId"] == grant)["revoked"] is True
            assert (await request(0, "status", {"instanceId": id_b}))[0] == 200
            assert (await request(1, "revoke", {"commandId": "retire-outgoing", "grantId": grant}))[0] == 200
            for rejected in (local_conflict, peer_conflict):
                assert (await request(1, "join", rejected))[1]["code"] == "link_already_linked"
            # The issuer's exact refused command must not become a new grant
            # either: reusing its invitation with a fresh command is denied
            # when the refusal was issued by the peer, not just locally.
            if fault != "source-write":
                assert (await request(1, "join", {**peer_conflict, "commandId": "new-id"}))[1]["code"] == "link_offer_used"
            new_code = (await request(0, "offer", {**make, "commandId": "replacement-offer"}))[1]["result"]["code"]
            status, replacement = await request(1, "join", {"commandId": "replacement-join", "code": new_code})
            assert status == 200 and replacement["result"]["grantId"] != grant
            assert (await request(1, "status", {"instanceId": id_a}))[0] == 200
            assert (await request(0, f"{PEER_PREFIX}/status", {"grantId": grant, "instanceId": id_a,
                           "requesterId": id_b}, raw=True, auth=credential, origin=False))[1]["code"] == "link_revoked"
            await runners[0].cleanup()
            runners.pop(0)
            assert (await request(1, "status", {"instanceId": id_a}))[1]["code"] == "link_unavailable"
    finally:
        for runner in runners:
            await runner.cleanup()
        for sock in sockets:
            sock.close()


def test_opt_in_and_offer_expiry(tmp_path, monkeypatch):
    config = {"public_origins": ["https://127.0.0.1:39278"], "tls": LOCAL_TLS}
    opt_out = LinkedInstances(tmp_path, config)
    with pytest.raises(AppError) as disabled:
        opt_out.list()
    assert disabled.value.code == "link_unsupported"
    assert not opt_out.store.path.exists()
    now = [100]
    links = LinkedInstances(tmp_path, {**config, "linked_instances": True}, clock=lambda: now[0])
    monkeypatch.setattr("amplifier_web.linked_instances._leaf_fingerprint", lambda *_: "a" * 64)
    result = links.offer({"commandId": "offer-id", "origin": config["public_origins"][0]})
    proof = _parse_offer(result["code"])
    enrollment = {"instanceId": proof["instanceId"], "requesterId": "a" * 32, "enrollmentId": "b" * 32,
                  "proof": proof["secret"], "credentialDigest": "c" * 64}
    now[0] += OFFER_TTL
    with pytest.raises(AppError) as expired:
        links.enroll(enrollment, proof["origin"])
    assert expired.value.code == "link_offer_expired"
    assert links.list()["links"] == []


def test_exhausted_command_capacity_does_not_block_revocation(tmp_path, monkeypatch):
    origin = "https://localhost"
    links = LinkedInstances(tmp_path, {"linked_instances": True, "public_origins": [origin], "tls": LOCAL_TLS})
    monkeypatch.setattr("amplifier_web.linked_instances._leaf_fingerprint", lambda *_: "a" * 64)
    monkeypatch.setattr("amplifier_web.linked_instances.MAX_COMMANDS", 1)
    offer = _parse_offer(links.offer({"commandId": "offer", "origin": origin})["code"])
    receipt = links.enroll({"instanceId": offer["instanceId"], "requesterId": "a" * 32, "enrollmentId": "b" * 32,
                            "proof": offer["secret"], "credentialDigest": "c" * 64}, origin)
    with pytest.raises(AppError) as exhausted:
        links.offer({"commandId": "offer-2", "origin": origin})
    assert exhausted.value.code == "link_capacity"
    assert links.revoke({"commandId": "revoke", "grantId": receipt["grantId"]})["revoked"]


@pytest.mark.parametrize(("operation", "args", "code"), [
    ("dispatch", {"target": {"instanceId": "a" * 32}, "action": "runtime.control"}, "link_unsupported"),
    ("history", {"target": {"instanceId": "a" * 32}}, "link_unsupported"),
    ("context.read", {"target": {"instanceId": "a" * 32}}, "link_unsupported"),
    ("get_state", {"target": {"instanceId": "a" * 32}, "path": "/sessions"}, "link_unsupported"),
    ("get_state", {"target": None}, "link_invalid"),
    ("get_state", {"target": {"origin": "https://localhost"}}, "link_invalid"),
    ("get_state", {"target": {"instanceId": "a" * 32}}, "link_not_found"),
])
async def test_explicit_target_does_not_fall_back(tmp_path, operation, args, code):
    links = LinkedInstances(tmp_path, {"linked_instances": True, "public_origins": ["https://localhost"], "tls": LOCAL_TLS})
    with pytest.raises(AppError) as refused:
        await agent_route(links, operation, args)
    assert refused.value.code == code


async def test_preexisting_same_direction_grants_fail_status_closed(tmp_path):
    from hashlib import sha256

    class NoNetwork:
        async def status(self, *args):
            pytest.fail("Ambiguous status must not touch the network")

    peer, token = "a" * 32, "A" * 43
    links = LinkedInstances(tmp_path, {"linked_instances": True, "public_origins": ["https://localhost"],
                                      "tls": LOCAL_TLS}, transport=NoNetwork())
    with links.store.transaction() as db:
        instance_id = db["instanceId"]
        for grant_id, direction in (("b" * 32, "incoming"), ("c" * 32, "incoming"),
                                    ("d" * 32, "outgoing"), ("e" * 32, "outgoing")):
            db["links"][grant_id] = {"grantId": grant_id, "instanceId": peer, "direction": direction,
                                    "scope": "status", "origin": None, "revoked": False,
                                    "credentialDigest": sha256(token.encode()).hexdigest()}
    args = {"instanceId": instance_id, "requesterId": peer, "grantId": "b" * 32}
    with pytest.raises(AppError) as invalid:
        links.peer_status(args, "Bearer ali_" + "B" * 43)
    assert invalid.value.code == "link_revoked"
    with pytest.raises(AppError) as incoming:
        links.peer_status(args, "Bearer ali_" + token)
    assert incoming.value.code == "link_ambiguous"
    with pytest.raises(AppError) as outgoing:
        await links.status({"instanceId": peer})
    assert outgoing.value.code == "link_ambiguous"
    assert links.revoke({"commandId": "retire-extra", "grantId": "c" * 32})["revoked"]
    assert links.peer_status(args, "Bearer ali_" + token)["availability"] == "online"