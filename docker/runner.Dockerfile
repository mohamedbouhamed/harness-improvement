# Minimal image for executing candidate modules and unittest files.
# Build once: python -m harness_mvp build-image
FROM python:3.11-slim

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

USER 10001:10001
WORKDIR /work
