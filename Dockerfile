# Build native wheels (notably TgCrypto) separately from the runtime image.
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
# Optional headless browser for AUTO_CLICK=1:  docker build --build-arg WITH_BROWSER=1 .
ARG WITH_BROWSER=0
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY requirements-browser.txt .
RUN if [ "$WITH_BROWSER" = "1" ]; then \
      pip install -r requirements-browser.txt && playwright install --with-deps chromium \
      && chmod -R a+rX /ms-playwright; fi
COPY bot ./bot
COPY run.py ./run.py
RUN useradd --system --uid 10001 --create-home appuser
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1
CMD ["python", "-m", "bot"]
