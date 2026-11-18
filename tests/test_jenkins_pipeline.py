"""Verify that test-container failures actually stop the Jenkins shell step."""
import os
import re
import subprocess
from pathlib import Path

import pytest


def _stage_script(pipeline, stage):
    match = re.search(
        rf"stage\('{re.escape(stage)}'\).*?sh(?:\s+|\(script:\s*)'''(.*?)'''",
        pipeline,
        re.S,
    )
    assert match is not None
    return match.group(1).replace("\\\\", "\\")


@pytest.mark.parametrize("container_exit, expected_success", [(0, True), (1, False), (2, False)])
def test_test_stage_checks_container_exit_status(tmp_path, container_exit, expected_success):
    pipeline = Path("Jenkinsfile").read_text()
    script = _stage_script(pipeline, "Test")
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$D2N_TEST_COMMANDS"\n'
        'if [ "$1" = "inspect" ]; then\n'
        f'  echo {container_exit}\n'
        'fi\n'
        'exit 0\n'
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-e", "-c", script], capture_output=True, text=True,
        env={
            **os.environ, "PATH": str(tmp_path), "NETWORK": "ci-outbound",
            "TEST_CONTAINER": "test-env-d2n", "TEST_IMAGE": "python:3.14-trixie",
            "D2N_TEST_COMMANDS": str(tmp_path / "commands.txt"),
        },
    )
    assert (result.returncode == 0) is expected_success
    commands = (tmp_path / "commands.txt").read_text().splitlines()
    create = next(command for command in commands if command.startswith("create "))
    assert "--network ci-outbound" in create


def test_docker_build_stage_uses_configured_network(tmp_path):
    pipeline = Path("Jenkinsfile").read_text()
    script = _stage_script(pipeline, "Docker build")
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = build ]; then [ "$DOCKER_BUILDKIT" = 0 ] || exit 2; fi\n'
        'printf "%s\\n" "$*"\n'
    )
    fake_docker.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", "-e", "-c", script], capture_output=True, text=True,
        env={
            **os.environ, "PATH": str(tmp_path), "NETWORK": "ci-outbound", "DOCKER_BUILDKIT": "1",
            "PROJECT_NAME": "d2n", "IMAGE_TAG": "stable-42",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "build --network ci-outbound -t d2n:stable-42 .",
        "tag d2n:stable-42 d2n:latest",
    ]


@pytest.mark.parametrize("health", ["healthy", "unhealthy"])
def test_deployment_checks_new_container_and_mounts_socket_read_only(tmp_path, health):
    pipeline = Path("Jenkinsfile").read_text()
    socket_mount = "--volume=/var/run/docker.sock:/var/run/docker.sock:ro"
    config_mount = f"--volume={tmp_path / 'host dir'}/config:/app/config"
    assert f"'{socket_mount}'" in pipeline
    assert "'--env=NOTION_API_KEY'" in pipeline
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '    container) exit 1 ;;\n'
        '    run) printf "%s\\n" "$@" > "$D2N_TEST_COMMANDS"; echo new-container-id ;;\n'
        '    inspect) [ "$3" = "new-container-id" ] || exit 2; echo "running $D2N_TEST_HEALTH 0" ;;\n'
        '    *) exit 2 ;;\n'
        'esac\n'
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "NETWORK": "ci-outbound",
        "PROJECT_NAME": "d2n", "IMAGE_TAG": "stable-42", "DOCKER_STOP_TIMEOUT": "10",
        "APP_DOCKER_ARGS": "\n".join((socket_mount, config_mount, "--env=NOTION_API_KEY")),
        "NOTION_API_KEY": "fake-test-token",
        "TZ": "Asia/Seoul", "D2N_DATABASE": "Jenkins",
        "D2N_TEST_COMMANDS": str(tmp_path / "commands.txt"),
        "D2N_TEST_HEALTH": health,
    }
    deploy = subprocess.run(
        ["/bin/sh", "-e", "-c", _stage_script(pipeline, "Deploy")],
        capture_output=True, text=True, check=True, env=env,
    )
    result = subprocess.run(
        ["/bin/sh", "-e", "-c", _stage_script(pipeline, "Verify new docker container")],
        capture_output=True, text=True,
        env={**env, "DEPLOYED_CONTAINER_ID": deploy.stdout.strip()},
    )
    assert (result.returncode == 0) is (health == "healthy"), result.stderr
    if health == "unhealthy":
        assert "running unhealthy 0" in result.stderr
    args = (tmp_path / "commands.txt").read_text().splitlines()
    assert args[0] == "run"
    assert socket_mount in args
    assert config_mount in args
    assert "--env=NOTION_API_KEY" in args
    assert "fake-test-token" not in "\n".join(args)
