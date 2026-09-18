import stat
import pytest

from amplifier_web.cli import _doctor, _setup_tls
from cryptography.hazmat.primitives import hashes


def test_setup_local_ca_is_servable_has_ca_constraint_and_prints_ca_fingerprint(tmp_path, capsys):
    from cryptography import x509
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import ca_bytes, ca_fingerprint, setup_local_ca

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    assert stat.S_IMODE((tmp_path / "config/tls").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "config/tls/ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "config/tls/leaf.key").stat().st_mode) == 0o600
    certificate = ca_bytes(tmp_path)
    assert config["tls"]["method"] == "ca"
    assert certificate
    assert x509.load_pem_x509_certificate(certificate).extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    expected_fingerprint = ":".join(
        f"{byte:02X}" for byte in x509.load_pem_x509_certificate(certificate).fingerprint(hashes.SHA256())
    )
    assert ca_fingerprint(tmp_path) == expected_fingerprint
    _doctor(tmp_path)
    assert f"CA SHA-256 fingerprint: {expected_fingerprint}" in capsys.readouterr().out
    _setup_tls(tmp_path, "status")
    assert f"CA SHA-256 fingerprint: {expected_fingerprint}" in capsys.readouterr().out


def test_setup_tls_reuses_leaf_until_force_is_requested(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    config = load_server_config(tmp_path)
    setup_local_ca(tmp_path, config)
    ca_before = (tmp_path / "config/tls/ca.crt").read_bytes()
    leaf_before = (tmp_path / "config/tls/leaf.crt").read_bytes()
    setup_local_ca(tmp_path, config)
    assert (tmp_path / "config/tls/ca.crt").read_bytes() == ca_before
    assert (tmp_path / "config/tls/leaf.crt").read_bytes() == leaf_before
    setup_local_ca(tmp_path, config, force=True)
    assert (tmp_path / "config/tls/ca.crt").read_bytes() == ca_before
    assert (tmp_path / "config/tls/leaf.crt").read_bytes() != leaf_before


def test_tls_status_rejects_a_missing_leaf(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca, tls_ready

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    assert tls_ready(tmp_path, config)
    (tmp_path / "config/tls/leaf.crt").unlink()
    assert not tls_ready(tmp_path, config)


@pytest.mark.parametrize("existing_directory", [False, True])
def test_tls_status_reads_do_not_create_or_change_tls_state(tmp_path, existing_directory):
    from amplifier_web.tls import ca_bytes, ca_fingerprint, tls_ready

    directory = tmp_path / "config" / "tls"
    if existing_directory:
        directory.mkdir(parents=True)
        directory.chmod(0o750)
    assert ca_bytes(tmp_path) is None
    assert ca_fingerprint(tmp_path) is None
    assert not tls_ready(tmp_path, {"tls": {"method": "ca"}})
    if existing_directory:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750
        assert not list(directory.iterdir())
    else:
        assert not directory.exists()


def test_tls_setup_restricts_existing_directory_even_when_reusing_leaf(tmp_path):
    from amplifier_web.deployment import load_server_config
    from amplifier_web.tls import setup_local_ca

    config = setup_local_ca(tmp_path, load_server_config(tmp_path))
    directory = tmp_path / "config" / "tls"
    directory.chmod(0o750)
    leaf = (directory / "leaf.crt").read_bytes()
    setup_local_ca(tmp_path, config)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert (directory / "leaf.crt").read_bytes() == leaf