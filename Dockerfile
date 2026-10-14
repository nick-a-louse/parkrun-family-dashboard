FROM python:3.12-slim-bookworm

# Standard image. The :browser variant (headless Chromium) is built from Dockerfile.browser.
ARG VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=$VERSION \
    HOME=/tmp \
    TZ=Europe/London \
    DB_PATH=/data/parkrun.db \
    RUNNERS_FILE=/config/runners.yaml

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Runs as PUID 1003 / PGID 1006 on my server (my app standard). Compose sets it via user:;
# this default keeps the image non-root when run without compose.
RUN mkdir -p /data /config && chown 1003:1006 /data /config

USER 1003:1006
VOLUME ["/data"]
EXPOSE 8000
LABEL org.opencontainers.image.title="parkrun family dashboard" \
      org.opencontainers.image.version="$VERSION"

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
