from src.docker_client import (
    is_host_network,
    parse_ip,
    parse_ports,
    parse_stack,
    to_local_iso,
)


# --- is_host_network -------------------------------------------------------

def test_host_network_by_mode():
    assert is_host_network({"HostConfig": {"NetworkMode": "host"}}) is True


def test_host_network_by_networks_key():
    assert is_host_network({"NetworkSettings": {"Networks": {"host": {}}}}) is True


def test_not_host_network():
    assert is_host_network({"HostConfig": {"NetworkMode": "bridge"}}) is False
    assert is_host_network({}) is False


# --- parse_ip --------------------------------------------------------------

def test_parse_ip_multiple_networks_ip_first_sorted_ascending():
    attrs = {
        "NetworkSettings": {
            "Networks": {
                "bridge": {"IPAddress": "172.17.0.2"},
                "app-net": {"IPAddress": "10.5.0.3"},
            }
        }
    }
    # `ip: name` 형식, IP 오름차순
    assert parse_ip(attrs) == "10.5.0.3: app-net\n172.17.0.2: bridge"


def test_parse_ip_numeric_sort_not_lexicographic():
    attrs = {
        "NetworkSettings": {
            "Networks": {
                "n21": {"IPAddress": "10.21.0.5"},
                "n20_1": {"IPAddress": "10.20.1.3"},
                "n20_0": {"IPAddress": "10.20.0.7"},
                "n9": {"IPAddress": "10.9.0.2"},
            }
        }
    }
    # 숫자값 기준: 10.9.x 가 10.20.x 보다 앞서야 함 (문자열 정렬이면 뒤로 감)
    assert parse_ip(attrs) == (
        "10.9.0.2: n9\n"
        "10.20.0.7: n20_0\n"
        "10.20.1.3: n20_1\n"
        "10.21.0.5: n21"
    )


def test_parse_ip_host_mode():
    assert parse_ip({"HostConfig": {"NetworkMode": "host"}}) == "host"


def test_parse_ip_legacy_fallback():
    attrs = {"NetworkSettings": {"IPAddress": "172.17.0.9", "Networks": {}}}
    assert parse_ip(attrs) == "172.17.0.9"


def test_parse_ip_empty():
    assert parse_ip({}) == ""


def test_parse_ip_skips_networks_without_ip():
    attrs = {
        "NetworkSettings": {
            "Networks": {
                "bridge": {"IPAddress": "172.17.0.2"},
                "none": {"IPAddress": ""},
            }
        }
    }
    assert parse_ip(attrs) == "172.17.0.2: bridge"


# --- parse_ports -----------------------------------------------------------

def test_parse_ports_all_interfaces_omits_ip_and_skips_ipv6():
    attrs = {
        "NetworkSettings": {
            "Ports": {
                "80/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "8080"},
                    {"HostIp": "::", "HostPort": "8080"},
                ]
            }
        }
    }
    assert parse_ports(attrs) == "80 → 8080/tcp"


def test_parse_ports_specific_ip_is_shown():
    attrs = {
        "NetworkSettings": {
            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
        }
    }
    assert parse_ports(attrs) == "5432 → 127.0.0.1:5432/tcp"


def test_parse_ports_exposed_only():
    attrs = {"NetworkSettings": {"Ports": {"9000/tcp": None}}}
    assert parse_ports(attrs) == "9000/tcp"


def test_parse_ports_host_mode_is_blank():
    attrs = {"HostConfig": {"NetworkMode": "host"}, "NetworkSettings": {"Ports": {}}}
    assert parse_ports(attrs) == ""


def test_parse_ports_multiple_sorted():
    attrs = {
        "NetworkSettings": {
            "Ports": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
                "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}],
                "9000/tcp": None,
            }
        }
    }
    assert parse_ports(attrs) == "5432 → 127.0.0.1:5432/tcp\n80 → 8080/tcp\n9000/tcp"


def test_parse_ports_empty():
    assert parse_ports({}) == ""


# --- to_local_iso ----------------------------------------------------------

def test_to_local_iso_truncates_nanoseconds():
    out = to_local_iso("2024-05-01T12:00:00.123456789Z", "UTC")
    assert out.startswith("2024-05-01T12:00:00.123456")
    assert out.endswith("+00:00")


def test_to_local_iso_timezone_conversion():
    assert to_local_iso("2024-05-01T00:00:00Z", "Asia/Seoul") == "2024-05-01T09:00:00+09:00"


def test_to_local_iso_zero_time_is_blank():
    assert to_local_iso("0001-01-01T00:00:00Z", "UTC") == ""


def test_to_local_iso_empty_is_blank():
    assert to_local_iso("", "UTC") == ""


def test_to_local_iso_invalid_is_blank():
    assert to_local_iso("not-a-timestamp", "UTC") == ""


# --- parse_stack -----------------------------------------------------------

def test_parse_stack_compose():
    assert parse_stack({"com.docker.compose.project": "myapp"}) == "myapp"


def test_parse_stack_swarm_fallback():
    assert parse_stack({"com.docker.stack.namespace": "prodstack"}) == "prodstack"


def test_parse_stack_compose_takes_precedence():
    labels = {"com.docker.compose.project": "a", "com.docker.stack.namespace": "b"}
    assert parse_stack(labels) == "a"


def test_parse_stack_none():
    assert parse_stack({}) == ""
    assert parse_stack({"some.other.label": "x"}) == ""
