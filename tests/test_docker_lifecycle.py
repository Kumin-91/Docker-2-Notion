from threading import Event
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from docker.errors import NotFound
from requests.exceptions import ConnectionError

from src.docker_client import DockerClient
from src.event_reader import DockerEventReader, FILTER


@pytest.fixture
def docker_client(monkeypatch):
    sdk = MagicMock()
    monkeypatch.setattr("src.docker_client.from_env", lambda **kwargs: sdk)
    client = DockerClient(SimpleNamespace(DOCKER_API_URL="unix:///fake", TIMEZONE="Asia/Seoul"), connect=False)
    return client, sdk


def test_initialization_is_lazy_for_startup_outage(monkeypatch):
    factory = MagicMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr("src.docker_client.from_env", factory)
    client = DockerClient(SimpleNamespace(DOCKER_API_URL="unix:///fake"), connect=False)
    factory.assert_not_called()
    assert client.reconnect() is False


def test_snapshot_failure_propagates_instead_of_becoming_empty(docker_client):
    client, sdk = docker_client
    sdk.containers.list.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        client.snapshot()


def test_inspect_failure_is_not_not_found(docker_client):
    client, sdk = docker_client
    sdk.containers.get.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        client.get_container_info("id")
    sdk.containers.get.side_effect = NotFound("gone")
    assert client.get_container_info("id") is None


def test_snapshot_keeps_listed_id_even_if_removed_during_inspect(docker_client):
    client, sdk = docker_client
    sdk.containers.list.return_value = [SimpleNamespace(id="gone")]
    sdk.containers.get.side_effect = NotFound("gone")
    snapshot = client.snapshot()
    assert snapshot.container_ids == {"gone"} and snapshot.containers == []
    sdk.containers.list.assert_called_once_with(all=True, sparse=True)


def test_partial_inspect_failure_invalidates_entire_snapshot(docker_client):
    client, sdk = docker_client
    sdk.containers.list.return_value = [SimpleNamespace(id="one")]
    sdk.containers.get.side_effect = ConnectionError("inspect failed")
    with pytest.raises(ConnectionError):
        client.snapshot()


def test_disconnect_closes_blocking_stream_and_sdk(docker_client):
    client, sdk = docker_client
    stream = client.monitor_changes()
    client.disconnect()
    stream.close.assert_called_once()
    sdk.close.assert_called_once()
    assert client._client is None


def test_event_reader_retries_exceptions_during_reconnect():
    docker = MagicMock(spec=DockerClient)
    docker.reconnect.side_effect = ConnectionError("offline")
    stop = MagicMock(spec=Event)
    stop.is_set.return_value = False
    stop.wait.side_effect = [False, False, True]
    reader = DockerEventReader(docker, Queue(), stop)
    reader.run()
    assert docker.reconnect.call_count == 3
    assert [call.args[0] for call in stop.wait.call_args_list] == [1, 2, 4]


def test_stream_opens_before_snapshot_notification():
    inbox = Queue()
    docker = MagicMock(spec=DockerClient)
    docker.reconnect.return_value = True
    def open_stream(**kwargs):
        assert inbox.empty()
        return iter([{"Action": "start"}])
    docker.monitor_changes.side_effect = open_stream
    stop = MagicMock(spec=Event)
    stop.is_set.return_value = False
    stop.wait.return_value = True
    reader = DockerEventReader(docker, inbox, stop)
    reader.run()
    assert inbox.get_nowait().event is None
    assert inbox.get_nowait().event == {"Action": "start"}
    assert FILTER["label"] == "d2n.enabled"


def test_real_thread_can_stop_a_blocking_stream():
    entered, released = Event(), Event()
    class BlockingDocker:
        def reconnect(self):
            return True
        def monitor_changes(self, **kwargs):
            entered.set()
            released.wait(2)
            if False:
                yield {}
        def disconnect(self):
            released.set()
    stop = Event()
    reader = DockerEventReader(BlockingDocker(), Queue(), stop)
    reader.start()
    try:
        assert entered.wait(1)
        reader.close()
        reader.join(timeout=1)
        assert not reader.is_alive()
    finally:
        released.set()
        stop.set()


def test_shutdown_interrupts_snapshot_before_inspection(docker_client):
    client, sdk = docker_client
    sdk.containers.list.return_value = [SimpleNamespace(id="one")]
    client.stop_event.set()
    with pytest.raises(InterruptedError):
        client.snapshot()
    sdk.containers.get.assert_not_called()
