"""Gegevens ophalen: de toestand elke 15 seconden, de sensoren van de batterijen elke minuut (uit de cache van
SmartBatteryControl), en de doorrekening van de plannen met kosten en opbrengst elke 5 minuten."""
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
from .const import DETAIL_INTERVAL, DOMAIN, SENSOR_INTERVAL, STATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

MONEY_KEYS = ("charge_kwh", "discharge_kwh", "cost_eur", "value_eur", "wear_eur", "margin_eur")


@dataclass
class SbcData:
    client: SbcClient
    state: "StateCoordinator"
    details: "DetailCoordinator"
    sensors: "SensorCoordinator"
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
    # batterij → "today"/"month" → {charge_kwh, discharge_kwh, cost_eur, value_eur, wear_eur, margin_eur}: wat er werkelijk gebeurde
    money: dict[str, dict[str, dict]] = field(default_factory=dict)


def add_money(totals: dict, actual: dict) -> None:
    for key in MONEY_KEYS:
        totals[key] = round(totals.get(key, 0.0) + (actual.get(key) or 0.0), 4)


class DetailCoordinator(DataUpdateCoordinator[Details]):
    """Doorrekening van de plannen van vandaag en morgen, kosten en opbrengst (vandaag en deze maand), kerncijfers."""

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
                details.money[battery["id"]] = await self._money(battery, now)
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SbcError as exc:
            raise UpdateFailed(str(exc)) from exc
        return details

    async def _money(self, battery: dict, now) -> dict:
        """Vandaag tot nu, en deze maand: de bewaarde dagen van deze maand plus vandaag."""
        out: dict[str, dict] = {}
        try:
            today = (await self.client.review(battery["id"], now.date())).get("actual") or {}
            out["today"] = {k: today.get(k) for k in MONEY_KEYS}
        except SbcAuthError:
            raise
        except SbcError as exc:  # bijv. nog geen prijzen: dan geen kosten te berekenen
            _LOGGER.debug("Geen controle van vandaag voor %s: %s", battery["name"], exc)
            return out
        month = now.strftime("%Y-%m")
        totals: dict = {}
        try:
            for day in (await self.client.reviews(battery["id"], now.day)).get("days", []):
                if str(day.get("day", "")).startswith(month):
                    add_money(totals, day.get("actual") or {})
        except SbcAuthError:
            raise
        except SbcError as exc:
            _LOGGER.debug("Geen bewaarde controles voor %s: %s", battery["name"], exc)
        add_money(totals, out["today"])
        out["month"] = totals
        return out


class SensorCoordinator(DataUpdateCoordinator[dict[str, list[dict]]]):
    """Alle sensoren van elke batterij (spanning, temperaturen, cellen, energietellers, instellingen …).

    SmartBatteryControl leest ze zelf op de achtergrond uit en bewaart ze; wij vragen de bewaarde waarden (tot 10
    minuten oud). Zo kost Home Assistant de batterij geen extra verzoeken, ook niet met veel sensoren.
    """

    def __init__(self, hass: HomeAssistant, entry: SbcConfigEntry, client: SbcClient, state: StateCoordinator) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} sensoren", update_interval=SENSOR_INTERVAL)
        self.client = client
        self.state = state

    async def _async_update_data(self) -> dict[str, list[dict]]:
        out = dict(self.data or {})
        try:
            for battery in (self.state.data or {}).get("batteries", []):
                try:
                    out[battery["id"]] = (await self.client.details(battery["id"])).get("sensors", [])
                except SbcAuthError:
                    raise
                except SbcError as exc:  # één batterij onbereikbaar: de andere gewoon bijwerken, deze houdt zijn laatste waarden
                    _LOGGER.debug("Sensoren van %s niet op te halen: %s", battery["name"], exc)
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        return out
