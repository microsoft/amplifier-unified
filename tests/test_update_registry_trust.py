"""Which server certificates the registry lookup of the update check accepts.

Real TLS against a local HTTPS server with a private root. Nothing here uses
the network. The lookup must accept a root from this system's store or from
the bundled roots, and nothing else; certificate and host-name checks stay on.

Synchronous tests on purpose: the focused native Windows lane has no async
pytest plugin.
"""
import asyncio
import datetime
import hashlib
import http.server
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading

import aiohttp
import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from amplifier_web import app_component_graph, update_checks
from amplifier_web.update_checks import CheckCache, RegistryLookupFailure, checking

PUBLISHED = '2.0.3'


def _name(common):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def _usage(**enabled):
    names = ('digital_signature', 'content_commitment', 'key_encipherment', 'data_encipherment',
             'key_agreement', 'key_cert_sign', 'crl_sign', 'encipher_only', 'decipher_only')
    return x509.KeyUsage(**{name: bool(enabled.get(name)) for name in names})


def _validity(builder):
    now = datetime.datetime.now(datetime.timezone.utc)
    return builder.not_valid_before(now - datetime.timedelta(hours=1)).not_valid_after(now + datetime.timedelta(days=1))


class Authority:
    """A private root that no system store and no bundle holds."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.key = ec.generate_private_key(ec.SECP256R1())
        subject = _name('Amplifier test registry root ' + os.urandom(6).hex())
        self.certificate = (_validity(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(self.key.public_key()).serial_number(x509.random_serial_number()))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(_usage(key_cert_sign=True, crl_sign=True), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256()))
        self.pem = self.certificate.public_bytes(serialization.Encoding.PEM)
        self.der = self.certificate.public_bytes(serialization.Encoding.DER)
        self.thumbprint = hashlib.sha1(self.der).hexdigest()

    def leaf(self, names):
        """Key and certificate files of a server certificate for these names."""
        key = ec.generate_private_key(ec.SECP256R1())
        alternatives = [x509.IPAddress(ipaddress.ip_address(name)) if name[0].isdigit() else x509.DNSName(name)
                        for name in names]
        certificate = (_validity(x509.CertificateBuilder().subject_name(_name(names[0]))
                .issuer_name(self.certificate.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number()))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_usage(digital_signature=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256()))
        stem = self.directory / ('leaf-' + os.urandom(4).hex())
        key_file, certificate_file = stem.with_suffix('.key'), stem.with_suffix('.pem')
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return key_file, certificate_file

    def bundle(self):
        """A stand-in for the bundled roots that holds only this root."""
        path = self.directory / 'bundled-roots.pem'
        path.write_bytes(self.pem)
        return str(path)


class Registry:
    """A local HTTPS registry. It counts connection attempts and answered requests."""

    def __init__(self, authority, names=('127.0.0.1', 'localhost'), *, status=200, content_type='application/json'):
        key_file, certificate_file = authority.leaf(list(names))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate_file, key_file)
        self.connections = self.answers = 0
        registry = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'  # Keep-alive: an accepted connection stays open for reuse.

            def do_GET(self):
                registry.answers += 1
                body = json.dumps({'info': {'version': PUBLISHED}}).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                connection, address = self.socket.accept()
                registry.connections += 1
                connection.settimeout(10)
                # A refused handshake is an OSError: the server drops that client.
                return registry.context.wrap_socket(connection, server_side=True), address

            def handle_error(self, request, client_address):
                pass

        self.server = Server(('127.0.0.1', 0), Handler)
        self.url = 'https://127.0.0.1:%d/pypi/{}/json' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


@pytest.fixture
def authority(tmp_path):
    return Authority(tmp_path)


@pytest.fixture
def registry(authority, monkeypatch):
    with Registry(authority) as value:
        monkeypatch.setattr(update_checks, 'REGISTRY_JSON', value.url)
        yield value


def lookups(directory):
    """The result of each of the three call paths of the update check."""
    async def run():
        cache = CheckCache(directory)
        results = {}

        async def record(name, call):
            try:
                results[name] = await call()
            except Exception as error:  # noqa: BLE001 -- the test compares exact classes below
                results[name] = error
        try:
            await record('cache', lambda: cache.index('amplifier-core'))
            await record('unscoped', lambda: app_component_graph.index_latest('amplifier-core'))

            async def scoped():
                async with checking(cache):
                    return await update_checks.index_latest('amplifier-core', None)
            await record('scoped', scoped)
        finally:
            await cache.close()
        return results
    return asyncio.run(run())


def refused(results, reason='certificate'):
    assert isinstance(results['cache'], ssl.SSLCertVerificationError), results['cache']
    for path in ('unscoped', 'scoped'):
        assert isinstance(results[path], RegistryLookupFailure), (path, results[path])
        assert results[path].reason == reason
        assert str(results[path]) == 'Component release could not be checked'  # No endpoint or error text.
    return True


def test_trust_keeps_verification_on_and_holds_every_bundled_root():
    context = update_checks.registry_trust()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    bundled = ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True)
    assert len(bundled) > 50 and set(bundled) <= set(context.get_ca_certs(binary_form=True))


def test_a_root_that_neither_the_store_nor_the_bundle_holds_is_refused(registry, tmp_path):
    assert refused(lookups(tmp_path / 'cache'))
    assert registry.connections == 3 and registry.answers == 0  # Three refused handshakes, no retry.


def test_a_root_in_the_bundled_roots_alone_is_accepted(registry, authority, tmp_path, monkeypatch):
    # The defect on a new Windows PC: the system store does not hold the root.
    monkeypatch.setattr(certifi, 'where', authority.bundle)
    assert lookups(tmp_path / 'cache') == {'cache': PUBLISHED, 'unscoped': PUBLISHED, 'scoped': PUBLISHED}
    assert registry.answers == 3


def test_a_root_in_the_system_trust_alone_is_accepted(registry, authority, tmp_path, monkeypatch):
    # The other half of the union: the bundled roots do not hold this root.
    file = tmp_path / 'system-root.pem'
    file.write_bytes(authority.pem)
    monkeypatch.setenv('SSL_CERT_FILE', str(file))  # The system default that OpenSSL reads.
    assert authority.der not in ssl.create_default_context(cafile=certifi.where()).get_ca_certs(binary_form=True)
    assert lookups(tmp_path / 'cache') == {'cache': PUBLISHED, 'unscoped': PUBLISHED, 'scoped': PUBLISHED}
    assert registry.answers == 3


def test_a_certificate_for_another_host_is_refused(authority, tmp_path, monkeypatch):
    monkeypatch.setattr(certifi, 'where', authority.bundle)  # The root itself is trusted.
    with Registry(authority, names=('registry.other.invalid',)) as other:
        monkeypatch.setattr(update_checks, 'REGISTRY_JSON', other.url)
        assert refused(lookups(tmp_path / 'cache'))
        assert other.answers == 0


def test_no_request_is_sent_when_the_roots_cannot_be_loaded(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(certifi, 'where', lambda: str(tmp_path / 'absent-roots.pem'))
    results = lookups(tmp_path / 'cache')
    for path in ('cache', 'unscoped', 'scoped'):
        assert isinstance(results[path], RegistryLookupFailure) and results[path].reason == 'roots_unavailable'
    assert registry.connections == 0  # No weaker request instead.


@pytest.mark.parametrize('status,content_type', [(503, 'application/json'), (200, 'text/plain')])
def test_an_answer_that_cannot_be_used_is_not_reported_as_a_connection_fault(
        authority, tmp_path, monkeypatch, status, content_type):
    # Through the real request path: the site answered over a verified connection.
    monkeypatch.setattr(certifi, 'where', authority.bundle)
    with Registry(authority, status=status, content_type=content_type) as answered:
        monkeypatch.setattr(update_checks, 'REGISTRY_JSON', answered.url)
        results = lookups(tmp_path / 'cache')
        assert isinstance(results['cache'], aiohttp.ClientResponseError), results['cache']
        for path in ('unscoped', 'scoped'):
            assert isinstance(results[path], RegistryLookupFailure), (path, results[path])
            assert results[path].reason == 'invalid_response'
        assert answered.answers == 3


def test_each_failure_class_gets_the_reason_that_fits():
    import aiohttp
    from types import SimpleNamespace

    key = SimpleNamespace(host='pypi.org', port=443, ssl=True)
    answered = aiohttp.ClientResponseError(SimpleNamespace(real_url='x'), (), status=503)
    cases = [
        (aiohttp.ClientConnectorCertificateError(key, ssl.SSLCertVerificationError(1, 'x')), 'certificate'),
        (ssl.SSLCertVerificationError(1, 'x'), 'certificate'),
        (TimeoutError(), 'timeout'),
        (answered, 'invalid_response'),  # The site answered; the connection is not the problem.
        (aiohttp.ClientConnectorError(key, OSError(101, 'x')), 'unavailable'),
        (OSError(101, 'x'), 'unavailable'),
        (KeyError('info'), 'invalid_response'),
        (ValueError('Unsupported component release version'), 'invalid_response'),
        (RegistryLookupFailure('roots_unavailable'), 'roots_unavailable'),
    ]
    for error, reason in cases:
        assert update_checks.lookup_failure_reason(error) == reason, error
    assert RegistryLookupFailure('not-a-reason').reason == 'invalid_response'


def test_trust_lost_after_a_success_is_refused_and_the_saved_success_is_not_reused(
        registry, authority, tmp_path, monkeypatch):
    """One live client: neither its open connection nor the saved result outlives the trust.

    The order matters: the person's check comes first and removes the saved
    result. An automatic check that runs first, inside the saved result's
    lifetime, may reuse the earlier value, which a verified connection gave.
    That is the cache's design and is not a case of this test.
    """
    bundled = certifi.where()
    directory = tmp_path / 'cache'

    async def scoped(cache, *, fresh):
        try:
            async with checking(cache, fresh=fresh):
                return await update_checks.index_latest('amplifier-core', None)
        except Exception as error:  # noqa: BLE001 -- the test compares exact classes below
            return error

    async def run():
        cache = CheckCache(directory)
        try:
            monkeypatch.setattr(certifi, 'where', authority.bundle)
            trusted = await scoped(cache, fresh=True)
            saved = [entry['value'] for entry in json.loads(cache.path.read_text())['entries'].values()]
            monkeypatch.setattr(certifi, 'where', lambda: bundled)  # The root is not trusted any more.
            person = await scoped(cache, fresh=True)       # A person selects Check for updates.
            automatic = await scoped(cache, fresh=False)   # The app's own check may reuse saved successes.
        finally:
            await cache.close()
        reopened_cache = CheckCache(directory)             # The app after a restart.
        try:
            reopened = await scoped(reopened_cache, fresh=False)
            left = list(reopened_cache.entries.values())
        finally:
            await reopened_cache.close()
        return trusted, saved, person, automatic, reopened, left

    trusted, saved, person, automatic, reopened, left = asyncio.run(run())
    assert trusted == PUBLISHED and saved == [PUBLISHED]
    for later in (person, automatic, reopened):
        assert isinstance(later, RegistryLookupFailure) and later.reason == 'certificate', later
    assert left == []  # No saved success is left behind.
    # One answered request in all; each later lookup made its own refused handshake.
    assert registry.answers == 1 and registry.connections == 4


@pytest.mark.parametrize('reason', sorted(update_checks.LOOKUP_REASONS))
def test_each_reason_has_plain_words_and_a_next_step(reason):
    for text in (update_checks.lookup_guidance(reason), update_checks.lookup_guidance(reason, application=True)):
        assert 'pypi.org' in text and text.endswith(update_checks._LOOKUP_NEXT[reason])  # The next step itself.
        assert not any(word in text for word in ('Error', 'Exception', 'errno', 'ssl', 'SSL', 'aiohttp'))
    assert 'The installed version is kept.' in update_checks.lookup_guidance(reason)
    assert update_checks.lookup_guidance(reason, application=True).startswith('The newest app release is installed.')
    assert update_checks.lookup_guidance('not-a-reason') == update_checks.lookup_guidance('invalid_response')


@pytest.mark.skipif(os.name != 'nt', reason='Requires the native Windows certificate store')
def test_native_windows_store_root_is_accepted_without_a_restart(registry, authority, tmp_path):
    """A root that enters the Windows store while the app runs is used by the next lookup.

    The test root goes into this disposable runner's MACHINE store (a hosted
    runner cannot answer the question Windows asks for a person's own store)
    and is removed in every case. The bundled roots never hold it.
    """
    required = os.environ.get('AMPLIFIER_REQUIRE_WINDOWS_TRUST_CHECKS') == '1'
    certutil = str(Path(os.environ['SystemRoot']) / 'System32' / 'certutil.exe')
    file = tmp_path / 'test-root.cer'
    file.write_bytes(authority.der)

    async def lookup(cache):
        try:
            return await cache.index('amplifier-core')
        except Exception as error:  # noqa: BLE001
            return error

    async def run():
        cache = CheckCache(tmp_path / 'cache')  # One long-lived cache and HTTP client, as in the app.
        try:
            before = await lookup(cache)
            added = await asyncio.to_thread(subprocess.run, [certutil, '-f', '-addstore', 'Root', str(file)],
                                            capture_output=True, text=True, timeout=60)
            if added.returncode != 0:
                return before, None, added
            return before, await lookup(cache), added
        finally:
            await cache.close()

    try:
        before, after, added = asyncio.run(run())
    finally:
        removed = subprocess.run([certutil, '-delstore', 'Root', authority.thumbprint],
                                 capture_output=True, text=True, timeout=60)
    assert isinstance(before, ssl.SSLCertVerificationError), before  # Absent from the store: refused.
    if added.returncode != 0:
        reason = 'This account cannot change the machine root store (certutil exit %d)' % added.returncode
        if required:
            pytest.fail('This lane must be able to add a test root: ' + reason)
        pytest.skip(reason)
    assert removed.returncode == 0, 'The test root must be removed from the machine store'
    assert after == PUBLISHED, after  # Same process, same client: no restart was needed.
    assert registry.answers == 1
    assert authority.der not in update_checks.registry_trust().get_ca_certs(binary_form=True)  # Removed again.
