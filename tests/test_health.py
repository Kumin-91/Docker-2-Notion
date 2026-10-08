"""Check worker stalls, Docker outage grace, recovery, and isolated probe mode."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from queue import Queue
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from main import run_event_loop
from src.health import HealthReporter, _ping_docker, check_health
from src.storage import read_json, write_json


@pytest.fixture
def health(tmp_path):
    now = [1000.0]
    path = tmp_path / "health.json"
    reporter = HealthReporter("unix:///test/docker.sock", path, lambda: now[0])
    reporter.beat()
    return SimpleNamespace(
        now=now, path=path, reporter=reporter,
        probe=lambda ping: check_health(path, lambda: now[0], ping),
        failure=path.with_name(path.name + ".docker-failure"),
    )


def test_fresh_heartbeat_and_docker_ping_are_healthy(health):
    ping = MagicMock(return_value=True)
    assert health.probe(ping) == 0
    ping.assert_called_once_with("unix:///test/docker.sock")


def test_stalled_loop_is_unhealthy_even_if_docker_works(health):
    health.now[0] += 180
    ping = MagicMock(return_value=True)
    assert health.probe(ping) == 1
    ping.assert_not_called()


def test_docker_failure_window_survives_separate_probes(health):
    ping = MagicMock(return_value=False)
    assert health.probe(ping) == 0
    health.now[0] += 179
    health.reporter.beat()  # Worker is still progressing during the outage.
    assert health.probe(ping) == 0
    health.now[0] += 1
    assert health.probe(ping) == 1
    assert read_json(str(health.failure))["since"] == 1000.0


def test_docker_recovery_clears_failure_window(health):
    assert health.probe(lambda url: False) == 0
    health.now[0] += 180
    health.reporter.beat()
    assert health.probe(lambda url: False) == 1
    assert health.probe(lambda url: True) == 0
    assert not health.failure.exists()
    health.now[0] += 1
    assert health.probe(lambda url: False) == 0
    assert read_json(str(health.failure))["since"] == 1181.0


def test_worker_restart_resets_previous_docker_failure_window(health):
    assert health.probe(lambda url: False) == 0
    health.now[0] += 200
    new = HealthReporter("unix:///test/docker.sock", health.path, lambda: health.now[0])
    new.beat()
    assert health.probe(lambda url: False) == 0
    assert read_json(str(health.failure))["instance"] == new.instance


@pytest.mark.parametrize("report", [
    None, [], {}, {"version": 2},
    {"version": 1, "instance": "a", "pid": -1, "loop_at": 1000, "docker_api_url": "unix:///x"},
    {"version": 1, "instance": "a", "pid": os.getpid(), "loop_at": True, "docker_api_url": "unix:///x"},
    {"version": 1, "instance": "a", "pid": os.getpid(), "loop_at": 1001, "docker_api_url": "unix:///x"},
])
def test_missing_or_invalid_heartbeat_is_unhealthy(tmp_path, report):
    path = tmp_path / "health.json"
    if report is not None:
        write_json(str(path), report)
    assert check_health(path, lambda: 1000.0, lambda url: True) == 1


def test_corrupt_heartbeat_is_unhealthy(health):
    health.path.write_text("{")
    assert health.probe(lambda url: True) == 1


def test_dead_worker_is_unhealthy(health, monkeypatch):
    def dead(pid, signal):
        raise ProcessLookupError()
    monkeypatch.setattr("src.health.os.kill", dead)
    assert health.probe(lambda url: True) == 1


def test_corrupt_docker_failure_history_does_not_restart_grace(health):
    health.failure.write_text("{")
    assert health.probe(lambda url: False) == 1


def test_invalid_docker_failure_timestamp_is_unhealthy(health):
    write_json(str(health.failure), {"instance": health.reporter.instance, "since": 1001})
    assert health.probe(lambda url: False) == 1


def test_failed_health_write_is_unhealthy(health, monkeypatch):
    def fail(*args):
        raise PermissionError()
    monkeypatch.setattr("src.health.write_json", fail)
    assert health.probe(lambda url: False) == 1


def test_reporter_throttles_disk_writes_and_removes_ephemeral_files(health):
    health.now[0] += 4
    health.reporter.beat()
    assert read_json(str(health.path))["loop_at"] == 1000.0
    health.now[0] += 1
    health.reporter.beat()
    assert read_json(str(health.path))["loop_at"] == 1005.0
    assert health.probe(lambda url: False) == 0
    health.reporter.close()
    assert not health.path.exists()
    assert not health.failure.exists()
    assert health.probe(lambda url: True) == 1


def test_idle_event_loop_advances_heartbeat(tmp_path):
    stop = Event()
    now = [1000.0]
    health = HealthReporter("unix:///test/docker.sock", tmp_path / "health.json", lambda: now[0])
    service = MagicMock()
    service.wait_seconds.return_value = 0
    def tick():
        now[0] += 5
        stop.set()
    service.tick.side_effect = tick
    run_event_loop(service, Queue(), stop, health)
    assert read_json(str(health.path))["loop_at"] == 1005.0


def test_failed_tick_does_not_advance_heartbeat(tmp_path):
    now = [1000.0]
    health = HealthReporter("unix:///test/docker.sock", tmp_path / "health.json", lambda: now[0])
    service = MagicMock()
    service.wait_seconds.return_value = 0
    def fail():
        now[0] += 180
        raise RuntimeError("worker failed")
    service.tick.side_effect = fail
    with pytest.raises(RuntimeError, match="worker failed"):
        run_event_loop(service, Queue(), Event(), health)
    assert read_json(str(health.path))["loop_at"] == 1000.0
    assert check_health(health.path, lambda: now[0], lambda url: True) == 1


def test_event_processing_advances_heartbeat(tmp_path):
    from src.event_reader import StreamMessage

    now = [1000.0]
    stop = Event()
    health = HealthReporter("unix:///test/docker.sock", tmp_path / "health.json", lambda: now[0])
    inbox = Queue()
    inbox.put(StreamMessage({"Action": "start"}))
    service = MagicMock()
    service.wait_seconds.return_value = 0
    def tick():
        now[0] += 5
        stop.set()
    service.tick.side_effect = tick
    run_event_loop(service, inbox, stop, health)
    service.submit_event.assert_called_once_with({"Action": "start"})
    assert read_json(str(health.path))["loop_at"] == 1005.0


@pytest.mark.parametrize("ping_status, expected", [(200, True), (503, False)])
def test_real_docker_sdk_health_probe_uses_only_get_requests(monkeypatch, ping_status, expected):
    import requests

    calls = []
    def send(session, request, **kwargs):
        calls.append((request.method, request.url))
        response = requests.Response()
        response.request = request
        if request.url.endswith("/version"):
            response.status_code = 200
            response._content = b'{"ApiVersion": "1.47"}'
        else:
            assert request.url.endswith("/_ping")
            response.status_code = ping_status
            response._content = b'OK' if ping_status == 200 else b'{"message": "Unavailable"}'
        return response
    monkeypatch.setattr(requests.Session, "send", send)
    assert _ping_docker("unix:///test/docker.sock") is expected
    assert [method for method, url in calls] == ["GET", "GET"]


def test_probe_cli_needs_no_settings_and_writes_no_logs_or_sync_state(tmp_path):
    health_path = tmp_path / "health.json"
    reporter = HealthReporter(f"unix://{tmp_path}/absent.sock", health_path)
    reporter.beat()
    script = Path("main.py").resolve()
    env = {**os.environ, "D2N_HEALTH_FILE": str(health_path), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("NOTION_API_KEY", None)
    env.pop("DOCKER_API_URL", None)
    result = subprocess.run(
        [sys.executable, str(script), "--healthcheck"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Docker outage within grace period" in result.stdout
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "data").exists()
    report = json.loads(health_path.read_text())
    report["loop_at"] = time.monotonic() - 181
    health_path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(script), "--healthcheck"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    assert "worker made no progress" in result.stdout
