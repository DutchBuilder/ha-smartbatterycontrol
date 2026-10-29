"""Client voor de API van SmartBatteryControl (lokaal, met een API-token).

Bewust zonder Home Assistant-imports, zodat hij los te testen is.
"""
from __future__ import annotations

import asyncio
from datetime import date

import aiohttp


class SbcError(Exception):
    """SmartBatteryControl is niet bereikbaar of gaf een fout."""


class SbcAuthError(SbcError):
    """Het API-token ontbreekt of klopt niet."""


class SbcClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int = 80, token: str = "", timeout: float = 15):
        self._session = session
        self.base = f"http://{host}:{port}/api"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, params: dict | None = None):
        try:
            async with self._session.get(f"{self.base}{path}", params=params, headers=self._headers,
                                         timeout=self._timeout) as response:
                if response.status in (401, 403):
                    raise SbcAuthError("API-token ontbreekt of klopt niet")
                if response.status != 200:
                    raise SbcError(f"HTTP {response.status} voor {path}")
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise SbcError(f"niet bereikbaar: {exc!r}") from exc

    async def version(self) -> str:
        return (await self._get("/health")).get("version", "?")

    async def state(self) -> dict:
        return await self._get("/state")

    async def calculation(self, battery_id: str, day: date) -> dict:
        return await self._get(f"/plan/{battery_id}/calculation", {"day": day.isoformat()})

    async def metrics(self, hours: float) -> dict:
        return await self._get("/metrics", {"hours": f"{hours:.3f}"})
