from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from notion_client import Client
from notion_client.errors import HTTPResponseError, RequestTimeoutError

from src.notion_client import (
    AmbiguousCreateError, DuplicatePagesError, NotionClient, retry_after_seconds,
)


def http_error(status=503, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return HTTPResponseError(httpx.Response(status, headers=headers, text="error"))


@pytest.fixture
def client(monkeypatch):
    instance = NotionClient.__new__(NotionClient)
    instance.client = MagicMock()
    instance.stop_event = Event()
    instance.retry_not_before = 0.0
    waits = []
    monkeypatch.setattr(instance.stop_event, "wait", lambda delay: waits.append(delay) or False)
    instance.waits = waits
    return instance


def test_search_exhaustion_propagates_instead_of_returning_empty(client):
    client.client.databases.query.side_effect = http_error()
    with pytest.raises(HTTPResponseError):
        client.find_page_id("db", "web")
    assert client.client.databases.query.call_count == 5
    assert client.waits == [1, 2, 4, 8]


def test_successful_empty_search_is_none(client):
    client.client.databases.query.return_value = {"results": []}
    assert client.find_page_id("db", "web") is None


def test_search_recovers_after_transient_failures(client):
    client.client.databases.query.side_effect = [http_error(), http_error(), {"results": [{"id": "page"}]}]
    assert client.find_page_id("db", "web") == "page"
    assert client.waits == [1, 2]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_non_retryable_errors_and_rate_limit_do_not_busy_retry(client, status):
    client.client.databases.query.side_effect = http_error(status)
    with pytest.raises(HTTPResponseError):
        client.find_page_id("db", "web")
    assert client.client.databases.query.call_count == 1
    assert client.waits == []


@pytest.mark.parametrize("response", [{}, {"results": None}, {"results": [{}]}])
def test_malformed_search_never_means_missing(client, response):
    client.client.databases.query.return_value = response
    with pytest.raises(ValueError):
        client.find_page_id("db", "web")


@pytest.mark.parametrize("response", [
    {"results": [{"id": "one"}, {"id": "two"}]},
    {"results": [{"id": "one"}], "has_more": True},
])
def test_existing_duplicates_need_review(client, response):
    client.client.databases.query.return_value = response
    with pytest.raises(DuplicatePagesError):
        client.find_page_id("db", "web")


@pytest.mark.parametrize("error", [http_error(), RequestTimeoutError(), httpx.ConnectError("offline")])
def test_uncertain_creation_is_sent_only_once(client, container, error):
    client.client.pages.create.side_effect = error
    with pytest.raises(AmbiguousCreateError):
        client.create_page("db", container)
    assert client.client.pages.create.call_count == 1
    assert client.waits == []


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_explicit_creation_rejection_propagates(client, container, status):
    client.client.pages.create.side_effect = http_error(status)
    with pytest.raises(HTTPResponseError):
        client.create_page("db", container)
    assert client.client.pages.create.call_count == 1


def test_creation_without_id_is_uncertain(client, container):
    client.client.pages.create.return_value = {}
    with pytest.raises(AmbiguousCreateError):
        client.create_page("db", container)


def test_rate_limit_cooldown_honors_long_header(client, monkeypatch):
    monkeypatch.setattr("src.notion_client.time.time", lambda: 1000)
    client.client.databases.query.side_effect = http_error(429, "600")
    with pytest.raises(HTTPResponseError):
        client.find_page_id("db", "web")
    assert client.retry_not_before == 1600
    assert client.waits == []


def test_long_retry_after_moves_to_scheduler(client):
    client.client.databases.query.side_effect = http_error(503, "600")
    with pytest.raises(HTTPResponseError):
        client.find_page_id("db", "web")
    assert client.client.databases.query.call_count == 1
    assert client.waits == []


@pytest.mark.parametrize("value, expected", [("42", 42), ("-5", 0), ("nan", 0), ("inf", 0), ("invalid", 0)])
def test_retry_after_validation(value, expected):
    assert retry_after_seconds(http_error(429, value)) == expected


def test_retry_after_http_date():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert 58 <= retry_after_seconds(http_error(429, date)) <= 60


def test_shutdown_interrupts_short_retry(client, monkeypatch):
    client.client.databases.query.side_effect = http_error()
    monkeypatch.setattr(client.stop_event, "wait", lambda delay: True)
    with pytest.raises(InterruptedError):
        client.find_page_id("db", "web")
    assert client.client.databases.query.call_count == 1


def test_shutdown_prevents_creation(client, container):
    client.stop_event.set()
    with pytest.raises(InterruptedError):
        client.create_page("db", container)
    client.client.pages.create.assert_not_called()


def test_real_sdk_transport_distinguishes_failure_from_missing(monkeypatch):
    responses = [httpx.Response(503, json={"code": "service_unavailable", "message": "later"})] * 5
    responses.append(httpx.Response(200, json={"results": []}))
    calls = []
    def handler(request):
        calls.append(request)
        return responses.pop(0)
    notion = NotionClient("test-token")
    notion.client.close()
    notion.client = Client(client=httpx.Client(transport=httpx.MockTransport(handler)), auth="test-token")
    monkeypatch.setattr(notion.stop_event, "wait", lambda delay: False)
    try:
        with pytest.raises(HTTPResponseError):
            notion.find_page_id("db", "web")
        assert notion.find_page_id("db", "web") is None
        assert len(calls) == 6
    finally:
        notion.close()
