"""Saved loopback port selection; native cases run on the Windows CI lane."""
import ctypes
import os
import socket
import subprocess

import pytest

from amplifier_web import windows_ports as ports

DYNAMIC = """
Protocol tcp Dynamic Port Range
---------------------------------
Start Port      : 49152
Number of Ports : 16384

"""
EXCLUDED = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      5357        5357
     20000       20999
     50000       50059     *

* - Administered port exclusions.

"""


def test_netsh_ranges_parse_by_shape_not_by_english_labels():
    assert ports.parse_dynamic(DYNAMIC) == (49152, 65535)
    localized = 'Dynamischer Portbereich des Protokolls "tcp"\r\n-----\r\nStartport        : 1024\r\nAnzahl von Ports : 64511\r\n'
    assert ports.parse_dynamic(localized) == (1024, 65534)
    assert ports.parse_excluded(EXCLUDED) == [(5357, 5357), (20000, 20999), (50000, 50059)]
    assert ports.parse_excluded("Protocol tcp Port Exclusion Ranges\n\nStart Port    End Port\n----------    --------\n\n") == []


@pytest.mark.parametrize("text", ["", "Start Port : 49152\n", "Start : 0\nCount : 5\n",
                                  "Start : 60000\nCount : 9000\n", "A : 1\nB : 2\nC : 3\n"])
def test_unreadable_dynamic_range_is_unknown_not_guessed(text):
    assert ports.parse_dynamic(text) is None


def test_pool_stays_outside_the_dynamic_and_reserved_ranges():
    found = ports.pool((49152, 65535), [(20000, 20999), (50000, 50059)])
    assert found[0] == 21000 and found[-1] == 39999 and len(found) == 19000
    # Unknown ranges still give the preferred block; the bind filter remains.
    assert ports.pool(None, None) == list(ports.PREFERRED)


def test_pool_moves_when_the_dynamic_range_covers_the_preferred_block():
    found = ports.pool((15000, 45000), [(10000, 10099)])
    assert min(found) == 10100 and 14999 in found and 45001 in found
    assert not any(15000 <= port <= 45000 for port in found)
    assert 8941 not in found and 8942 not in found


def test_pool_survives_a_dynamic_range_that_starts_at_1024():
    # Nothing is outside such a range; reserved blocks are still avoided.
    found = ports.pool((1024, 65535), [(20000, 29999)])
    assert found[0] == 30000 and found[-1] == 39999


def test_select_skips_taken_candidates_and_never_adopts_them(monkeypatch):
    monkeypatch.setattr(ports, "ranges", lambda: ((49152, 65535), [(20000, 20999)]))
    states = {21000: "in-use", 21001: "reserved", 21002: "free"}
    seen = []
    monkeypatch.setattr(ports, "state", lambda port: seen.append(port) or states[port])
    assert ports.select(choose=lambda candidates, count: candidates[:count]) == 21002
    assert seen == [21000, 21001, 21002]
    monkeypatch.setattr(ports, "state", lambda port: "in-use")
    with pytest.raises(RuntimeError, match="No free local address"):
        ports.select(attempts=3)


def test_default_selection_is_random_within_the_pool(monkeypatch):
    monkeypatch.setattr(ports, "ranges", lambda: (None, None))
    monkeypatch.setattr(ports, "state", lambda port: "free")
    chosen = {ports.select() for _ in range(24)}
    assert len(chosen) > 12 and chosen <= set(ports.PREFERRED)


@pytest.mark.parametrize("listening,refusal,expected", [
    (True, None, "in-use"),      # The listener table decides first.
    (False, None, "free"),
    (False, 10013, "reserved"),  # Access denied with no listener: a Windows reservation.
    (False, 10048, "in-use"),    # Address in use: a program holds it without listening yet.
    (False, 98, "in-use"),
])
def test_port_state_separates_a_program_from_a_windows_reservation(monkeypatch, listening, refusal, expected):
    monkeypatch.setattr(ports, "_listening", lambda port: listening)
    monkeypatch.setattr(ports, "_bind", lambda port: refusal if not listening else pytest.fail("no bind needed"))
    # A shortcut click must not start netsh to classify its saved port.
    monkeypatch.setattr(ports, "ranges", lambda: pytest.fail("state reads no ranges"))
    assert ports.state(23456) == expected


@pytest.mark.parametrize("value", [0, 65536, "23456", True, None])
def test_port_state_requires_a_port_number(value):
    with pytest.raises(ValueError):
        ports.state(value)


def test_native_listener_table_decides_before_any_connection_or_bind(monkeypatch):
    from amplifier_web import windows_task_runtime
    monkeypatch.setattr(ports.os, "name", "nt")
    calls = []
    monkeypatch.setattr(windows_task_runtime, "listener_owners",
                        lambda host, port: calls.append((host, port)) or {123})
    monkeypatch.setattr(ports, "_bind", lambda port: pytest.fail("A bind refusal cannot establish a listener"))
    assert ports.state(8953) == "in-use"
    assert calls == [("127.0.0.1", 8953)]


def test_test_bind_is_released_and_reports_a_real_conflict():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert ports._bind(port) is not None
    assert ports._bind(port) is None
    assert ports._bind(port) is None  # The filter itself holds nothing afterwards.


native = pytest.mark.skipif(os.name != "nt", reason="Requires native Windows TCP configuration")
REQUIRED = os.environ.get("AMPLIFIER_REQUIRE_PORT_RESERVATION_CHECK") == "1"


def unavailable(reason):
    """An elevated lane must run these cases; elsewhere they need an administrator."""
    if REQUIRED:
        pytest.fail("This lane must exercise a real Windows port reservation: " + reason)
    pytest.skip(reason)


@native
def test_native_ranges_are_read_from_this_computer():
    dynamic, reserved = ports.ranges()
    assert dynamic is not None and 1024 <= dynamic[0] <= dynamic[1] <= 65535
    assert isinstance(reserved, list)


@native
def test_native_selection_is_outside_this_computers_ranges_and_free():
    dynamic, reserved = ports.ranges()
    port = ports.select()
    assert not dynamic[0] <= port <= dynamic[1]
    assert not any(start <= port <= end for start, end in reserved)
    assert ports.state(port) == "free"


@native
def test_native_second_installation_never_selects_a_listening_first_one():
    first = ports.select()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as host:
        host.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        host.bind(("127.0.0.1", first))  # The real listener claims the selected port.
        host.listen(8)
        assert ports.state(first) == "in-use"
        # Even when the random choice offers the same port first.
        offered = lambda candidates, count: [first, *[port for port in candidates if port != first][:count - 1]]
        second = ports.select(choose=offered)
        assert second != first and ports.state(second) == "free"
    assert ports.state(first) == "free"  # Stopping the host releases it; nothing was adopted.


@native
def test_native_port_reservation_is_reported_as_reserved_not_as_a_program():
    """A real Windows port reservation refuses the bind although no program listens.

    This is the mechanism behind Hyper-V and WSL reservations. Creating one
    needs an elevated token (hosted CI has one). It covers one selected free
    port and is deleted in this test.
    """
    api = ctypes.WinDLL("iphlpapi", use_last_error=True)
    create, delete = api.CreatePersistentTcpPortReservation, api.DeletePersistentTcpPortReservation
    create.argtypes = [ctypes.c_ushort, ctypes.c_ushort, ctypes.POINTER(ctypes.c_uint64)]
    delete.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
    create.restype = delete.restype = ctypes.c_ulong
    port = ports.select()
    token = ctypes.c_uint64()
    result = create(socket.htons(port), 1, ctypes.byref(token))  # Network byte order.
    if result != 0:
        unavailable(f"CreatePersistentTcpPortReservation returned {result}")
    try:
        assert not ports._listening(port)
        assert ports._bind(port) == 10013
        assert ports.state(port) == "reserved"
        offered = lambda candidates, count: [port, *[other for other in candidates if other != port][:count - 1]]
        assert ports.select(choose=offered) != port  # Selection never returns a reserved port.
    finally:
        removed = delete(socket.htons(port), 1)
    assert removed == 0
    assert ports.state(port) == "free"


@native
def test_native_listed_exclusion_is_parsed_from_real_netsh_output_and_avoided():
    """Windows lists an administered exclusion; selection stays out of it."""
    netsh = os.path.join(os.environ["SystemRoot"], "System32", "netsh.exe")
    port = ports.select()
    scope = ["protocol=tcp", f"startport={port}", "numberofports=1", "store=active"]
    added = subprocess.run([netsh, "int", "ipv4", "add", "excludedportrange", *scope],
                           capture_output=True, timeout=30, check=False)
    if added.returncode != 0:
        unavailable("netsh could not add a temporary excluded port range")
    try:
        dynamic, reserved = ports.ranges()
        assert (port, port) in reserved
        assert port not in ports.pool(dynamic, reserved)
    finally:
        removed = subprocess.run([netsh, "int", "ipv4", "delete", "excludedportrange", *scope],
                                 capture_output=True, timeout=30, check=False)
    assert removed.returncode == 0
    assert (port, port) not in ports.ranges()[1]
