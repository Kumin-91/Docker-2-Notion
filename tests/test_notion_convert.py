from src.models import DockerContainerInfo
from src.notion_client import NotionClient, _rich_text


def _container(**overrides):
    base = dict(
        container_id="abc123",
        name="web",
        status="running",
        seen="2024-05-01T09:00:00+09:00",
        ip="172.17.0.2: bridge",
        port="80 → 8080/tcp",
        image="nginx:latest",
        created="2024-05-01T08:00:00+09:00",
        stack="myproject",
        d2n_enabled=True,
        d2n_database="",
    )
    base.update(overrides)
    return DockerContainerInfo(**base)


def _convert(container):
    # 네트워크 연결 없이 변환 로직만 검증 (__init__ 우회)
    client = NotionClient.__new__(NotionClient)
    return client._convert_property(container)


def test_rich_text_empty_uses_empty_array():
    assert _rich_text("") == {"rich_text": []}


def test_rich_text_with_value():
    assert _rich_text("x") == {"rich_text": [{"text": {"content": "x"}}]}


def test_convert_includes_core_fields():
    props = _convert(_container())
    assert props["Name"]["title"][0]["text"]["content"] == "web"
    assert props["Status"]["status"]["name"] == "running"
    assert props["Image"]["rich_text"][0]["text"]["content"] == "nginx:latest"
    assert props["IP"]["rich_text"][0]["text"]["content"] == "172.17.0.2: bridge"


def test_convert_includes_dates_when_present():
    props = _convert(_container())
    assert props["Seen"]["date"]["start"] == "2024-05-01T09:00:00+09:00"
    assert props["Created"]["date"]["start"] == "2024-05-01T08:00:00+09:00"


def test_convert_omits_empty_created():
    props = _convert(_container(created=""))
    assert "Created" not in props


def test_convert_sets_stacks_multi_select_when_present():
    props = _convert(_container(stack="myproject"))
    assert props["Stacks"] == {"multi_select": [{"name": "myproject"}]}


def test_convert_omits_stacks_when_empty():
    props = _convert(_container(stack=""))
    assert "Stacks" not in props


def test_convert_empty_ip_clears_property():
    props = _convert(_container(ip="", port=""))
    assert props["IP"] == {"rich_text": []}
    assert props["Ports"] == {"rich_text": []}
