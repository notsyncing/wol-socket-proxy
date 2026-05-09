FROM ghcr.io/astral-sh/uv:0.11.7-python3.13-trixie AS builder

RUN mkdir -p /app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked


FROM docker.io/python:3.13.13-slim-trixie

RUN apt update && apt -y upgrade && apt install -y curl

COPY --from=builder /app /app

RUN mkdir /app/config

ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

CMD python -m wolsocketproxy \
    -c /app/config/wolsocketproxy.conf
