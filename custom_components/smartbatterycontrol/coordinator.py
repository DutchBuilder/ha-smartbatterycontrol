"""Gegevens ophalen: de toestand elke 15 seconden, de doorrekening van de plannen elke 5 minuten."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SbcAuthError, SbcClient, SbcError
from .const import DETAIL_INTERVAL, DOMAIN, STATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class SbcData:
    client: SbcClient
    state: "StateCoordinator"
    details: "DetailCoordinator"
    version: str = "?"


type SbcConfigEntry = ConfigEntry[SbcData]


class StateCoordinator(DataUpdateCoordinator[dict]):
    """/api/state: batterijen, meter, zon, prijzen en plannen."""

    def __init__(self, hass: HomeAssistant, entry: SbcConfigEntry, client: SbcClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} toestand", update_interval=STATE_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            return await self.client.state()
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SbcError as exc:
            raise UpdateFailed(str(exc)) from exc


@dataclass
class Details:
    calculations: dict[str, dict[str, dict]] = field(default_factory=dict)  # batterij → "today"/"tomorrow" → doorrekening
    metrics: dict = field(default_factory=dict)  # kerncijfers sinds middernacht


class DetailCoordinator(DataUpdateCoordinator[Details]):
    """Doorrekening van de plannen van vandaag en morgen, en de kerncijfers van vandaag."""

    def __init__(self, hass: HomeAssistant, entry: SbcConfigEntry, client: SbcClient, state: StateCoordinator) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} details", update_interval=DETAIL_INTERVAL)
        self.client = client
        self.state = state

    async def _async_update_data(self) -> Details:
        now = dt_util.now()
        midnight = dt_util.start_of_local_day(now)
        details = Details()
        try:
            details.metrics = await self.client.metrics((now - midnight).total_seconds() / 3600)
            for battery in (self.state.data or {}).get("batteries", []):
                per_day = {}
                for key, day in (("today", now.date()), ("tomorrow", now.date() + timedelta(days=1))):
                    if day.isoformat() in battery.get("plans", {}):
                        try:
                            per_day[key] = await self.client.calculation(battery["id"], day)
                        except SbcError as exc:  # bijv. nog geen prijzen voor morgen
                            _LOGGER.debug("Geen doorrekening voor %s %s: %s", battery["name"], day, exc)
                details.calculations[battery["id"]] = per_day
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SbcError as exc:
            raise UpdateFailed(str(exc)) from exc
        return details
