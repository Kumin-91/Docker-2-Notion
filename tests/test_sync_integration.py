"""Exercise the scheduler with the real pinned SDK through an in-memory HTTP transport."""
import httpx
from notion_client import Client

from src.notion_client import NotionClient
from src.storage import identity
from src.sync_service import SyncService
from src.sync_state import SyncState


def test_accepted_creation_with_lost_response_is_recovered_after_restart(harness):
    h = harness
    created, updates = [], []
    def handler(request):
        if request.url.path.endswith('/query'):
            return httpx.Response(200, json={'results': [{'id': 'accepted-page'}] if created else []})
        if request.method == 'POST' and request.url.path.endswith('/pages'):
            created.append('accepted-page')
            raise httpx.ReadTimeout('response lost after server commit', request=request)
        if request.method == 'PATCH':
            updates.append(request.url.path)
            return httpx.Response(200, json={'id': 'accepted-page'})
        raise AssertionError(f'Unexpected request: {request.method} {request.url.path}')
    notion = NotionClient('fake-token', h.stop)
    notion.client.close()
    notion.client = Client(client=httpx.Client(transport=httpx.MockTransport(handler)), auth='fake-token')
    h.service.notion = notion
    try:
        h.service.submit(h.container)
        h.service.tick()
        assert created == ['accepted-page']
        state = SyncState(h.state.path)
        assert state.jobs[identity('db-a', 'web')].uncertain
        service = SyncService(h.docker, notion, h.cache, h.settings, state, h.stop, lambda: h.now[0])
        service.snapshot_due = float('inf')
        h.now[0] = 1030
        service.tick()
        assert created == ['accepted-page']
        assert updates == ['/v1/pages/accepted-page']
        assert not state.jobs
        assert h.cache.get_page_id('db-a', 'web') == 'accepted-page'
    finally:
        notion.close()


def test_real_sdk_search_outage_never_reaches_creation(harness, monkeypatch):
    h = harness
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(503, json={'code': 'service_unavailable', 'message': 'temporary outage'})
    notion = NotionClient('fake-token', h.stop)
    notion.client.close()
    notion.client = Client(client=httpx.Client(transport=httpx.MockTransport(handler)), auth='fake-token')
    monkeypatch.setattr(h.stop, 'wait', lambda delay: False)
    h.service.notion = notion
    try:
        h.service.submit(h.container)
        h.service.tick()
        assert len(calls) == 5
        assert all(path.endswith('/query') for method, path in calls)
        assert h.state.jobs[identity('db-a', 'web')].due == 1030
        h.now[0] = 1030
        h.service.tick()
        assert len(calls) == 10
        assert all(path.endswith('/query') for method, path in calls)
    finally:
        notion.close()
