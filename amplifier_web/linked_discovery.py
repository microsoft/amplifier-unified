"""Bounded private discovery. Inventory never contacts the advertised service."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from dataclasses import dataclass

from .deployment import LINK_DEVICE_ID as DEVICE, LINK_INSTANCE_ID as INSTANCE, LINK_LABEL as LABEL
from .deployment import validate_discovery

SERVICE = "_amplifier-link._tcp"
NAME = re.compile(r"Amplifier-([0-9a-f]{32})")
LAN = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
LEASE_SECONDS = 120
CANDIDATE_SECONDS = 120
MAX_RESULTS = 64
MAX_CANDIDATES = 256


class Unavailable(Exception):
    """The local adapter could not prove a usable result."""


def _port(value):
    return type(value) is int and 1 <= value <= 65535


def _address(value, route):
    if not isinstance(value, str):
        return False
    try:
        ip = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return ip in TAILSCALE if route == "tailscale" else any(ip in network for network in LAN)


def _on_link(address, interfaces):
    ip = ipaddress.IPv4Address(address)
    return any(ip in local.network and ip != local.ip and (
        local.network.prefixlen >= 31 or ip not in (local.network.network_address, local.network.broadcast_address)
    ) for local in interfaces)


@dataclass(frozen=True)
class Endpoint:
    instance_id: str
    label: str
    address: str
    port: int

    def valid(self, route):
        return (INSTANCE.fullmatch(self.instance_id) and LABEL.fullmatch(self.label)
                and _address(self.address, route) and _port(self.port))


@dataclass(frozen=True)
class Node:
    device_id: str
    label: str
    online: bool | None
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    endpoint: Endpoint
    route: str
    device_id: str | None
    expires_at: float

    def saved(self):
        return {"address": self.endpoint.address, "port": self.endpoint.port, "deviceId": self.device_id}


async def _pipe(*args):
    try:
        return await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, NotImplementedError):
        raise Unavailable from None


async def _stop(process, *, graceful=False):
    if process.returncode is None:
        try:
            (process.terminate if graceful else process.kill)()
        except ProcessLookupError:
            pass
    await process.wait()


async def _read(*args):
    process = await _pipe(*args)
    try:
        async with asyncio.timeout(3):
            data = bytearray()
            while chunk := await process.stdout.read(16384):
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise Unavailable
            if await process.wait() != 0:
                raise Unavailable
            return data.decode("utf-8")
    except (TimeoutError, UnicodeError):
        raise Unavailable from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # The reader can be paused at its buffer limit. Drain after killing.
        while await process.stdout.read(16384):
            pass
        await process.wait()


async def _json(*args):
    try:
        return json.loads(await _read(*args))
    except (ValueError, RecursionError):
        raise Unavailable from None


def _private_interfaces(rows):
    if not isinstance(rows, list):
        raise Unavailable
    interfaces = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("ifname"), str):
            continue
        addresses = row.get("addr_info")
        for addr in addresses if isinstance(addresses, list) else ():
            try:
                local = ipaddress.IPv4Interface(f"{addr['local']}/{addr['prefixlen']}")
            except (KeyError, TypeError, ValueError):
                continue
            if any(local.network.subnet_of(network) for network in LAN):
                interfaces.setdefault(row["ifname"], []).append(local)
    return interfaces


class LocalDiscovery:
    """Read existing network tools; never install, start or reconfigure them."""

    def __init__(self, binds):
        self.binds = tuple(binds)

    async def _interfaces(self):
        if sys.platform != "linux":
            raise Unavailable
        rows = await _json("ip", "-j", "-4", "addr", "show", "up", "scope", "global")
        return _private_interfaces(rows)

    async def lan_address(self, address):
        if not _address(address, "lan"):
            return False
        interfaces = await self._interfaces()
        return any(_on_link(address, local) for local in interfaces.values())

    async def tailscale_interface(self, address):
        # A kernel route lookup sends no packets. Don't infer an interface
        # from a CGNAT address or accept a userspace/subnet-router route.
        if sys.platform != "linux" or not _address(address, "tailscale"):
            raise Unavailable
        rows = await _json("ip", "-j", "-4", "route", "get", address)
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                or rows[0].get("dev") != "tailscale0" or rows[0].get("gateway")
                or not _address(rows[0].get("prefsrc"), "tailscale")):
            raise Unavailable
        return "tailscale0"

    async def lan(self):
        interfaces = await self._interfaces()
        text = await _read("avahi-browse", "--terminate", "--resolve", "--parsable",
                           "--no-db-lookup", "--domain=local", SERVICE)
        endpoints = []
        for line in text.splitlines():
            fields = line.split(";", 9)
            if len(fields) != 10:
                continue
            event, interface, family, name, service, domain, _, address, port, txt = fields
            match = NAME.fullmatch(name)
            if (event != "=" or family != "IPv4" or not match or service != SERVICE or domain != "local"
                    or not _address(address, "lan") or not _on_link(address, interfaces.get(interface, ()))
                    or not re.fullmatch(r"[0-9]{1,5}", port) or not _port(int(port))):
                continue
            label = re.search(r'(?:^| )"label=([^"]{1,64})"(?: |$)', txt)
            endpoints.append(Endpoint(match[1], label[1] if label and LABEL.fullmatch(label[1])
                                      else f"Amplifier {match[1][:8]}", address, int(port)))
            if len(endpoints) >= MAX_RESULTS:
                break
        return endpoints

    async def roster(self):
        state = await _json("tailscale", "status", "--json")
        if not isinstance(state, dict) or state.get("BackendState") != "Running":
            raise Unavailable
        peers = state.get("Peer")
        if peers is None:
            return []
        if not isinstance(peers, dict):
            raise Unavailable
        nodes = {}
        for peer in peers.values():
            if not isinstance(peer, dict) or peer.get("ShareeNode") is True:
                continue
            device, label = peer.get("ID"), peer.get("HostName")
            if not isinstance(device, str) or not DEVICE.fullmatch(device):
                continue
            if device in nodes:
                raise Unavailable
            if not isinstance(label, str) or not LABEL.fullmatch(label):
                label = "Tailscale device"
            ips = peer.get("TailscaleIPs")
            addresses = tuple(dict.fromkeys(ip for ip in ips if _address(ip, "tailscale"))) if isinstance(ips, list) else ()
            online = peer.get("Online")
            nodes[device] = Node(device, label, online if type(online) is bool else None, addresses)
        return list(nodes.values())

    async def publish(self, name, port, label):
        if sys.platform != "linux":
            raise Unavailable
        rows = await _json("ip", "-j", "-4", "addr", "show", "up", "scope", "global")
        interfaces = _private_interfaces(rows)
        eligible = [(row["ifname"], str(ip.ip)) for row in rows if isinstance(row, dict)
                    and isinstance(row.get("ifname"), str)
                    and isinstance(row.get("flags"), list) and "MULTICAST" in row["flags"]
                    and "LOOPBACK" not in row["flags"] for ip in interfaces.get(row["ifname"], ())
                    if "0.0.0.0" in self.binds or str(ip.ip) in self.binds]
        if len(eligible) != 1:
            raise Unavailable
        interface, address = eligible[0]
        helper = Path(__file__).with_name("linked_lan_publisher.py")
        process = await _pipe("/usr/bin/python3", "-IB", str(helper), name, str(port), label,
                              interface, address, str(LEASE_SECONDS))
        try:
            async with asyncio.timeout(3):
                line = await process.stdout.readline()
            if line.rstrip() != b"established" or process.returncode is not None:
                raise Unavailable
        except BaseException as exc:
            await _stop(process, graceful=True)
            if isinstance(exc, (TimeoutError, ValueError)):
                raise Unavailable from None
            raise
        return process


class Discovery:
    def __init__(self, config, *, backend=None):
        self.config = validate_discovery(config.get("linked_instances_discovery", {}))
        self.port = config.get("port")
        self.binds = tuple(config.get("bind", ()))
        self.backend = LocalDiscovery(self.binds) if backend is None else backend
        self.candidates = {}
        self.roster_ids = set()
        self.roster_expiry = 0
        self.lease = None
        self.lease_task = None
        self.lease_deadline = 0
        self.lease_timestamp = None
        self.closed = False
        self.lock = asyncio.Lock()

    def capabilities(self):
        return {route: self.config[route] for route in ("lan", "tailscale")}

    def expires_at(self):
        if self.lease is not None and self.lease.returncode is None and self.lease_deadline > time.monotonic():
            return self.lease_timestamp
        return None

    def _expire(self):
        now = time.monotonic()
        self.candidates = {key: item for key, item in self.candidates.items() if item.expires_at > now}
        return now

    def _results(self, endpoints, route, own, device=None):
        now = self._expire()
        grouped = {}
        for endpoint in dict.fromkeys(endpoints[:MAX_RESULTS]):
            if endpoint.valid(route) and endpoint.instance_id != own:
                grouped.setdefault(endpoint.instance_id, []).append(endpoint)
        rows = []
        for choices in grouped.values():
            if len(choices) != 1:
                continue
            endpoint = choices[0]
            if len(self.candidates) >= MAX_CANDIDATES:
                self.candidates.pop(next(iter(self.candidates)))
            token = secrets.token_hex(16)
            self.candidates[token] = Candidate(endpoint, route, device, now + CANDIDATE_SECONDS)
            rows.append({"candidateId": token, "instanceId": endpoint.instance_id,
                         "label": endpoint.label, "route": route})
        return {"status": "ok" if rows else "empty", "instances": rows}

    def candidate(self, token):
        self._expire()
        return self.candidates.get(token)

    async def browse(self, own):
        try:
            async with self.lock:
                return self._results(await self.backend.lan(), "lan", own)
        except Unavailable:
            return {"status": "unavailable", "instances": []}

    async def devices(self):
        async with self.lock:
            self.roster_ids.clear()
            try:
                nodes = (await self.backend.roster())[:MAX_RESULTS]
            except Unavailable:
                return {"status": "unavailable", "devices": []}
            self.roster_ids = {node.device_id for node in nodes}
            self.roster_expiry = time.monotonic() + CANDIDATE_SECONDS
            return {"status": "ok" if nodes else "empty", "devices": [
                {"deviceId": node.device_id, "label": node.label, "online": node.online} for node in nodes]}

    async def find(self, device, own):
        async with self.lock:
            if device not in self.roster_ids or time.monotonic() >= self.roster_expiry:
                return {"status": "unavailable", "instances": []}
            try:
                nodes = await self.backend.roster()
            except Unavailable:
                return {"status": "unavailable", "instances": []}
            node = next((node for node in nodes if node.device_id == device), None)
            if node is None:
                return {"status": "empty", "instances": []}
            if node.online is False:
                return {"status": "offline", "instances": []}
            # The only eligible ports and identities are owner-known service
            # profiles for the selected stable node ID. No DNS or probing.
            addresses = [ip for ip in node.addresses if _address(ip, "tailscale")]
            if len(addresses) != 1:
                return {"status": "empty", "instances": []}
            rows = self.config["tailscale_endpoints"].get(device, ())
            if not rows:
                return {"status": "unavailable", "reason": "tailscale_profile_required", "instances": []}
            return self._results([Endpoint(row["instanceId"], row["label"], addresses[0], row["port"])
                                  for row in rows], "tailscale", own, device)

    async def usable(self, route, saved, instance_id):
        if (not self.capabilities().get(route) or not isinstance(saved, dict)
                or saved.keys() != {"address", "port", "deviceId"}
                or not _address(saved["address"], route) or not _port(saved["port"])):
            return False
        try:
            if route == "lan":
                return saved["deviceId"] is None and await self.backend.lan_address(saved["address"])
            device = saved["deviceId"]
            if not isinstance(device, str) or not any(
                row["instanceId"] == instance_id and row["port"] == saved["port"]
                for row in self.config["tailscale_endpoints"].get(device, ())
            ):
                return False
            nodes = await self.backend.roster()
            return any(node.device_id == device and node.online is not False
                       and saved["address"] in node.addresses for node in nodes)
        except Unavailable:
            return False

    async def discoverable(self, enabled, instance_id):
        async with self.lock:
            expires = self.expires_at()
            if enabled and expires is not None:
                return {"status": "ok", "expiresAt": expires}
            await self._close()
            if not enabled:
                return {"status": "ok", "expiresAt": None}
            if self.closed or not _port(self.port):
                return {"status": "unavailable", "expiresAt": None}
            label = self.config["label"] or f"Amplifier {instance_id[:8]}"
            started = time.monotonic()
            expires = int(time.time()) + LEASE_SECONDS
            try:
                self.lease = await self.backend.publish(f"Amplifier-{instance_id}", self.port, label)
            except Unavailable:
                return {"status": "unavailable", "expiresAt": None}
            self.lease_deadline = started + LEASE_SECONDS
            self.lease_timestamp = expires
            self.lease_task = asyncio.create_task(self._expire_lease(self.lease))
            expires = self.expires_at()
            return {"status": "ok" if expires else "unavailable", "expiresAt": expires}

    async def _expire_lease(self, lease):
        await asyncio.sleep(max(0, self.lease_deadline - time.monotonic()))
        await _stop(lease, graceful=True)

    async def _close(self):
        if self.lease_task is not None:
            self.lease_task.cancel()
            await asyncio.gather(self.lease_task, return_exceptions=True)
            self.lease_task = None
        if self.lease is not None:
            await _stop(self.lease, graceful=True)
            self.lease = None
        self.lease_deadline = 0
        self.lease_timestamp = None

    async def close(self, _app):
        self.closed = True
        async with self.lock:
            await self._close()