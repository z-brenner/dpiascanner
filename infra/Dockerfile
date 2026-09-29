# One image for the Katz API and worker (different commands).
#   docker build -f infra/Dockerfile -t lantern:latest .
FROM python:3.12-slim

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "uv==0.8.17" \
 && useradd --uid 10001 --create-home lantern

WORKDIR /srv/lantern
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
RUN uv sync --frozen --no-dev --all-packages \
 && chown -R lantern /srv/lantern

USER lantern
ENV PATH="/srv/lantern/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    LANTERN_CACHE_DIR=/tmp/lantern-cache
EXPOSE 8000
CMD ["uvicorn", "lantern_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
