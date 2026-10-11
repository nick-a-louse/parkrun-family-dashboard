FROM python:3.12-slim-bookworm

# Set INSTALL_BROWSER=true to include headless Chromium (FETCHER=browser).
ARG INSTALL_BROWSER=false
ARG VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/London \
    DB_PATH=/data/parkrun.db \
    RUNNERS_FILE=/config/runners.yaml \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_BROWSER" = "true" ]; then \
      pip install --no-cache-dir playwright==1.48.2 && playwright install --with-deps chromium; \
    fi

COPY app ./app
RUN useradd --system --uid 1000 --home /app parkrun \
 && mkdir -p /data /config && chown parkrun /data /config

USER parkrun
VOLUME ["/data"]
EXPOSE 8000
LABEL org.opencontainers.image.title="parkrun family dashboard" \
      org.opencontainers.image.version="$VERSION"

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
