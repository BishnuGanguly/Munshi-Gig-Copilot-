# Munshi backend + web page. Starts with NO keys: services without one show "key not set" on the page.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    FFMPEG_BIN=/usr/bin/ffmpeg

# ffmpeg from Debian rather than the bundled imageio-ffmpeg binary, so the image works on arm64
# (Apple Silicon) as well as x86_64.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# uv pinned to the version the lockfile was built and tested with.
RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Locked dependencies, without dev tools; the `llm` extra adds the Anthropic SDK (only used if configured).
RUN uv sync --frozen --no-dev --extra llm --no-install-project

COPY app ./app
COPY scripts ./scripts
COPY tests/eval ./tests/eval
COPY .env.example ./

RUN useradd --create-home --uid 10001 munshi && mkdir -p data && chown -R munshi:munshi /app/data
USER munshi

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
