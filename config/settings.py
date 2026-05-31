import os
from typing import Any
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import yaml
from src.logger import config_logger


class Settings:
    """애플리케이션 설정.

    DOCKER_API_URL  : 도커 데몬과 통신하기 위한 URL
    NOTION_API_KEY  : 노션 API 키
    TIMEZONE        : 타임존 (TZ 환경변수 기반)
    DB_IDS          : 데이터베이스 이름 -> ID 매핑 딕셔너리
    DEFAULT_DB_NAME : 기본 데이터베이스 이름
    DEFAULT_DB_ID   : 기본 데이터베이스 ID
    """

    DOCKER_API_URL: str
    NOTION_API_KEY: str
    TIMEZONE: str
    DB_IDS: dict[str, str]
    DEFAULT_DB_NAME: str
    DEFAULT_DB_ID: str

    def __init__(self, env_file: str | None = None, yaml_file: str | None = None) -> None:
        """설정 초기화 및 로드.

        파일 경로를 직접 주입할 수 있어 테스트 시 임시 설정으로 구성하기 쉽습니다.
        미지정 시 환경 변수(ENV_FILE_PATH / CONFIG_FILE_PATH) 또는 기본 경로를 사용합니다.
        """
        resolved_env = env_file or os.getenv("ENV_FILE_PATH") or "config/.env"
        resolved_yaml = yaml_file or os.getenv("CONFIG_FILE_PATH") or "config/config.yaml"

        config_logger.info("Loading configuration...")

        self._load_env(resolved_env)
        self._load_yaml_config(resolved_yaml)

    def _load_env(self, env_file: str) -> None:
        """시스템 환경 변수를 1순위로, 부족한 정보는 .env에서 보충."""

        # load_dotenv는 시스템 환경 변수를 덮어쓰지 않으므로 이미 설정된 변수는 유지되며,
        # 파일이 존재하지 않아도 예외를 발생시키지 않는다.
        load_dotenv(env_file)
        self.DOCKER_API_URL = os.getenv("DOCKER_API_URL", "")
        self.NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")

        tz_env = os.getenv("TZ", "Asia/Seoul")
        try:
            ZoneInfo(tz_env)
            self.TIMEZONE = tz_env
        except Exception:
            config_logger.warning(f"Invalid timezone: {tz_env}. Falling back to Asia/Seoul.")
            self.TIMEZONE = "Asia/Seoul"
        config_logger.info(f"Timezone set to: {self.TIMEZONE}")

        required_vars = {
            "DOCKER_API_URL": self.DOCKER_API_URL,
            "NOTION_API_KEY": self.NOTION_API_KEY,
        }

        for var_name, value in required_vars.items():
            if not value:
                error_msg = f"No {var_name} found in environment variables"
                config_logger.error(error_msg)
                raise ValueError(error_msg)

    def _load_yaml_config(self, yaml_file: str) -> None:
        """.yaml 파일에서 설정을 로드."""
        config: dict[str, Any] = {}
        if os.path.exists(yaml_file):
            with open(yaml_file, "r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
        else:
            config_logger.error(f"YAML configuration file '{os.path.abspath(yaml_file)}' not found")
            raise FileNotFoundError(f"YAML configuration file '{os.path.abspath(yaml_file)}' not found")

        targets_config = config.get("targets", {})
        if not targets_config:
            config_logger.error("No targets configuration found in YAML file")
            raise ValueError("No targets configuration found in YAML file")

        self.DB_IDS = {
            item["name"]: item["database_id"]
            for item in targets_config.get("databases", [])
        }
        if not self.DB_IDS:
            config_logger.error("No database mappings found in configuration")
            raise ValueError("No database mappings found in configuration")

        default_db_name = targets_config.get("default", "")
        if default_db_name == "":
            config_logger.error("No default target specified in configuration")
            raise ValueError("No default target specified in configuration")

        self.DEFAULT_DB_NAME = default_db_name
        self.DEFAULT_DB_ID = self.DB_IDS.get(default_db_name, "")
        if self.DEFAULT_DB_ID == "":
            config_logger.error(f"Default target '{default_db_name}' ID not found in configuration")
            raise ValueError(f"Default target '{default_db_name}' ID not found in configuration")

    def resolve_db_id(self, name: str | None) -> str:
        """`d2n.database` 라벨(데이터베이스 이름)을 실제 Notion DB ID로 해석.

        - 빈 값(라벨 미지정) -> 기본 DB로 폴백
        - 매핑에 없는 이름   -> 경고 후 기본 DB로 폴백
        """
        if not name:
            return self.DEFAULT_DB_ID

        db_id = self.DB_IDS.get(name)
        if db_id:
            return db_id

        config_logger.warning(
            f"Unknown d2n.database '{name}'. Falling back to default DB '{self.DEFAULT_DB_NAME}'."
        )
        return self.DEFAULT_DB_ID


def load_settings(env_file: str | None = None, yaml_file: str | None = None) -> Settings:
    """설정 인스턴스를 생성해 반환하는 팩토리.

    전역 싱글톤 대신 명시적으로 호출하여 import 시점의 부작용(검증/예외)을 제거합니다.
    """
    return Settings(env_file, yaml_file)
