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


class SbcForbidden(SbcError):
    """Het token is goed, maar dit mag het niet (bijv. iets wat alleen vanuit de webinterface kan)."""


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
                if response.status == 401:
                    raise SbcAuthError("API-token ontbreekt of klopt niet")
                if response.status == 403:
                    raise SbcForbidden(f"{path} mag dit API-token niet")
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

    async def details(self, battery_id: str, max_age: int = 600) -> dict:
        """Alle sensoren van een batterij, uit de cache van SmartBatteryControl (die houdt ze zelf vers)."""
        return await self._get(f"/batteries/{battery_id}/details", {"max_age": str(max_age)})

    async def review(self, battery_id: str, day: date) -> dict:
        """Plan tegen werkelijkheid van één dag (vandaag: tot nu): wat laden kostte en ontladen opleverde."""
        return await self._get(f"/plan/{battery_id}/review", {"day": day.isoformat()})

    async def plug_usage(self, since: float) -> dict:
        """Verbruik en kosten per stekker vanaf `since` (Unix-tijd): totaal, per dag en de gemiddelde kWh-prijs."""
        return await self._get("/plugs/usage", {"since": f"{since:.0f}"})

    async def reviews(self, battery_id: str, days: int = 31) -> dict:
        """De bewaarde controles van de afgelopen dagen (tot en met gisteren)."""
        return await self._get(f"/plan/{battery_id}/reviews", {"days": str(days)})

    async def stats(self) -> dict:
        """Alle cijfers van de schermen in één blok: per periode (vandaag, 7 dagen, 30 dagen, dit jaar), per batterij, de
        zon, het verbruikspatroon en de health-controles. SmartBatteryControl bewaart wat langzaam verandert een tijdje."""
        return await self._get("/stats")

    async def day_totals(self, day: date) -> dict:
        """Alle batterijen samen op één dag: gemeten tot nu, daarna gepland, met de gemiddelde prijs per kWh."""
        return await self._get("/plan/totals", {"day": day.isoformat()})

    async def audit(self, since: float, until: float) -> dict:
        """De historie van de slots per dag (nieuwste eerst), met wat elk slot kostte of opleverde."""
        return await self._get("/plan/audit", {"since": f"{since:.0f}", "until": f"{until:.0f}"})

    async def pv(self) -> dict:
        """Het zonmodel: de voorspellers met hun verwachting, hoe goed ze waren en de zon tegen een gewoon jaar."""
        return await self._get("/pv")

    async def updates(self) -> dict:
        return await self._get("/updates")
