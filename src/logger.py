import logging
import os
import sys
from datetime import datetime

# 로그 레벨 설정
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# 유효하지 않은 레벨이면 INFO로 설정
if LOG_LEVEL not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
    print(f"Warning: Invalid LOG_LEVEL '{LOG_LEVEL}'. Defaulting to INFO.", file=sys.stderr)
    LOG_LEVEL = "INFO"

# 로그 디렉토리 생성
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE_PATH = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log")

class ColoredFormatter(logging.Formatter):
    """콘솔 출력용 컬러 로그 포맷터"""
    
    # 로그 레벨별 색상 (ANSI 코드)
    LOG_COLORS = {
        logging.DEBUG: '\x1b[40;1m',     # Black on gray
        logging.INFO: '\x1b[34;1m',      # Blue
        logging.WARNING: '\x1b[33;1m',   # Yellow
        logging.ERROR: '\x1b[31m',       # Red
        logging.CRITICAL: '\x1b[41m',    # Red Background
    }
    
    # 로거 이름별 색상
    NAME_COLORS = {
        "Main": '\x1b[35m',      # Magenta
        "Docker": '\x1b[36m',    # Cyan
        "Notion": '\x1b[32m',    # Green
        "Cache": '\x1b[33m',     # Yellow
        "Config": '\x1b[34m'     # Blue
    }
    
    RESET = '\x1b[0m'

    def format(self, record: logging.LogRecord) -> str:
        color = self.LOG_COLORS.get(record.levelno, '')
        name_color = self.NAME_COLORS.get(record.name, self.RESET)
        
        asctime = self.formatTime(record, datefmt="%H:%M:%S")
        levelname = f"{color}{record.levelname:<8}{self.RESET}"
        name = f"{name_color}{record.name:<8}{self.RESET}"
        msg = record.getMessage()
        
        return f"\x1b[30;1m{asctime}{self.RESET} {levelname} {name} {msg}"

class FileFormatter(logging.Formatter):
    """파일 저장용 일반 텍스트 로그 포맷터"""
    
    def format(self, record: logging.LogRecord) -> str:
        asctime = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")
        return f"{asctime} {record.levelname:<8} {record.name:<8} {record.getMessage()}"

def setup_logger(name: str) -> logging.Logger:
    """로거 초기화 및 설정"""
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False
    
    if not logger.handlers:
        # 파일 핸들러
        file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
        file_handler.setFormatter(FileFormatter())
        logger.addHandler(file_handler)
        
        # 콘솔 핸들러
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ColoredFormatter())
        logger.addHandler(console_handler)
    
    return logger

# 로거 인스턴스 생성
main_logger = setup_logger("Main")
config_logger = setup_logger("Config")
docker_logger = setup_logger("Docker")
notion_logger = setup_logger("Notion")
cache_logger = setup_logger("Cache")
