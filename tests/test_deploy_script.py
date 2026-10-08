"""Exercise argument construction with a fake Docker CLI; never deploy anything."""
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("network, expected", [(None, "net_outbound"), ("", None), ("custom-net", "custom-net")])
def test_manual_deploy_respects_explicit_empty_network(tmp_path, network, expected):
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
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOST_DIR": str(host_dir), "D2N_TEST_CAPTURE": str(capture),
        "NOTION_API_KEY": "fake-test-token",
    }
    env.pop("NETWORK", None)
    if network is not None:
        env["NETWORK"] = network
    result = subprocess.run(["/bin/sh", str(Path("deploy.sh").resolve())], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    run = next(call for call in calls if call[0] == "run")
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in run
    if expected is None:
        assert "--network" not in run
    else:
        assert run[run.index("--network") + 1] == expected
