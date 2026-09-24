# The GPU base image is built from base.Dockerfile.
ARG TOOLS_FOUNDATION_IMAGE=market-shock/tools-foundation:phase0
FROM ${TOOLS_FOUNDATION_IMAGE}
ARG MARKET_SHOCK_BUILD_INPUT_SHA256=unverified
LABEL com.nvidia.market-shock.build-input-schema="market-shock-build-input-v1" \
      com.nvidia.market-shock.build-input-sha256="${MARKET_SHOCK_BUILD_INPUT_SHA256}"
WORKDIR /opt/market-tools
COPY pyproject.toml .
COPY src src
RUN python -m pip install --no-cache-dir . && useradd --uid 10001 --create-home app
USER 10001
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "market_tools.app:app", "--host", "0.0.0.0", "--port", "8000"]
