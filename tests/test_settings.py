import re
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


@pytest.mark.parametrize("contents, field", [
    ("", "YAML root"),
    ("[]", "YAML root"),
    ("{}", "targets"),
    ("targets: []", "targets"),
    ("targets: {default: Docker}", "targets.databases"),
    ("targets: {databases: {}}", "targets.databases"),
    ("targets: {databases: []}", "targets.databases"),
    ("targets: {databases: [Docker]}", "targets.databases[0]"),
    (YAML.replace('name: "Jenkins"', 'other: "Jenkins"'), "targets.databases[1].name"),
    (YAML.replace('name: "Jenkins"', 'name: "  "'), "targets.databases[1].name"),
    (YAML.replace('name: "Jenkins"', 'name: 123'), "targets.databases[1].name"),
    (YAML.replace('database_id: "db-jenkins"', 'other: "db-jenkins"'), "targets.databases[1].database_id"),
    (YAML.replace('"db-jenkins"', "null"), "targets.databases[1].database_id"),
    (YAML.replace('"db-jenkins"', '" "'), "targets.databases[1].database_id"),
    (YAML.replace('name: "Jenkins"', 'name: "Docker"'), "targets.databases[1].name duplicates"),
    (YAML.replace('default: "Docker"', 'other: "Docker"'), "targets.default"),
    (YAML.replace('default: "Docker"', 'default: 42'), "targets.default"),
    (YAML.replace('default: "Docker"', 'default: " "'), "targets.default"),
    (YAML.replace('default: "Docker"', 'default: "Other"'), "targets.default must refer"),
])
def test_invalid_yaml_configuration_reports_field(tmp_path, monkeypatch, contents, field):
    monkeypatch.setenv("DOCKER_API_URL", "unix:///fake")
    monkeypatch.setenv("NOTION_API_KEY", "test-token")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=f"^{re.escape(field)}"):
        Settings(env_file=str(tmp_path / ".env"), yaml_file=str(yaml_path))


def test_yaml_syntax_error_reports_location_without_contents(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_API_URL", "unix:///fake")
    monkeypatch.setenv("NOTION_API_KEY", "test-token")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("targets: [sensitive-value, broken: }\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Invalid YAML configuration at line \d+, column \d+") as error:
        Settings(env_file=str(tmp_path / ".env"), yaml_file=str(yaml_path))
    assert "sensitive-value" not in str(error.value)
    assert error.value.__suppress_context__


def test_yaml_database_aliases_and_environment_precedence(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("DOCKER_API_URL=unix:///dotenv\nNOTION_API_KEY=dotenv-token\n", encoding="utf-8")
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(YAML.replace('"db-jenkins"', '"db-docker"'), encoding="utf-8")
    monkeypatch.setenv("DOCKER_API_URL", "unix:///system")
    monkeypatch.delenv("NOTION_API_KEY", raising=False)

    settings = Settings(env_file=str(env_path), yaml_file=str(yaml_path))

    assert settings.DOCKER_API_URL == "unix:///system"
    assert settings.NOTION_API_KEY == "dotenv-token"
    assert settings.resolve_db_id("Jenkins") == settings.DEFAULT_DB_ID == "db-docker"
