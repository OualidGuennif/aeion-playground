# Cockpit image: Mission Control and the ParcelDesk surface.
#
# The cockpit has no Docker socket, no host home and no provider credentials. It runs
# untrusted code only through the host executor agent; the in-process executor is kept
# for native development, not for this image.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Sandbox creation is a real git clone/commit; the worker and roles stay elsewhere.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --require-hashes -r /app/requirements.lock

COPY app /app/app
COPY sandbox/parceldesk /app/sandbox/parceldesk

# A named volume is mounted here. Docker copies this ownership on first use, so the
# non-root runtime user can write the ledger, sandbox and evidence packets.
RUN mkdir -p /app/runtime && chown 1000:1000 /app/runtime

USER 1000:1000
EXPOSE 8124
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8124", "--no-access-log"]
