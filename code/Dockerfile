FROM mcr.microsoft.com/playwright/python:v1.54.2-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM=1 \
    AUTO_EDITOR_OUTPUT_ROOT=/tmp/auto-tiktok-output \
    AUTO_EDITOR_TELEGRAM_CLEANUP_AFTER_JOB=1 \
    AUTO_EDITOR_LAZY_DOWN_ONLY=1 \
    AUTO_EDITOR_MAX_PARALLEL_SESSION_ITEMS=1 \
    AUTO_EDITOR_FFMPEG_BIN=ffmpeg \
    AUTO_EDITOR_FFPROBE_BIN=ffprobe \
    AUTO_EDITOR_YTDLP_BIN=yt-dlp \
    AUTO_EDITOR_LAZY_DOWN_BIN=lazy-down

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py README.md ./
COPY src ./src
COPY vendor/lazy-downloader ./vendor/lazy-downloader

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -e . \
    && npm install -g ./vendor/lazy-downloader

CMD ["auto-tiktok-telegram-web"]
