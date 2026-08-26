# NOT verified — see the note at the top of docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer caches across source changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" "pydantic>=2.9.0" \
    "pydantic-settings>=2.6.0" "asyncpg>=0.30.0" "httpx>=0.27.0" \
    "structlog>=24.4.0" "pyyaml>=6.0.2" "nh3>=0.2.18" "python-dotenv>=1.0.1"

COPY app ./app

# The app resolves .claude/ and corpus.yml relative to the repo root, which is
# two levels above the package. Compose mounts them at /app/.claude and
# /app/corpus.yml, so /app must look like the repo root to the config module.
RUN mkdir -p /app/.claude/skills

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8000/health', timeout=4).status_code==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
