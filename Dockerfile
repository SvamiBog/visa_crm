# One image = API + built frontend (same origin for cookie sessions).
# Base images are pinned by digest; the tag is there for reading. A new digest comes from
# `docker buildx imagetools inspect <image>:<tag>`. CI builds this file and starts it the
# way Railway does (.github/workflows/ci.yml, job image).

FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS frontend
WORKDIR /srv/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# The same uv release as CI (PINNED_UV_VERSION in .github/workflows/ci.yml).
COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
# Exactly the versions and hashes of uv.lock: a lock out of step with pyproject.toml fails
# the build instead of being resolved afresh. uv leaves a world-writable .venv/.lock; the
# venv is never synced at run time, so it goes.
RUN uv sync --locked --no-dev --no-install-project && rm -f .venv/.lock
COPY backend/ ./
COPY --from=frontend /srv/frontend/dist /srv/frontend/dist
ENV PATH="/srv/backend/.venv/bin:$PATH"
# The app runs unprivileged. Its code, venv and frontend stay root-owned and read-only to
# it, so a bug in the app cannot rewrite them. It writes only to the temporary directory
# (openpyxl saves a workbook export through a temporary file).
RUN groupadd --gid 10001 visacrm \
    && useradd --uid 10001 --gid visacrm --home-dir /nonexistent --no-create-home \
       --shell /usr/sbin/nologin visacrm
ENV TMPDIR=/tmp
USER 10001:10001
CMD ["./start.sh"]
