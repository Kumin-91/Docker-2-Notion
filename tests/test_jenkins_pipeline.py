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


def test_deployment_mounts_docker_socket_read_only(tmp_path):
    pipeline = Path("Jenkinsfile").read_text()
    stage = pipeline.split("stage('Run new docker container')", 1)[1]
    script = re.search(r"sh '''(.*?)'''", stage, re.S).group(1)
    fake_docker = tmp_path / "docker"
    fake_docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$D2N_TEST_COMMANDS"\n')
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", "-e", "-c", script], capture_output=True, text=True,
        env={
            **os.environ, "PATH": str(tmp_path), "NETWORK": "ci-outbound",
            "PROJECT_NAME": "d2n", "HOST_DIR": str(tmp_path / "host"),
            "NOTION_API_KEY": "fake-test-token", "LOG_LEVEL": "INFO",
            "TZ": "Asia/Seoul", "D2N_DATABASE": "Jenkins",
            "D2N_TEST_COMMANDS": str(tmp_path / "commands.txt"),
        },
    )
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "commands.txt").read_text().splitlines()
    assert args[0] == "run"
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in args
