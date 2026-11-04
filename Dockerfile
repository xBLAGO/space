FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS sqlite-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY tools/build_sqlite.py /build/build_sqlite.py
RUN python /build/build_sqlite.py --prefix /opt/blago-sqlite/lib

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV LD_LIBRARY_PATH=/opt/blago-sqlite/lib

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git git-lfs \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md requirements-integration.lock ./
COPY src ./src
COPY --from=sqlite-build /opt/blago-sqlite/lib /opt/blago-sqlite/lib

RUN pip install --no-cache-dir -r requirements-integration.lock \
    && pip install --no-cache-dir --no-deps .
RUN python -m xblago.core.sqlite_safety --require-safe

EXPOSE 7860

CMD ["xblago-integration"]
