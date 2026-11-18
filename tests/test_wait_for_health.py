"""Exercise the deployment health gate without a Docker daemon."""

import os
import subprocess

import pytest


@pytest.mark.parametrize(
    "scenario, returncode, message",
    [
        ("start", 0, "is healthy"),
        ("running 0 unhealthy", 1, "running 0 unhealthy"),
        ("running 1 healthy", 1, "running 1 healthy"),
        ("restarting 1 starting", 1, "restarting 1 starting"),
        ("exited 0 starting", 1, "exited 0 starting"),
        ("running 0 none", 1, "running 0 none"),
        ("inspect-error", 1, "Could not inspect container"),
        ("deadline", 1, "within 300 seconds"),
        ("stalled-inspect", 1, "within 300 seconds"),
    ],
)
def test_wait_for_health(tmp_path, scenario, returncode, message):
    commands = {
        "docker": """
[ "$1" = inspect ] && [ "$2" = --format ] && [ "$4" = new-container-id ] || exit 2
case "$D2N_TEST_SCENARIO" in
    start)
        if [ -f "$D2N_TEST_POLLED" ]; then
            echo "running 0 healthy"
        else
            echo "running 0 starting"
        fi
        ;;
    deadline) echo "running 0 starting" ;;
    stalled-inspect)
        : > "$D2N_TEST_POLLED"
        exec /bin/sleep 30
        ;;
    inspect-error) echo "sensitive daemon diagnostic" >&2; exit 1 ;;
    *) echo "$D2N_TEST_SCENARIO" ;;
esac
""",
        "date": """
case "$D2N_TEST_SCENARIO" in
    deadline|stalled-inspect)
        if [ -f "$D2N_TEST_POLLED" ]; then echo 300; exit; fi
        ;;
esac
echo 0
""",
        "sleep": """
if [ "$1" = 5 ]; then
    : > "$D2N_TEST_POLLED"
else
    /bin/sleep 0.01
fi
""",
    }
    for name, script in commands.items():
        executable = tmp_path / name
        executable.write_text("#!/bin/sh\n" + script)
        executable.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", "./wait-for-health.sh", "new-container-id"],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "D2N_TEST_POLLED": str(tmp_path / "polled"),
            "D2N_TEST_SCENARIO": scenario,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == returncode
    assert message in (result.stdout if returncode == 0 else result.stderr)
    assert "sensitive daemon diagnostic" not in result.stderr
