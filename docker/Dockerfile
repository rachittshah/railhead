# Railhead environment image: patched OpenTTD 15.3 (dedicated) + RailheadBridge GS + railhead CLI/daemon
# + every task's sealed start state. One image serves all Harbor tasks (task picked via RAILHEAD_TASK).
#   docker build -f docker/Dockerfile -t railhead:dev .
ARG DEBIAN=trixie

FROM debian:${DEBIAN}-slim AS engine
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build pkg-config git ca-certificates curl unzip \
      zlib1g-dev liblzma-dev liblzo2-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY engine/ engine/
RUN RAILHEAD_ENGINE_SRC=/build/openttd-src PREFIX=/opt/railhead/engine engine/build.sh \
    && rm -rf /build/openttd-src

FROM debian:${DEBIAN}-slim
ARG RAILHEAD_VERSION=0.1.0
LABEL org.opencontainers.image.source="https://github.com/rachittshah/railhead" \
      org.opencontainers.image.description="Railhead: models play OpenTTD directly (RL environment)" \
      org.opencontainers.image.licenses="MIT AND GPL-2.0-only"
# Runtime libs for the engine, python for railhead, and the basics Harbor agents expect (bash, tmux, curl).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv zlib1g liblzma5 liblzo2-2 ca-certificates curl tmux procps less jq \
    && rm -rf /var/lib/apt/lists/*
COPY --from=engine /opt/railhead/engine /opt/railhead/engine
COPY pyproject.toml README.md /opt/railhead/src/
COPY src/ /opt/railhead/src/src/
COPY gamescript/ /opt/railhead/scripts/
RUN python3 -m venv /opt/railhead/venv \
    && /opt/railhead/venv/bin/pip install --no-cache-dir /opt/railhead/src \
    && ln -s /opt/railhead/venv/bin/rh /usr/local/bin/rh \
    && ln -s /opt/railhead/venv/bin/railhead /usr/local/bin/railhead
COPY tasks/ /opt/railhead/tasks/
COPY docker/railhead-up docker/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/railhead-up /usr/local/bin/entrypoint.sh \
    && mkdir -p /var/lib/railhead /app
ENV RAILHEAD_OPENTTD=/opt/railhead/engine/openttd \
    RAILHEAD_SCRIPTS=/opt/railhead/scripts \
    RAILHEAD_TASKS=/opt/railhead/tasks \
    RAILHEAD_STATE=/var/lib/railhead \
    RAILHEAD_VERSION=${RAILHEAD_VERSION} \
    PYTHONUNBUFFERED=1
WORKDIR /app
# OpenTTD needs no graceful shutdown inside an ephemeral trial (Harbor teardown 10-20 s -> <1 s).
STOPSIGNAL SIGKILL
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
