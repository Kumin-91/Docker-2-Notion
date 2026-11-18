"""Validate the outgoing Notion page contract through the public write method."""
from dataclasses import replace

import pytest


@pytest.fixture
def properties(mock_notion):
    mock_notion.client.pages.create.return_value = {"id": "page"}

    def convert(container):
        mock_notion.create_page("db-a", container)
        return mock_notion.client.pages.create.call_args.kwargs["properties"]

    return convert


def test_page_properties_include_core_fields_dates_and_stack(properties, container):
    assert properties(container) == {
        "Name": {"title": [{"text": {"content": "web"}}]},
        "Status": {"status": {"name": "running"}},
        "Image": {"rich_text": [{"text": {"content": "nginx:latest"}}]},
        "IP": {"rich_text": [{"text": {"content": "10.0.0.2: bridge"}}]},
        "Ports": {"rich_text": [{"text": {"content": "80 → 8080/tcp"}}]},
        "Seen": {"date": {"start": "2026-10-07T10:00:00+09:00"}},
        "Created": {"date": {"start": "2026-10-07T09:00:00+09:00"}},
        "Stacks": {"multi_select": [{"name": "app"}]},
    }


def test_page_properties_omit_empty_created(properties, container):
    assert "Created" not in properties(replace(container, created=""))


def test_page_properties_preserve_manual_stack_when_container_has_none(properties, container):
    assert "Stacks" not in properties(replace(container, stack=""))


def test_page_properties_clear_empty_network_fields(properties, container):
    props = properties(replace(container, ip="", port=""))
    assert props["IP"] == {"rich_text": []}
    assert props["Ports"] == {"rich_text": []}
