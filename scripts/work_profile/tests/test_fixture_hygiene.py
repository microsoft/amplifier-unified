import importlib.util
from pathlib import Path


def test_fixture_cleanup_preserves_sqlite_external_symlinks_and_dependency_caches(
    tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "fixture_hygiene", Path(__file__).resolve().parents[1] / "fixture_hygiene.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    secret = "synthetic-selected-secret-12345"
    run = tmp_path / "run"
    run.mkdir()
    (run / "settings.yaml").write_text("api_key: " + secret)
    (run / "events.json").write_text('{"public":"unchanged"}')
    (run / "app.sqlite3").write_bytes(b"keep-original-" + secret.encode())
    (run / "cache").mkdir()
    (run / "cache" / "module.py").write_text(secret)
    outside = tmp_path / "outside.txt"
    outside.write_text(secret)
    (run / "link.txt").symlink_to(outside)
    report = helper.redact_generated_credentials(run, {"config": {"api_key": secret}})
    assert (
        report["redactedFiles"] == 1
        and report["sqliteMatches"] == 1
        and report["remainingMatches"] == 1
    )
    assert (run / "settings.yaml").read_text() == "api_key: [REDACTED]"
    assert (run / "app.sqlite3").read_bytes() == b"keep-original-" + secret.encode()
    assert (
        outside.read_text() == secret
        and (run / "cache" / "module.py").read_text() == secret
    )
    assert (run / "events.json").read_text() == '{"public":"unchanged"}'
    assert secret not in str(report)
