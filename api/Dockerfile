# api/Dockerfile
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, so a source change does not re-resolve them. --no-install-project is
# load-bearing: the package directory does not exist at this layer, and without it hatchling
# fails with "Unable to determine which files to ship inside the wheel".
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY oneledger ./oneledger
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "oneledger.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
