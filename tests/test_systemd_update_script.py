"""Registry availability must not change or replay the real handoff exercise."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "systemd_update_script", Path(__file__).parents[1] / "scripts/test_systemd_update.py",
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def image_identity(*, digest=None, system="linux"):
    return json.dumps({
        "id": "sha256:" + "1" * 64, "os": system, "architecture": "amd64",
        "digests": ["python@" + (digest or script.BASE_DIGEST)],
    })


def test_primary_success_keeps_pinned_reference_and_records_platform(capsys):
    calls = []

    def docker(*command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=image_identity())

    assert script.acquire_base_image(docker) == script.BASE_IMAGES[0]
    assert [call[0][:2] for call in calls] == [
        ("pull", script.BASE_IMAGES[0]), ("image", "inspect"),
    ]
    assert calls[0][1]["timeout"] == 180
    assert calls[1][0][-1] == script.BASE_IMAGES[0]
    receipt = json.loads(capsys.readouterr().out.partition(": ")[2])
    assert receipt["architecture"] == "amd64" and receipt["os"] == "linux"
    assert receipt["reference"].endswith("@" + script.BASE_DIGEST)


@pytest.mark.parametrize("timeout", [False, True])
def test_acquisition_failure_tries_equivalent_mirror_once(timeout):
    pulls = []

    def docker(*command, **kwargs):
        if command[0] == "pull":
            pulls.append(command[1])
            if len(pulls) == 1:
                if timeout:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                raise subprocess.CalledProcessError(1, command, stderr="429 Data limit exceeded")
        return subprocess.CompletedProcess(command, 0, stdout=image_identity())

    assert script.acquire_base_image(docker) == script.BASE_IMAGES[1]
    assert pulls == list(script.BASE_IMAGES)
    assert {image.rpartition("@")[2] for image in pulls} == {script.BASE_DIGEST}


def test_both_registries_fail_without_inspection_build_or_application():
    commands = []

    def docker(*command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(RuntimeError, match="either official registry"):
        script.acquire_base_image(docker)
    assert commands == [("pull", image) for image in script.BASE_IMAGES]


@pytest.mark.parametrize("identity", [
    image_identity(digest="sha256:" + "2" * 64),
    image_identity(system="windows"),
    json.dumps({"digests": [], "os": "linux"}),
])
def test_identity_mismatch_fails_closed_without_another_mirror(identity):
    commands = []

    def docker(*command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=identity)

    with pytest.raises(RuntimeError, match="pinned Linux image"):
        script.acquire_base_image(docker)
    assert [command[0] for command in commands] == ["pull", "image"]


def test_inspection_failure_does_not_become_acquisition_retry():
    commands = []

    def docker(*command, **kwargs):
        commands.append(command)
        if command[0] == "image":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(subprocess.CalledProcessError):
        script.acquire_base_image(docker)
    assert [command[0] for command in commands] == ["pull", "image"]


@pytest.mark.parametrize("failure", [None, "acquire", "build", "run", "exercise"])
def test_main_never_retries_build_or_application_after_acquisition(monkeypatch, failure):
    calls = []
    docker_calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, stdout=b"historical negative control")
        assert command[0] == "owned-docker"
        args = command[1:]
        docker_calls.append(args)
        # Always force the hosted ECR failure to exercise the real fallback path.
        if args[0] == "pull" and (args[1] == script.BASE_IMAGES[0] or failure == "acquire"):
            raise subprocess.CalledProcessError(1, command)
        if args[0] == failure or (failure == "exercise" and args[-1] == "exercise"):
            raise subprocess.CalledProcessError(1, command)
        stdout = image_identity() if args[0] == "image" else "running\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(script.sys, "argv", ["test_systemd_update.py", "--docker", "owned-docker"])
    monkeypatch.setattr(script.subprocess, "run", run)
    if failure:
        with pytest.raises(RuntimeError if failure == "acquire" else subprocess.CalledProcessError):
            script.main()
    else:
        script.main()

    assert [args[1] for args in docker_calls if args[0] == "pull"] == list(script.BASE_IMAGES)
    builds = [(args, kwargs) for args, kwargs in calls if args[:2] == ["owned-docker", "build"]]
    assert len(builds) == (0 if failure == "acquire" else 1)
    if builds:
        assert builds[0][1]["input"].splitlines()[0] == "FROM " + script.BASE_IMAGES[1]
        assert "public.ecr.aws" not in builds[0][1]["input"]
    assert sum(args[0] == "run" for args in docker_calls) == (0 if failure in {"acquire", "build"} else 1)
    exercises = [args for args in docker_calls if args[-1] == "exercise"]
    assert len(exercises) == (0 if failure in {"acquire", "build", "run"} else 1)
    if exercises:
        assert "--user" in exercises[0] and "1234" in exercises[0]
        assert sum(args[0] == "rm" for args in docker_calls) == 1
    if builds:
        build_index = next(i for i, args in enumerate(docker_calls) if args[0] == "build")
        assert all(args[0] != "pull" for args in docker_calls[build_index:])
