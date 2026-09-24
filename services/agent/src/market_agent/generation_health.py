"""Process-local admission protection after observed local generation timeouts.

This diagnoses generation availability, not the cause of an inference stall.
Only readiness polling can recover the circuit; user admission never runs a probe.
"""

import asyncio
from datetime import datetime, UTC
import hmac
import logging
import secrets
from collections.abc import AsyncIterator
from time import monotonic

import httpx
from fastapi import HTTPException, Request

from .config import LOCAL_MODEL

log = logging.getLogger(__name__)


class GenerationHealth:
    def __init__(self, cooldown: float = 30, timeout: float = 20):
        self.cooldown, self.timeout = cooldown, timeout
        self.blocked, self.checking = True, False
        self.retry_at, self.generation = 0.0, 0
        self.owner = None
        self.maintenance = None

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def acquire_maintenance(self) -> dict[str, str]:
        """Atomically reserve an idle process for one operator procedure."""
        if self.owner is not None or self.checking or self.maintenance is not None:
            raise HTTPException(
                status_code=409, detail="The agent is not idle for maintenance.", headers={"Retry-After": "2"}
            )
        lease_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        acquired_at = self._timestamp()
        self.maintenance = {"lease_id": lease_id, "token": token, "acquired_at": acquired_at}
        return {"lease_id": lease_id, "token": token, "acquired_at": acquired_at}

    def _require_maintenance(self, token: str | None) -> dict[str, str]:
        lease = self.maintenance
        if lease is None or not isinstance(token, str) or not hmac.compare_digest(token, lease["token"]):
            raise HTTPException(status_code=403, detail="A valid maintenance lease is required.")
        return lease

    def release_maintenance(self, token: str | None) -> dict[str, str]:
        lease = self._require_maintenance(token)
        if self.checking:
            raise HTTPException(
                status_code=409,
                detail="The maintenance generation check is still running.",
                headers={"Retry-After": "2"},
            )
        self.maintenance = None
        return {
            "lease_id": lease["lease_id"],
            "acquired_at": lease["acquired_at"],
            "released_at": self._timestamp(),
        }

    def request_fresh_check(self, maintenance_token: str | None = None) -> None:
        if self.maintenance is not None:
            self._require_maintenance(maintenance_token)
        elif maintenance_token is not None:
            raise HTTPException(status_code=403, detail="A valid maintenance lease is required.")
        if (
            self.owner is not None
            or self.checking
            or (self.maintenance is None and monotonic() < self.retry_at)
        ):
            raise HTTPException(
                status_code=409,
                detail="Generation verification needs an idle agent and an elapsed diagnostic cooldown. Try again shortly.",
                headers={"Retry-After": "30"},
            )
        self.blocked = True
        self.generation += 1
        if self.maintenance is not None:
            self.retry_at = 0.0

    def observe_failure(self, model_id: str, failure: str) -> None:
        if model_id != LOCAL_MODEL or failure != "timeout":
            return
        self.blocked = True
        self.generation += 1
        self.retry_at = monotonic() + self.cooldown
        log.warning("readiness_diagnostic local_generation_timeout")

    async def check(self, settings, client: httpx.AsyncClient, maintenance_token: str | None = None) -> bool:
        if self.maintenance is not None:
            try:
                self._require_maintenance(maintenance_token)
            except HTTPException:
                return False
        if not self.blocked:
            return True
        if self.checking or self.owner is not None or monotonic() < self.retry_at:
            return False
        self.checking = True
        generation, verified = self.generation, False
        try:
            # Separate diagnostic: no user text, credentials, routing or retries.
            async with asyncio.timeout(self.timeout):
                response = await client.post(
                    settings.model_url + "/chat/completions",
                    json={
                        "model": LOCAL_MODEL,
                        "messages": [{"role": "user", "content": "Reply OK."}],
                        "max_tokens": 2,
                        "temperature": 0,
                        "stream": False,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
                message = body["choices"][0]["message"]
                completion_tokens = body.get("usage", {}).get("completion_tokens")
                verified = (
                    body.get("model") == LOCAL_MODEL
                    and isinstance(message.get("content"), str)
                    and bool(message["content"].strip())
                    and type(completion_tokens) is int
                    and completion_tokens > 0
                )
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            pass
        finally:
            self.checking = False
            self.retry_at = monotonic() + self.cooldown
            if verified and generation == self.generation:
                self.blocked = False
            log.warning(
                "readiness_diagnostic generation_canary %s", "verified" if not self.blocked else "unavailable"
            )
        return not self.blocked


generation_health = GenerationHealth()


async def require_generation(request: Request) -> AsyncIterator[None]:
    work = request.method == "POST" and not request.url.path.endswith("/cancel")
    if work and generation_health.maintenance is not None:
        raise HTTPException(
            status_code=503,
            detail="The agent is temporarily reserved for operator maintenance. Try again shortly.",
            headers={"Retry-After": "2"},
        )
    if work and generation_health.blocked:
        raise HTTPException(
            status_code=503,
            detail="Local model generation is temporarily unavailable. Wait for the readiness check to recover, then try again.",
            headers={"Retry-After": "30"},
        )
    if work and generation_health.owner is not None:
        raise HTTPException(
            status_code=409,
            detail="Another investigation is running. Wait for it to finish or cancel it before starting another.",
            headers={"Retry-After": "2"},
        )
    token = object() if work and request.url.path.rsplit("/", 1)[-1] in {"run", "turn", "retry"} else None
    if token is not None:
        generation_health.owner = token
    try:
        yield
    finally:
        if token is not None and generation_health.owner is token:
            generation_health.owner = None
