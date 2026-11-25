"""Linux helper run with the distro Python's optional GIO system bus binding.

The Avahi entry group belongs to this connection. Disconnect, including after
an unexpected exit, withdraws its interface-scoped address and service records.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import struct
import sys
import time


def main(name, port, label, interface, ip, seconds):
    import fcntl

    if (not re.fullmatch(r"Amplifier-[0-9a-f]{32}", name)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()'-]{0,63}", label)
            or not 1 <= int(port) <= 65535 or not 1 <= int(seconds) <= 120
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface)
            or not any(ipaddress.IPv4Address(ip) in ipaddress.IPv4Network(net)
                       for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))):
        return 1

    from gi.repository import Gio, GLib

    endpoint = "org.freedesktop.Avahi"
    # Never auto-start or reconfigure the local discovery daemon.
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(path, api, method, types=None, args=()):
        return bus.call_sync(endpoint, path, endpoint + "." + api, method,
                             GLib.Variant(types, args) if types else None, None,
                             Gio.DBusCallFlags.NO_AUTO_START, 900, None).unpack()

    index = socket.if_nametoindex(interface)

    def same_address():
        if socket.if_nametoindex(interface) != index:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            request = struct.pack("256s", interface.encode())
            flags = struct.unpack_from("H", fcntl.ioctl(sock.fileno(), 0x8913, request), 16)[0]  # SIOCGIFFLAGS
            if flags & 0x1001 != 0x1001:  # IFF_MULTICAST | IFF_UP
                return False
            address = fcntl.ioctl(sock.fileno(), 0x8915, request)  # SIOCGIFADDR
            return socket.inet_ntoa(address[20:24]) == ip

    if not same_address():
        return 1
    path, = call("/", "Server", "EntryGroupNew")
    host = name.lower() + ".local"
    deadline = time.monotonic() + int(seconds)
    # Interface index + AVAHI_PROTO_INET; publish only the selected address,
    # not Avahi's hostname (which can resolve to additional interfaces).
    try:
        call(path, "EntryGroup", "AddAddress", "(iiuss)", (index, 0, 16, host, ip))  # NO_REVERSE
        call(path, "EntryGroup", "AddService", "(iiussssqaay)",
             (index, 0, 32, name, "_amplifier-link._tcp", "local", host, int(port),
              [("label=" + label).encode()]))  # NO_COOKIE
        call(path, "EntryGroup", "Commit")
        announced = False
        while time.monotonic() < deadline and same_address():
            state, = call(path, "EntryGroup", "GetState")
            if state == 2 and not announced:  # ESTABLISHED
                print("established", flush=True)
                announced = True
            elif state not in (1, 2):  # REGISTERING or ESTABLISHED
                return 1
            time.sleep(0.5)
    finally:
        call(path, "EntryGroup", "Free")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(*sys.argv[1:]))
    except Exception:
        # No daemon replies or host paths in the application response.
        raise SystemExit(1) from None