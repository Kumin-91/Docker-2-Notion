import textwrap
import pytest
from config.settings import Settings

YAML = textwrap.dedent(
    """
    targets:
      default: "Docker"
      databases:
        - name: "Docker"
          database_id: "db-docker"
        - name: "Jenkins"
          database_id: "db-jenkins"
    """
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_API_URL", "unix:///var/run/docker.sock")
    monkeypatch.setenv("NOTION_API_KEY", "secret")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(YAML, encoding="utf-8")
    env_path = tmp_path / ".env"  # 존재하지 않아도 무방
    return Settings(env_file=str(env_path), yaml_file=str(yaml_path))


def test_db_mapping_loaded(settings):
    assert settings.DB_IDS == {"Docker": "db-docker", "Jenkins": "db-jenkins"}
    assert settings.DEFAULT_DB_NAME == "Docker"
    assert settings.DEFAULT_DB_ID == "db-docker"


def test_resolve_known_name(settings):
    assert settings.resolve_db_id("Jenkins") == "db-jenkins"


def test_resolve_empty_falls_back_to_default(settings):
    assert settings.resolve_db_id("") == "db-docker"
    assert settings.resolve_db_id(None) == "db-docker"


def test_resolve_unknown_falls_back_to_default(settings):
    assert settings.resolve_db_id("Nope") == "db-docker"


def test_missing_required_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_API_URL", raising=False)
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(YAML, encoding="utf-8")
    with pytest.raises(ValueError):
        Settings(env_file=str(tmp_path / ".env"), yaml_file=str(yaml_path))
