# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE

FROM ${PYTHON_IMAGE} AS base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json \
    PATH="/opt/venv/bin:$PATH"
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg mesa-vulkan-drivers \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /app /opt/venv \
    && chown app:app /app /opt/venv
RUN mkdir -p /var/lib/youtube/job-errors \
    && chown app:app /var/lib/youtube/job-errors \
    && chmod 700 /var/lib/youtube/job-errors
RUN mkdir -p /var/lib/youtube/media \
    && chown app:app /var/lib/youtube/media \
    && chmod 700 /var/lib/youtube/media
WORKDIR /app

FROM base AS dev
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
COPY backend/tach.toml ./tach.toml
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src", "--proxy-headers", "--no-access-log"]

FROM base AS prod
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
