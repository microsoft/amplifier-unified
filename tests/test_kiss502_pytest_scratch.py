"""The Linux KISS502 guest saves reports even when a pytest stage fails."""
from pathlib import Path
import sys

import pytest

from scripts import kiss502_checks


@pytest.mark.skipif(sys.platform != "linux", reason="KISS502 runs in a Linux guest")
def test_pytest_scratch_is_retired_outside_saved_outputs_on_failure(tmp_path):
    output = tmp_path / "artifacts"
    output.mkdir()
    scratch = {}
    reports = {}

    def stage(name, argv, *, cwd, env, required=True):
        temp = Path(env["TMPDIR"])
        assert temp.is_dir() and not temp.is_relative_to(output)
        scratch[name] = temp
        if "--basetemp" in argv:
            base = Path(argv[argv.index("--basetemp") + 1])
            assert base.is_relative_to(temp)
            base.mkdir()
            (base / "fixture").touch()
            report = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml=")))
            assert report.parent == output
            report.touch()
            reports[name] = report
        if name == "pytest":
            assert Path(env["AMPLIFIER_TEST_ARTIFACTS"]) == output / "native"
        if name == "owner-pytest":
            raise OSError("interrupted subprocess")

    with pytest.raises(OSError, match="interrupted subprocess"):
        kiss502_checks.run_pytests(stage, sys.executable, tmp_path, output,
                                   {"TMPDIR": str(output)}, ("test_app_guidance",))
    assert set(scratch) == {"source-census", "pytest", "owner-pytest"}
    assert all(not path.exists() for path in scratch.values())
    assert set(path.name for path in reports.values()) == {"junit.xml", "owner-junit.xml"}
    assert set(output.iterdir()) == set(reports.values())
