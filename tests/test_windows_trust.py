"""Windows trust for the key-less local root.

Portable cases use fakes for the store. Native cases call the real crypt32
API on a private throwaway store and ask real Windows verifiers about the
certificate shape. The Windows confirmation itself is clean-room evidence.
"""
from contextlib import contextmanager
import ctypes
from datetime import UTC, datetime, timedelta
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
from types import SimpleNamespace
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from amplifier_web import deployment, windows_tls as wt, windows_trust as trust

CONFIG = deployment.validate_server({})
DER = serialization.Encoding.DER


def identity(config=CONFIG):
    """A product-shaped identity; the TEST keeps the signing key for variants."""
    root, key = wt._new_root(config)
    leaf, leaf_key = wt._new_local_leaf(root, key, config)
    return root, key, leaf, leaf_key


def variant_root(*, name=wt.ROOT_NAME, path_length=0, permitted=None, excluded=None, days=825,
                 purposes=(ExtendedKeyUsageOID.SERVER_AUTH,), constraints_critical=True, constrained=True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=path_length), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if purposes:
        builder = builder.add_extension(x509.ExtendedKeyUsage(list(purposes)), critical=False)
    if constrained:
        permitted = permitted if permitted is not None else [
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")),
            x509.IPAddress(ipaddress.ip_network("::1/128"))]
        builder = builder.add_extension(
            x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=excluded),
            critical=constraints_critical)
    return builder.sign(key, hashes.SHA256()), key


def variant_leaf(root, key, *, names=None, subject=(), ca=False):
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = names if names is not None else [
        x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1"))]
    now = datetime.now(UTC)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name(list(subject))).issuer_name(root.subject)
        .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(names), critical=True)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256()))
    return leaf, leaf_key


def test_thumbprint_uses_the_grouping_windows_shows():
    assert trust.thumbprint(b"amplifier") == " ".join(
        hashlib.sha1(b"amplifier").hexdigest().upper()[index:index + 8] for index in range(0, 40, 8))
    assert len(trust.thumbprint(b"x").split()) == 5


def test_check_accepts_exactly_the_identity_setup_creates():
    root, _, leaf, _ = identity()
    trust.check(root.public_bytes(DER), leaf.public_bytes(DER), CONFIG)
    wider = deployment.validate_server({"public_origins": ["https://pc.example.ts.net"]})
    root, _, leaf, _ = identity(wider)
    trust.check(root.public_bytes(DER), leaf.public_bytes(DER), wider)
    # The same root is refused for an installation that never configured that address.
    with pytest.raises(ValueError, match="not a key-less local-only identity"):
        trust.check(root.public_bytes(DER), leaf.public_bytes(DER), CONFIG)


@pytest.mark.parametrize("change", [
    {"constrained": False},                      # An ordinary CA, as earlier setups made.
    {"constraints_critical": False},
    {"path_length": None}, {"path_length": 1},
    {"name": "Some Other Authority"},
    {"days": 3650},
    {"purposes": ()},
    {"purposes": (ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CODE_SIGNING)},
    {"permitted": [x509.DNSName("com")]},
    {"permitted": [x509.DNSName("localhost"), x509.DNSName("example.com")]},
    {"permitted": [x509.IPAddress(ipaddress.ip_network("127.0.0.0/8"))]},
    {"permitted": [x509.IPAddress(ipaddress.ip_network("0.0.0.0/0"))]},
    {"permitted": [x509.DNSName("localhost"), x509.RFC822Name("person@example.com")]},
    {"excluded": [x509.DNSName("example.com")]},
])
def test_check_refuses_every_root_that_is_not_local_only(change):
    root, key = variant_root(**change)
    leaf, _ = variant_leaf(root, key, names=[x509.DNSName("localhost")])
    with pytest.raises(ValueError, match="Windows was not asked to trust it"):
        trust.check(root.public_bytes(DER), leaf.public_bytes(DER), CONFIG)


@pytest.mark.parametrize("change", [
    {"names": [x509.DNSName("localhost"), x509.DNSName("amplifier-outside.example")]},
    {"names": [x509.IPAddress(ipaddress.ip_address("127.0.0.2"))]},
    {"names": [x509.DNSName("localhost"), x509.RFC822Name("person@example.com")]},
    {"subject": [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]},
    {"ca": True},
])
def test_check_refuses_a_server_certificate_outside_the_roots_names(change):
    root, key = variant_root()
    trust.check(root.public_bytes(DER), variant_leaf(root, key)[0].public_bytes(DER), CONFIG)
    with pytest.raises(ValueError, match="Windows was not asked to trust it"):
        trust.check(root.public_bytes(DER), variant_leaf(root, key, **change)[0].public_bytes(DER), CONFIG)


def test_check_refuses_a_server_certificate_from_another_root():
    root, _ = variant_root()
    other, other_key = variant_root()
    with pytest.raises(ValueError, match="Windows was not asked to trust it"):
        trust.check(root.public_bytes(DER), variant_leaf(other, other_key)[0].public_bytes(DER), CONFIG)


@pytest.fixture
def saved(monkeypatch):
    root, _, leaf, _ = identity()
    pair = (root.public_bytes(DER), leaf.public_bytes(DER))
    state = {"identity": pair}
    monkeypatch.setattr(wt, "keyless_identity", lambda home: state["identity"])
    monkeypatch.setattr(deployment, "load_server_config", lambda home: CONFIG)
    pin = SimpleNamespace(fingerprint=hashlib.sha256(pair[1]).digest())
    return state, pair, pin


def test_candidate_is_the_exact_fresh_root_this_process_created(saved):
    _, (root, _), pin = saved
    fresh = bytes(bytearray(root))  # Equal bytes, a distinct object.
    assert trust.candidate("home", pin, fresh) is fresh
    # A later start has no in-memory copy and uses the owner-only store.
    assert trust.candidate("home", pin) == root


def test_candidate_refuses_a_saved_root_that_differs_from_the_fresh_one(saved):
    state, (root, leaf), pin = saved
    other, _ = variant_root()
    with pytest.raises(ValueError, match="changed"):
        trust.candidate("home", pin, other.public_bytes(DER))
    with pytest.raises(ValueError, match="changed"):
        trust.candidate("home", SimpleNamespace(fingerprint=b"\0" * 32), root)


def test_candidate_never_offers_an_identity_with_a_signing_key_on_disk(saved):
    state, (root, _), pin = saved
    state["identity"] = None
    assert trust.candidate("home", pin) is None
    assert trust.candidate("home", pin, root) is None


def test_candidate_checks_the_shape_of_a_saved_root(saved):
    state, _, _ = saved
    root, key = variant_root(permitted=[x509.DNSName("com")])
    leaf, _ = variant_leaf(root, key, names=[x509.DNSName("localhost")])
    state["identity"] = (root.public_bytes(DER), leaf.public_bytes(DER))
    pin = SimpleNamespace(fingerprint=hashlib.sha256(leaf.public_bytes(DER)).digest())
    with pytest.raises(ValueError, match="Windows was not asked to trust it"):
        trust.candidate("home", pin)


class Store:
    """The crypt32 calls this module makes, over an in-memory set."""

    def __init__(self, *, answer=True, error=5, keeps=True, openable=True):
        self.held, self.answer, self.error, self.keeps, self.openable = set(), answer, error, keeps, openable
        self.opens, self.questions, self.raised = [], [], []

    @contextmanager
    def open(self, der, store, *, write):
        self.opens.append((store, write))
        if write and not self.openable:
            raise OSError("Access is denied")
        yield self, "store", der

    def CertFindCertificateInStore(self, handle, encoding, flags, kind, context, previous):
        return context if context in self.held else None

    def CertFreeCertificateContext(self, context):
        return True

    def CertAddCertificateContextToStore(self, handle, context, disposition, out):
        assert disposition == 5
        self.questions.append("add")
        if self.answer and self.keeps:
            self.held.add(context)
        return bool(self.answer)

    def CertDeleteCertificateFromStore(self, context):
        self.questions.append("delete")
        if self.answer and self.keeps:
            self.held.discard(context)
        return bool(self.answer)


@pytest.fixture
def store(monkeypatch):
    def make(**options):
        fake = Store(**options)
        monkeypatch.setattr(trust, "_open", fake.open)
        monkeypatch.setattr(trust, "_find", lambda api, handle, context:
                            api.CertFindCertificateInStore(handle, 0, 0, 0, context, None))
        monkeypatch.setattr(trust, "_raise_dialogs", lambda done: fake.raised.append(done.wait(5)))
        monkeypatch.setattr(trust.C, "get_last_error", lambda: fake.error, raising=False)
        return fake
    return make


def test_add_asks_windows_once_and_confirms_the_store_holds_the_root(store):
    fake = store()
    assert trust.add_root(b"root") == "added"
    assert fake.questions == ["add"] and fake.raised == [True] and b"root" in fake.held
    assert fake.opens == [("ROOT", True), ("ROOT", False)]
    # Already trusted: no question, no window to raise.
    assert trust.add_root(b"root") == "present"
    assert fake.questions == ["add"] and fake.raised == [True]


@pytest.mark.parametrize("options,expected", [
    ({"answer": False, "error": 1223}, "declined"),   # The person selected No.
    ({"answer": False, "error": 5}, "blocked"),       # Windows refused.
    ({"keeps": False}, "blocked"),                    # Yes, but the store does not hold it.
    ({"openable": False}, "blocked"),
])
def test_add_reports_no_and_refusal_separately_and_never_claims_trust(store, options, expected):
    fake = store(**options)
    assert trust.add_root(b"root") == expected
    assert b"root" not in fake.held


def test_remove_reports_each_outcome_and_handles_the_default_no(store):
    fake = store()
    assert trust.remove_root(b"root") == "absent" and fake.questions == []
    fake.held.add(b"root")
    assert trust.remove_root(b"root") == "removed" and b"root" not in fake.held
    assert fake.questions == ["delete"] and fake.raised == [True]
    # The default button of the Windows question is No: the root simply stays.
    fake = store(answer=False, error=1223)
    fake.held.add(b"root")
    assert trust.remove_root(b"root") == "declined" and b"root" in fake.held
    fake = store(answer=False, error=5)
    fake.held.add(b"root")
    assert trust.remove_root(b"root") == "blocked"
    fake = store(keeps=False)
    fake.held.add(b"root")
    assert trust.remove_root(b"root") == "blocked"


def test_store_calls_require_native_windows(monkeypatch):
    monkeypatch.setattr(trust.os, "name", "posix")
    assert trust.user_roots_disabled() is False
    with pytest.raises(RuntimeError, match="native Windows"):
        trust.contains(b"root")


native = pytest.mark.skipif(os.name != "nt", reason="Requires the native Windows certificate APIs")


@native
def test_native_store_add_find_remove_on_a_private_throwaway_store():
    """The real crypt32 calls, on a store that is not a trust store (no question)."""
    name = "AmplifierUnifiedTest" + uuid.uuid4().hex
    root, _, _, _ = identity()
    der = root.public_bytes(DER)
    other = variant_root()[0].public_bytes(DER)
    try:
        assert trust.contains(der, store=name) is False
        assert trust.remove_root(der, store=name) == "absent"
        assert trust.add_root(der, store=name) == "added"
        assert trust.contains(der, store=name) is True
        assert trust.contains(other, store=name) is False  # The exact certificate, not a name match.
        assert trust.add_root(der, store=name) == "present"
        assert trust.remove_root(other, store=name) == "absent"
        assert trust.remove_root(der, store=name) == "removed"
        assert trust.contains(der, store=name) is False
    finally:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        crypt32.CertUnregisterSystemStore.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong]
        crypt32.CertUnregisterSystemStore(name, 0x00010000 | 0x10)  # Current user; delete.


@native
def test_native_confirmation_window_is_found_and_brought_in_front():
    """A real dialog-class window of this process; no person and no trust store."""
    from ctypes import wintypes as W
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    user.CreateWindowExW.restype = W.HWND
    user.GetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int]
    user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user.DestroyWindow.argtypes = [W.HWND]
    window = user.CreateWindowExW(0, "#32770", "Amplifier test window", 0x80000000 | 0x10000000,
                                  40, 40, 240, 120, None, None, None, None)  # Popup, visible.
    if not window:
        if os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_TRUST_CHECKS") == "1":
            pytest.fail(f"This lane must be able to create a window (error {ctypes.get_last_error()})")
        pytest.skip("No interactive desktop for a test window")
    try:
        assert not user.GetWindowLongPtrW(window, -20) & 0x8  # Extended style; topmost.
        assert window in trust._dialog_windows()
        trust._bring_forward(window)
        assert user.GetWindowLongPtrW(window, -20) & 0x8
    finally:
        user.DestroyWindow(window)
    assert window not in trust._dialog_windows()
    # The helper thread stops at once when the Windows question is answered.
    done = threading.Event()
    done.set()
    helper = threading.Thread(target=trust._raise_dialogs, args=(done,), daemon=True)
    helper.start()
    helper.join(5)
    assert not helper.is_alive()


@native
def test_native_product_root_is_not_trusted_before_the_person_says_yes():
    root, _, _, _ = identity()
    assert trust.contains(root.public_bytes(DER)) is False  # Read-only; asks nothing.
    assert isinstance(trust.user_roots_disabled(), bool)


CHAIN_SCRIPT = r"""
param([string]$Root, [string]$Leaf)
$ErrorActionPreference = 'Stop'
$rootCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($Root)
$leafCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($Leaf)
$chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
$chain.ChainPolicy.TrustMode = [System.Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
[void]$chain.ChainPolicy.CustomTrustStore.Add($rootCertificate)
$chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
[void]$chain.ChainPolicy.ApplicationPolicy.Add([System.Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
$built = $chain.Build($leafCertificate)
@{ built = $built; status = @($chain.ChainStatus | ForEach-Object { $_.Status.ToString() }) } | ConvertTo-Json -Compress
"""


def windows_chain(directory, root, leaf):
    """Ask Windows CryptoAPI (through .NET) to build the chain to this root only."""
    shell = shutil.which("pwsh")
    if shell is None:
        if os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_TRUST_CHECKS") == "1":
            pytest.fail("This lane must have PowerShell 7 for the Windows chain check")
        pytest.skip("PowerShell 7 is not installed")
    (directory / "chain.ps1").write_text(CHAIN_SCRIPT, encoding="ascii")
    (directory / "root.cer").write_bytes(root.public_bytes(DER))
    (directory / "leaf.cer").write_bytes(leaf.public_bytes(DER))
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(directory / "chain.ps1"),
         str(directory / "root.cer"), str(directory / "leaf.cer")],
        capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture
def private_directory():
    root = Path.home() / (".unified-trust-test-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


@native
def test_native_windows_accepts_the_product_shape_and_enforces_the_roots_names(private_directory):
    root, key, leaf, _ = identity()
    accepted = windows_chain(private_directory, root, leaf)
    print("Windows chain, product identity:", accepted)
    assert accepted == {"built": True, "status": []}
    # Measured, not assumed: does Windows apply a ROOT's name constraints?
    for name in (x509.DNSName("amplifier-outside.example"), x509.IPAddress(ipaddress.ip_address("127.0.0.2"))):
        refused = windows_chain(private_directory, root, variant_leaf(root, key, names=[name])[0])
        print("Windows chain, name outside the root's constraints:", name.value, refused)
        assert refused["built"] is False
        assert any("NameConstraint" in flag for flag in refused["status"])


class Page(threading.Thread):
    """One HTTPS listener that answers every request with a marker page."""

    def __init__(self, directory, address, leaf, key, marker):
        super().__init__(daemon=True)
        cert_bytes, key_bytes = wt._encoded_pair(leaf, key)
        (directory / (marker + ".crt")).write_bytes(cert_bytes)
        (directory / (marker + ".key")).write_bytes(key_bytes)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(directory / (marker + ".crt"), directory / (marker + ".key"))
        self.listener = socket.create_server((address, 0))
        self.listener.settimeout(0.25)
        self.address, self.port = address, self.listener.getsockname()[1]
        self.marker, self.stopped, self.handshakes = marker, threading.Event(), []

    def run(self):
        body = ("<!doctype html><title>t</title><p>" + self.marker + "</p>").encode()
        reply = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                 + str(len(body)).encode() + b"\r\n\r\n" + body)
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                client.settimeout(5)
                with self.context.wrap_socket(client, server_side=True) as secure:
                    self.handshakes.append("ok")
                    secure.recv(65536)
                    secure.sendall(reply)
            except (OSError, ssl.SSLError) as error:
                self.handshakes.append(type(error).__name__)

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.join(5)


def edge_dom(directory, url):
    edge = next((path for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")) if base
                 for path in [Path(base) / "Microsoft/Edge/Application/msedge.exe"] if path.is_file()), None)
    assert edge is not None, "Microsoft Edge is required on this lane"
    profile = directory / ("edge-" + uuid.uuid4().hex)
    try:
        result = subprocess.run(
            [str(edge), "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + str(profile), "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("Edge did not finish loading " + url)
    return result.stdout


def test_edge_check_cannot_be_silently_skipped_on_the_lane_that_requires_it():
    # The required lane sets both variables; a missing opt-in there is a failure.
    if os.environ.get("AMPLIFIER_REQUIRE_WINDOWS_TRUST_CHECKS") == "1":
        assert os.name == "nt" and os.environ.get("AMPLIFIER_TEST_EDGE_WITH_MACHINE_ROOT") == "1"


@native
@pytest.mark.skipif(os.environ.get("AMPLIFIER_TEST_EDGE_WITH_MACHINE_ROOT") != "1",
                    reason="Changes the machine root store; only a disposable CI runner opts in")
def test_native_edge_accepts_the_product_shape_and_refuses_a_name_outside_it(private_directory):
    """Edge's own verifier (Windows root store plus Chromium rules).

    Only a disposable, elevated CI runner opts in: the test root goes into the
    MACHINE store there, because a hosted runner cannot answer the question
    Windows asks for the person's own store. The product never uses that
    store. Chromium treats both stores with the same rules. The root's key
    exists only in this test process; the root is removed in every case.
    """
    root, key, leaf, leaf_key = identity()
    outside = variant_leaf(root, key, names=[x509.IPAddress(ipaddress.ip_address("127.0.0.2"))])
    (private_directory / "root.cer").write_bytes(root.public_bytes(DER))
    thumbprint = root.fingerprint(hashes.SHA1()).hex()
    certutil = str(Path(os.environ["SystemRoot"]) / "System32" / "certutil.exe")
    good = Page(private_directory, "127.0.0.1", leaf, leaf_key, "amplifier-product-shape-accepted")
    bad = Page(private_directory, "127.0.0.2", *outside, "amplifier-outside-name-accepted")
    good.start()
    bad.start()
    try:
        # Before trust: Edge refuses the product certificate as well.
        assert good.marker not in edge_dom(private_directory, f"https://127.0.0.1:{good.port}/")
        added = subprocess.run([certutil, "-f", "-addstore", "Root", str(private_directory / "root.cer")],
                               capture_output=True, text=True, timeout=60, check=False)
        assert added.returncode == 0, added.stdout[-1000:]
        try:
            accepted = edge_dom(private_directory, f"https://127.0.0.1:{good.port}/")
            refused = edge_dom(private_directory, f"https://127.0.0.2:{bad.port}/")
        finally:
            removed = subprocess.run([certutil, "-delstore", "Root", thumbprint],
                                     capture_output=True, text=True, timeout=60, check=False)
        assert removed.returncode == 0, removed.stdout[-1000:]
        print("Edge handshakes, product shape:", good.handshakes, "| outside name:", bad.handshakes)
        assert good.marker in accepted and "ok" in good.handshakes
        # The same trusted root must not vouch for a name outside its constraints.
        # Edge reached that listener and ended the handshake itself; a page
        # that never connected would prove nothing.
        assert bad.marker not in refused
        assert bad.handshakes and "ok" not in bad.handshakes
    finally:
        good.close()
        bad.close()
