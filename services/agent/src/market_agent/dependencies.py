import httpx
from .config import LOCAL_MODEL, Settings
from .generation_health import generation_health


async def probe(
    settings: Settings, *, verify_generation: bool = True, maintenance_token: str | None = None
) -> dict:
    out = {"tools": False, "model": False, "mode": "foundation"}
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            out["tools"] = (
                await client.get(settings.tools_url.removesuffix("/mcp") + "/health")
            ).status_code == 200
        except httpx.HTTPError:
            pass
        try:
            data = (await client.get(settings.model_url + "/models")).json()
            out["model"] = any(item.get("id") == LOCAL_MODEL for item in data.get("data", []))
        except (httpx.HTTPError, ValueError):
            pass
        if out["model"]:
            out["model"] = (
                await generation_health.check(settings, client, maintenance_token)
                if verify_generation
                else not generation_health.blocked
            )
    return out
