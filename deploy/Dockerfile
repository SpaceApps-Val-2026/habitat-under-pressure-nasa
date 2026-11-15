# Habitat Under Pressure: one image with the API, the executor and the built web app.
# Build from the repository root:  docker compose -f deploy/compose.yaml up --build

# Stage 1: Node builds the web app into static files.
FROM node:24-slim AS web
WORKDIR /repo/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
# The web reads its texts and its example data from outside its own folder.
COPY locales /repo/locales
COPY docs/contratos /repo/docs/contratos
RUN npm run build

# Stage 2: Python serves the API, the event stream and those files.
FROM python:3.12-slim
# The raster libraries ship inside their wheels, except this one, which the slim image does not include.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1

# Dependencies first, so a change in the code does not reinstall them.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/hup_data/pyproject.toml packages/hup_data/
COPY packages/hup_ops/pyproject.toml packages/hup_ops/
COPY apps/api/pyproject.toml apps/api/
RUN uv sync --frozen --no-dev --no-install-workspace

COPY packages packages
COPY apps/api apps/api
RUN uv sync --frozen --no-dev

COPY docs/contratos docs/contratos
COPY catalog catalog
COPY locales locales
COPY --from=web /repo/apps/web/dist apps/web/dist

# The commit the image is built from, shown by /health:
#   docker build --build-arg HUP_COMMIT=$(git rev-parse --short HEAD) ...
ARG HUP_COMMIT=""
ENV HUP_COMMIT=$HUP_COMMIT
ENV PATH="/app/.venv/bin:$PATH" \
    HUP_REPO_ROOT=/app \
    HUP_DATA_DIR=/data \
    HUP_WEB_DIST=/app/apps/web/dist
EXPOSE 8080
CMD ["uvicorn", "hup_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
