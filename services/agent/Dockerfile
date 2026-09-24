FROM rust:1.96.1-slim-bookworm@sha256:e18a79fc84dfcfc3ab5ba72290398a644c135c97eaa881447fddc354ee4701a3 AS rust-toolchain

FROM python:3.12.10-slim@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS wheels
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:${PATH}
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /build/venv \
    && /build/venv/bin/pip install --no-cache-dir maturin==1.11.5 poetry-core==2.2.1

# Build the current Python libsy bindings and NVIDIA's LangChain middleware
# from immutable source revisions. No proxy, gateway, or native Relay plugin is
# part of the runtime image.
WORKDIR /build/switchyard
RUN git init . \
    && git remote add origin https://github.com/NVIDIA-NeMo/Switchyard.git \
    && git fetch --depth 1 origin 1759dfdff95ed2718f6dd2afef6541654bbd0116 \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "1759dfdff95ed2718f6dd2afef6541654bbd0116" \
    && /build/venv/bin/maturin build --release --locked --compatibility off --out /build/wheels

WORKDIR /build/langchain-nvidia
RUN git init . \
    && git remote add origin https://github.com/langchain-ai/langchain-nvidia.git \
    && git fetch --depth 1 origin 9aa3de853238aa0bcddb799c733a2e189e98078f \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "9aa3de853238aa0bcddb799c733a2e189e98078f" \
    && /build/venv/bin/pip wheel --no-deps --wheel-dir /build/wheels ./libs/switchyard

FROM python:3.12.10-slim@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
ARG MARKET_SHOCK_BUILD_INPUT_SHA256=unverified
LABEL com.nvidia.market-shock.build-input-schema="market-shock-build-input-v1" \
      com.nvidia.market-shock.build-input-sha256="${MARKET_SHOCK_BUILD_INPUT_SHA256}" \
      com.nvidia.market-shock.switchyard-commit="1759dfdff95ed2718f6dd2afef6541654bbd0116" \
      com.nvidia.market-shock.langchain-nvidia-commit="9aa3de853238aa0bcddb799c733a2e189e98078f"
WORKDIR /opt/market-agent
# Network tools available to the OpenShell sandbox supervisor that runs this image.
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iptables nftables ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src src
COPY skills skills
COPY --from=wheels /build/wheels /wheels
# Install exactly the locked versions; the two git dependencies come from the wheels built above.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes \
      --no-emit-package nemo-switchyard --no-emit-package langchain-nvidia-switchyard -o /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps -r /tmp/requirements.txt /wheels/*.whl \
    && pip install --no-cache-dir --no-deps . \
    && pip check \
    && rm -rf /wheels /tmp/requirements.txt
WORKDIR /sandbox
EXPOSE 2024
# OpenShell runs the process (as the prepared host user) and probes /health/ready itself.
HEALTHCHECK NONE
