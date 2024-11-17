FROM python:3.12.7-alpine3.20 AS builder

RUN pip install poetry
RUN mkdir -p /app
COPY . /app

WORKDIR /app
RUN poetry install --without dev

FROM python:3.12.7-alpine3.20 AS base

COPY --from=builder /app /app
RUN mkdir /app/config
WORKDIR /app
VOLUME /app/config

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "wolsocketproxy", "-c", "/app/config/wolsocketproxy.conf"]
