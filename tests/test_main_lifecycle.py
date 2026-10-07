from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import main

from src.cache_manager import CacheManager
from src.sync_state import SyncState


def test_main_cleanup_saves_state_and_closes_both_connections(tmp_path, monkeypatch):
    state = SyncState(str(tmp_path / "state.json"))
    worker_docker, event_docker = MagicMock(), MagicMock()
    notion, reader = MagicMock(), MagicMock()
    reader.ident = 1
    reader.inbox = MagicMock()
    monkeypatch.setattr(main, "load_settings", lambda: SimpleNamespace(NOTION_API_KEY="fake"))
    monkeypatch.setattr(main, "SyncState", lambda: state)
    monkeypatch.setattr(main, "CacheManager", lambda: CacheManager(str(tmp_path / "cache.json")))
    monkeypatch.setattr(main, "DockerClient", MagicMock(side_effect=[worker_docker, event_docker]))
    monkeypatch.setattr(main, "NotionClient", lambda *args: notion)
    monkeypatch.setattr(main, "DockerEventReader", lambda *args: reader)
    monkeypatch.setattr(main, "signal", SimpleNamespace(SIGINT=2, SIGTERM=15, signal=lambda *args: None))
    def fail_loop(*args):
        raise RuntimeError("worker failed")
    monkeypatch.setattr(main, "run_event_loop", fail_loop)
    reader.close.side_effect = RuntimeError("stream close failed")
    with pytest.raises(RuntimeError, match="worker failed"):
        main.main()
    reader.close.assert_called_once()
    reader.join.assert_called_once_with(timeout=5)
    worker_docker.disconnect.assert_called_once()
    notion.close.assert_called_once()
    assert SyncState(state.path).jobs == {}
