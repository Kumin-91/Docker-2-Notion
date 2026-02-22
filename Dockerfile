# 1단계: 빌드 환경
FROM python:3.13-slim-trixie AS builder

WORKDIR /app

# 빌드 의존성 설치
RUN apt-get update && apt-get install -y gcc build-essential libffi-dev && rm -rf /var/lib/apt/lists/*

# 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install pyinstaller

# 소스 코드 전체 복사 및 빌드
COPY . .
RUN pyinstaller --onefile --name Docker-2-Notion --clean main.py

# 2단계: 실행 환경
FROM debian:trixie-slim

WORKDIR /app

# 런타임 의존성 설치 (타임존, 인증서 등)
RUN apt-get update && apt-get install -y ca-certificates tzdata && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/dist/Docker-2-Notion .

ENV DOCKER_API_URL=unix:///var/run/docker.sock
ENV NOTION_API_KEY=
ENV TZ=Asia/Seoul
ENV LOG_LEVEL=INFO

# 로그, 설정, 캐시 데이터는 컨테이너 외부에서 관리하도록 볼륨으로 설정
# 실행 시: -v ./logs:/app/logs -v ./config:/app/config -v ./data:/app/data
VOLUME ["/app/logs", "/app/config", "/app/data"]

CMD ["./Docker-2-Notion"]