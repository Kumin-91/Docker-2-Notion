import os
from typing import Any, Dict
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import yaml
from src.logger import config_logger

"""
DOCKER_API_URL : 도커 데몬과 통신하기 위한 URL
NOTION_API_KEY : 노션 API 키
DB_IDS : 데이터베이스 이름과 ID의 매핑 딕셔너리
DEFAULT_DB_ID : 기본 데이터베이스 ID
"""

class Settings:
    # Type Hints
    DOCKER_API_URL: str
    NOTION_API_KEY: str
    TIMEZONE: str
    DB_IDS: Dict[str, str]
    DEFAULT_DB_ID: str

    def __init__(self) -> None:
        """설정 초기화 및 로드"""
        env_file = os.getenv("ENV_FILE_PATH", "config/.env")
        yaml_file = os.getenv("CONFIG_FILE_PATH", "config/config.yaml")

        config_logger.info("Loading configuration...")

        # 설정 로드
        self._load_env(env_file)
        self._load_yaml_config(yaml_file)

    def _load_env(self, env_file: str) -> None:
        """시스템 환경 변수를 1순위로, 부족한 정보는 .env에서 보충."""

        # load_dotenv는 시스템 환경 변수를 덮어쓰지 않으므로, 이미 설정된 변수는 유지됩니다.
        # load_dotenv는 파일이 존재하지 않아도 예외를 발생시키지 않으므로 안전하게 호출할 수 있습니다.
        load_dotenv(env_file)
        # 시스템 환경 변수가 없다면, load_dotenv로 .env 파일에서 로드된 값을 사용합니다.
        self.DOCKER_API_URL = os.getenv("DOCKER_API_URL", "")
        self.NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")

        # 타임존 설정 (TZ 환경변수 확인, 없거나 유효하지 않으면 Asia/Seoul)
        tz_env = os.getenv("TZ", "Asia/Seoul")
        try:
            ZoneInfo(tz_env)
            self.TIMEZONE = tz_env
        except Exception:
            config_logger.warning(f"Invalid timezone: {tz_env}. Falling back to Asia/Seoul.")
            self.TIMEZONE = "Asia/Seoul"
        config_logger.info(f"Timezone set to: {self.TIMEZONE}")

        # 필수 환경 변수 유효성 검사
        required_vars = {
            "DOCKER_API_URL": self.DOCKER_API_URL,
            "NOTION_API_KEY": self.NOTION_API_KEY
        }
        
        for var_name, value in required_vars.items():
            if not value:
                error_msg = f"No {var_name} found in environment variables"
                config_logger.error(error_msg)
                raise ValueError(error_msg)

    def _load_yaml_config(self, yaml_file: str) -> None:
        """.yaml 파일에서 설정을 로드."""
        config: Dict[str, Any] = {}
        if os.path.exists(yaml_file):
            with open(yaml_file, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file) or {}
        else:
            config_logger.error(f"YAML configuration file '{os.path.abspath(yaml_file)}' not found")
            raise FileNotFoundError(f"YAML configuration file '{os.path.abspath(yaml_file)}' not found")
        
        # YAML에서 targets 설정 불러오기
        # targets 설정이 존재하는지 확인
        targets_config = config.get("targets", {})

        # targets 설정 유효성 검사
        if not targets_config:
            config_logger.error("No targets configuration found in YAML file")
            raise ValueError("No targets configuration found in YAML file")
        
        # 데이터베이스 ID 매핑 생성
        self.DB_IDS = {
            item["name"]: item["database_id"]
            for item in targets_config.get("databases", [])
        }

        # 데이터베이스 매핑 유효성 검사
        if not self.DB_IDS:
            config_logger.error("No database mappings found in configuration")
            raise ValueError("No database mappings found in configuration")
        
        # 기본 데이터베이스 설정
        default_db_name = targets_config.get("default", "")
        if default_db_name == "":
            config_logger.error(f"Default target '{default_db_name}' not found in database list")
            raise ValueError(f"Default target '{default_db_name}' not found in database list")
        
        # 기본 데이터베이스 ID 설정
        self.DEFAULT_DB_ID = self.DB_IDS.get(default_db_name, "")

        # 기본 데이터베이스 ID 유효성 검사
        if self.DEFAULT_DB_ID == "":
            config_logger.error(f"Default target '{default_db_name}' ID not found in configuration")
            raise ValueError(f"Default target '{default_db_name}' ID not found in configuration")

# 전역 설정 인스턴스 생성
settings = Settings()