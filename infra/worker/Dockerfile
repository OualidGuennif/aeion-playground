# Disposable polyglot test worker: Python contract, Quarkus/Maven backend, Deno frontend.
# The worker runs untrusted source with no network, no socket and a read-only root.
FROM maven:3.9-eclipse-temurin-25 AS m2
WORKDIR /build
COPY sandbox/parceldesk/product/backend/pom.xml ./pom.xml
COPY sandbox/parceldesk/product/backend/src ./src
RUN mvn -B -ntp test

FROM denoland/deno:2.5.0 AS deno
COPY sandbox/parceldesk/product/frontend /opt/deno-src
RUN DENO_DIR=/opt/deno deno cache /opt/deno-src/main.ts /opt/deno-src/tests/format_test.ts

FROM maven:3.9-eclipse-temurin-25
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY infra/worker/requirements.lock /opt/aeion/requirements.lock
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r /opt/aeion/requirements.lock
COPY --from=m2 /root/.m2/repository /opt/m2
COPY --from=deno /usr/bin/deno /usr/local/bin/deno
COPY --from=deno /opt/deno /opt/deno
COPY infra/worker/run_tests.py /opt/aeion/run_tests.py
WORKDIR /work
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    TMPDIR=/tmp \
    HOME=/tmp \
    DENO_DIR=/opt/deno
ENTRYPOINT ["/opt/venv/bin/python", "-I", "/opt/aeion/run_tests.py"]
