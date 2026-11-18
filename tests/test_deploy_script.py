"""Exercise argument construction with a fake Docker CLI; never deploy anything."""
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "network, expected, health",
    [
        (None, "net_outbound", "healthy"),
        ("", None, "healthy"),
        ("custom-net", "custom-net", "healthy"),
        ("", None, "unhealthy"),
    ],
)
def test_manual_deploy_passes_network_and_gates_success_on_health(tmp_path, network, expected, health):
    host_dir = tmp_path / "host"
    (host_dir / "config").mkdir(parents=True)
    (host_dir / "config" / "config.yaml").write_text("targets: {}")
    capture = tmp_path / "docker-args.jsonl"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, sys\n'
        'with open(os.environ["D2N_TEST_CAPTURE"], "a") as file:\n'
        '    file.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        'if sys.argv[1] == "build":\n'
        '    assert os.environ["DOCKER_BUILDKIT"] == "0"\n'
        'if sys.argv[1] == "run":\n'
        '    print("new-container-id")\n'
        'elif sys.argv[1] == "inspect":\n'
        '    print("running 0", os.environ["D2N_TEST_HEALTH"])\n'
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOST_DIR": str(host_dir), "D2N_TEST_CAPTURE": str(capture),
        "NOTION_API_KEY": "fake-test-token",
        "D2N_TEST_HEALTH": health,
        "DOCKER_BUILDKIT": "1",
    }
    env.pop("NETWORK", None)
    if network is not None:
        env["NETWORK"] = network
    result = subprocess.run(["/bin/sh", str(Path("deploy.sh").resolve())], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is (health == "healthy"), result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    build = next(call for call in calls if call[0] == "build")
    assert build[build.index("--network") + 1] == "net_outbound"
    run = next(call for call in calls if call[0] == "run")
    inspect = next(call for call in calls if call[0] == "inspect")
    assert inspect[-1] == "new-container-id"
    assert (["image", "prune", "-f"] in calls) is (health == "healthy")
    if health == "unhealthy":
        assert "running 0 unhealthy" in result.stderr
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in run
    if expected is None:
        assert "--network" not in run
    else:
        assert run[run.index("--network") + 1] == expected
