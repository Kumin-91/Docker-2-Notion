"""Verify that test-container failures actually stop the Jenkins shell step."""
import os
import re
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("container_exit, expected_success", [(0, True), (1, False), (2, False)])
def test_test_stage_checks_container_exit_status(tmp_path, container_exit, expected_success):
    pipeline = Path("Jenkinsfile").read_text()
    stage = pipeline.split("stage('Test')", 1)[1].split("post {", 1)[0]
    script = re.search(r"sh '''(.*?)'''", stage, re.S).group(1)
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
            "D2N_TEST_COMMANDS": str(tmp_path / "commands.txt"),
        },
    )
    assert (result.returncode == 0) is expected_success
    commands = (tmp_path / "commands.txt").read_text().splitlines()
    create = next(command for command in commands if command.startswith("create "))
    assert "--network ci-outbound" in create


def test_docker_build_stage_uses_configured_network(tmp_path):
    pipeline = Path("Jenkinsfile").read_text()
    stage = pipeline.split("stage('Docker build')", 1)[1]
    script = re.search(r"sh '''(.*?)'''", stage, re.S).group(1)
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
            "PROJECT_NAME": "d2n", "PROJECT_STATUS": "stable", "BUILD_NUMBER": "42",
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
    stage = pipeline.split("stage('Run new docker container')", 1)[1]
    script = re.search(r"sh '''(.*?)'''", stage, re.S).group(1)
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '    run) printf "%s\\n" "$@" > "$D2N_TEST_COMMANDS"; echo new-container-id ;;\n'
        '    inspect) [ "$4" = "new-container-id" ] || exit 2; echo "running 0 $D2N_TEST_HEALTH" ;;\n'
        '    *) exit 2 ;;\n'
        'esac\n'
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-e", "-c", script], capture_output=True, text=True,
        env={
            **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "NETWORK": "ci-outbound",
            "PROJECT_NAME": "d2n", "HOST_DIR": str(tmp_path / "host"),
            "NOTION_API_KEY": "fake-test-token", "LOG_LEVEL": "INFO",
            "TZ": "Asia/Seoul", "D2N_DATABASE": "Jenkins",
            "D2N_TEST_COMMANDS": str(tmp_path / "commands.txt"),
            "D2N_TEST_HEALTH": health,
        },
    )
    assert (result.returncode == 0) is (health == "healthy"), result.stderr
    if health == "unhealthy":
        assert "running 0 unhealthy" in result.stderr
    args = (tmp_path / "commands.txt").read_text().splitlines()
    assert args[0] == "run"
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in args
