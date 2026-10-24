FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
COPY infra/worker/requirements.lock /opt/aeion/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /opt/aeion/requirements.lock
COPY infra/worker/run_tests.py /opt/aeion/run_tests.py
WORKDIR /work
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TMPDIR=/tmp
ENTRYPOINT ["python", "-I", "/opt/aeion/run_tests.py"]
