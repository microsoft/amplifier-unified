"""Read forwarding headers only from configured reverse-proxy peers.

With no ``trusted_proxies`` configured (the default) this middleware does
nothing: the scheme, host and client address are those of the real socket and
request line, and ``X-Forwarded-*`` headers are ignored.

When the TCP peer is a configured proxy, its ``X-Forwarded-Proto``,
``X-Forwarded-Host`` and ``X-Forwarded-For`` replace the request's scheme, host
and client address. Every later check (Host allowlist, exact Origin, Secure
cookies) then runs unchanged against the address the browser actually used.
A request from any other peer is never altered, whatever headers it sends.
"""
from __future__ import annotations

import ipaddress

from aiohttp import web

from .deployment import validate_origin

VIA_PROXY = "amplifier_via_trusted_proxy"


def _address(value) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(str(value).strip().split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def peer_address(request):
    transport = request.transport
    peer = transport.get_extra_info("peername") if transport is not None else None
    return _address(peer[0]) if isinstance(peer, (tuple, list)) and peer else None


def trusted(networks, address) -> bool:
    return address is not None and any(address in network for network in networks)


def _single(request, name: str) -> str | None:
    values = request.headers.getall(name, [])
    if not values:
        return None
    if len(values) != 1 or "," in values[0]:
        raise ValueError(f"{name} must contain exactly one value.")
    return values[0].strip()


def _client(request, networks) -> str | None:
    """The nearest untrusted address in X-Forwarded-For, read right to left."""
    hops = [hop.strip() for value in request.headers.getall("X-Forwarded-For", []) for hop in value.split(",")]
    for hop in reversed(hops):
        address = _address(hop)
        if address is None:
            raise ValueError("X-Forwarded-For contains an invalid address.")
        if not trusted(networks, address):
            return str(address)
    return None


@web.middleware
async def forwarded_request(request, handler):
    networks = request.app.get("trusted_proxies") or ()
    if not networks or not trusted(networks, peer_address(request)):
        return await handler(request)
    request[VIA_PROXY] = True
    try:
        scheme = _single(request, "X-Forwarded-Proto")
        host = _single(request, "X-Forwarded-Host")
        client = _client(request, networks)
        changes = {}
        if scheme is not None:
            if scheme.lower() not in {"http", "https"}:
                raise ValueError("X-Forwarded-Proto must be http or https.")
            changes["scheme"] = scheme.lower()
        if host is not None:
            # Reject anything that is not a plain host[:port] authority.
            validate_origin(f"{changes.get('scheme', request.scheme)}://{host}")
            changes["host"] = host
        if client is not None:
            changes["remote"] = client
    except ValueError as exc:
        return web.json_response({"error": f"Invalid forwarding header from a trusted proxy: {exc}"}, status=400)
    return await handler(request.clone(**changes) if changes else request)
