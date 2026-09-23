"""Process lifecycle for NeMo Relay traces.

Relay owns observability only: Deep Agent middleware emits lifecycle events,
this module persists those events as append-only ATOF and, when explicitly
enabled, exports the same trace hierarchy to LangSmith over OTLP.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from nemo_relay import plugin, subscribers
from nemo_relay.observability import (
    AtofConfig,
    AtofFileSinkConfig,
    ComponentSpec,
    ObservabilityConfig,
    OpenTelemetryEndpointConfig,
    OpenTelemetrySectionConfig,
)
from nemo_relay.plugin import PluginConfig, PluginHostActivation, PluginHostReport

DEFAULT_TRACE_DIRECTORY = Path("/srv/market-shock/traces")
DEFAULT_TRACE_FILENAME = "relay-events.jsonl"
DEFAULT_LANGSMITH_BASE_URL = "https://api.smith.langchain.com"
LANGSMITH_API_KEY_ENV = "LANGSMITH_API_KEY"
LANGSMITH_PROJECT_ENV = "LANGSMITH_PROJECT"


class RelayTracingConfigurationError(ValueError):
    """Raised when tracing configuration is incomplete or unsafe."""


def _enabled(value: str | None, *, variable: str) -> bool:
    normalized = (value or "false").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise RelayTracingConfigurationError(f"{variable} must be a boolean")


def _langsmith_endpoint(environment: Mapping[str, str]) -> str:
    explicit = environment.get("NEMO_RELAY_LANGSMITH_ENDPOINT", "").strip()
    if explicit:
        return explicit
    base = environment.get("LANGSMITH_ENDPOINT", DEFAULT_LANGSMITH_BASE_URL).strip()
    return urljoin(f"{base.rstrip('/')}/", "otel/v1/traces")


@dataclass(frozen=True, slots=True)
class RelayTracingSettings:
    """Non-secret settings for Relay's process-wide observability plugin."""

    trace_directory: Path = DEFAULT_TRACE_DIRECTORY
    trace_filename: str = DEFAULT_TRACE_FILENAME
    langsmith_enabled: bool = False
    langsmith_endpoint: str = f"{DEFAULT_LANGSMITH_BASE_URL}/otel/v1/traces"
    langsmith_api_key_env: str = LANGSMITH_API_KEY_ENV
    langsmith_project_env: str = LANGSMITH_PROJECT_ENV
    service_name: str = "market-shock-agent"

    def __post_init__(self) -> None:
        if not self.trace_directory.is_absolute():
            raise RelayTracingConfigurationError("NEMO_RELAY_TRACE_DIRECTORY must be absolute")
        if not self.trace_filename or Path(self.trace_filename).name != self.trace_filename:
            raise RelayTracingConfigurationError("NEMO_RELAY_TRACE_FILENAME must be a filename")
        if self.langsmith_enabled:
            endpoint = urlsplit(self.langsmith_endpoint)
            if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
                raise RelayTracingConfigurationError("LangSmith OTLP endpoint must be an HTTP(S) URL")
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise RelayTracingConfigurationError("LangSmith OTLP endpoint must not contain credentials or a query")

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> RelayTracingSettings:
        """Read names and non-secret values without resolving credentials."""

        values = os.environ if environment is None else environment
        return cls(
            trace_directory=Path(
                values.get("NEMO_RELAY_TRACE_DIRECTORY", str(DEFAULT_TRACE_DIRECTORY))
            ),
            trace_filename=values.get("NEMO_RELAY_TRACE_FILENAME", DEFAULT_TRACE_FILENAME),
            langsmith_enabled=_enabled(
                values.get("NEMO_RELAY_LANGSMITH_ENABLED"),
                variable="NEMO_RELAY_LANGSMITH_ENABLED",
            ),
            langsmith_endpoint=_langsmith_endpoint(values),
        )

    @property
    def trace_path(self) -> Path:
        return self.trace_directory / self.trace_filename


def _build_plugin_config(settings: RelayTracingSettings) -> PluginConfig:
    otel = None
    if settings.langsmith_enabled:
        otel = OpenTelemetrySectionConfig(
            enabled=True,
            endpoints=[
                OpenTelemetryEndpointConfig(
                    type="gen_ai",
                    endpoint=settings.langsmith_endpoint,
                    transport="http_binary",
                    service_name=settings.service_name,
                    instrumentation_scope="nemo-relay",
                    header_env={
                        "x-api-key": settings.langsmith_api_key_env,
                        "Langsmith-Project": settings.langsmith_project_env,
                    },
                    resource_attributes={"service.name": settings.service_name},
                )
            ],
        )
    observability = ObservabilityConfig(
        atof=AtofConfig(
            enabled=True,
            sinks=[
                AtofFileSinkConfig(
                    output_directory=str(settings.trace_directory),
                    filename=settings.trace_filename,
                    mode="append",
                )
            ],
        ),
        opentelemetry=otel,
        enable_full_payloads=False,
    )
    return PluginConfig(components=[ComponentSpec(observability)])


class RelayTracing:
    """Own the process-wide Relay observability plugin lifecycle."""

    def __init__(self, settings: RelayTracingSettings | None = None) -> None:
        self.settings = settings or RelayTracingSettings.from_env()
        self._activation: PluginHostActivation | None = None

    @property
    def started(self) -> bool:
        return self._activation is not None and self._activation.is_active

    @property
    def trace_path(self) -> Path:
        return self.settings.trace_path

    async def start(self) -> PluginHostReport:
        """Create the trace directory and activate ATOF/optional OTLP export."""

        if self.started:
            assert self._activation is not None
            return self._activation.report
        if self.settings.langsmith_enabled:
            missing = [
                name
                for name in (
                    self.settings.langsmith_api_key_env,
                    self.settings.langsmith_project_env,
                )
                if not os.environ.get(name)
            ]
            if missing:
                names = ", ".join(missing)
                raise RelayTracingConfigurationError(f"LangSmith export requires environment variable(s): {names}")
        self.settings.trace_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        activation = await plugin.initialize(_build_plugin_config(self.settings))
        self._activation = activation
        return activation.report

    async def shutdown(self) -> None:
        """Flush queued events and tear down exporters without blocking asyncio."""

        activation = self._activation
        if activation is None:
            return
        try:
            await subscribers.flush_async()
        finally:
            try:
                await activation.close()
            finally:
                # Relay keeps a failed activation active so teardown can be
                # retried. Drop only a handle that has actually released its
                # process-wide registrations.
                if not activation.is_active:
                    self._activation = None


__all__ = [
    "RelayTracing",
    "RelayTracingConfigurationError",
    "RelayTracingSettings",
]
