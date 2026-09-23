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
      com.nvidia.market-shock.nemo-relay-version="0.9.0rc3" \
      com.nvidia.market-shock.switchyard-commit="1759dfdff95ed2718f6dd2afef6541654bbd0116" \
      com.nvidia.market-shock.langchain-nvidia-commit="9aa3de853238aa0bcddb799c733a2e189e98078f"
WORKDIR /opt/market-agent
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iptables nftables ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
COPY src src
COPY skills skills
COPY --from=wheels /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl . \
    && rm -rf /wheels \
    && useradd --uid 10001 --create-home app \
    && install -d -o app -g app -m 0700 /srv/market-shock/state /srv/market-shock/traces
USER 10001
WORKDIR /sandbox
EXPOSE 2024
# OpenShell gives the workload a separate network namespace. Docker's outer
# namespace cannot probe its loopback; operator status probes /health/ready
# through sandbox exec and fails closed on application/dependency failure.
HEALTHCHECK NONE
CMD ["uvicorn", "market_agent.app:app", "--host", "0.0.0.0", "--port", "2024"]
