"""Gegevens ophalen: de toestand elke 15 seconden, de sensoren van de batterijen elke minuut (uit de cache van
SmartBatteryControl), en elke 5 minuten de doorrekening van de plannen met kosten en opbrengst, de cijfers per periode,
de dagtotalen van alle batterijen, de historie van de slots, het zonmodel, de controles, de opslag en de updates."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SbcAuthError, SbcClient, SbcError
from .const import DETAIL_INTERVAL, DOMAIN, PV_INTERVAL, SENSOR_INTERVAL, STATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

MONEY_KEYS = ("charge_kwh", "discharge_kwh", "cost_eur", "value_eur", "wear_eur", "margin_eur")
FINISHED = ("ok", "full", "empty", "deviation")  # een slot dat klaar is (niet bezig, geen zelfconsumptie)


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
    # "today"/"week"/"month"/"year" → kerncijfers van die periode (energie, euro's, percentages, gemiddelde prijzen),
    # met "batteries" per batterij-id; een oudere SmartBatteryControl zonder /api/stats geeft alleen "today"
    periods: dict[str, dict] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)  # de rest van /api/stats: batterijen (dagcontroles), zon, zonmodel, patroon, health
    # batterij → "today"/"month" → {charge_kwh, discharge_kwh, cost_eur, value_eur, wear_eur, margin_eur}: wat er werkelijk gebeurde
    money: dict[str, dict[str, dict]] = field(default_factory=dict)
    plugs: dict[str, dict] = field(default_factory=dict)  # stekker → verbruik en kosten deze maand (kwh, cost_eur, avg_eur_per_kwh, days)
    totals: dict[str, dict] = field(default_factory=dict)  # "today"/"tomorrow" → alle batterijen samen (gemeten, daarna gepland)
    last_slots: dict[str, dict] = field(default_factory=dict)  # batterij → het laatste laad- of ontlaadslot dat klaar is
    pv: dict = field(default_factory=dict)  # het zonmodel: voorspellers, nauwkeurigheid, tegen een gewoon jaar
    updates: dict = field(default_factory=dict)
    storage: dict = field(default_factory=dict)


def add_money(totals: dict, actual: dict) -> None:
    for key in MONEY_KEYS:
        totals[key] = round(totals.get(key, 0.0) + (actual.get(key) or 0.0), 4)


def per_kwh(kind: str, eur: float | None, kwh: float | None) -> float | None:
    """Gemiddeld per kWh, zoals in de historie van SmartBatteryControl: bij laden wat het kostte, bij ontladen wat het opleverde."""
    if eur is None or not kwh or kwh < 0.05:
        return None
    return round((-eur if kind == "charge" else eur) / kwh, 4)


def last_slots(audit: dict) -> dict[str, dict]:
    """Per batterij het laatste laad- of ontlaadslot dat klaar is, uit de historie (de nieuwste dag eerst)."""
    out: dict[str, dict] = {}
    for day in audit.get("days", []):
        for battery in day.get("batteries", []):
            if battery["id"] in out:
                continue
            done = [b for b in battery.get("blocks", []) if b.get("kind") in ("charge", "discharge") and b.get("status") in FINISHED
                    and (b.get("actual_kwh") or 0) >= 0.05]
            if done:
                out[battery["id"]] = {**done[-1], "day": day["day"]}
    return out


class DetailCoordinator(DataUpdateCoordinator[Details]):
    """Doorrekening van de plannen van vandaag en morgen, kosten en opbrengst (vandaag en deze maand), kerncijfers."""

    def __init__(self, hass: HomeAssistant, entry: SbcConfigEntry, client: SbcClient, state: StateCoordinator) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} details", update_interval=DETAIL_INTERVAL)
        self.client = client
        self.state = state
        self._pv: tuple[datetime, dict] | None = None  # het zonmodel en wanneer het is opgehaald

    async def _async_update_data(self) -> Details:
        now = dt_util.now()
        midnight = dt_util.start_of_local_day(now)
        details = Details()
        try:
            details.periods, details.stats = await self._periods(now, midnight)
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
            if (self.state.data or {}).get("plugs"):  # verbruik en kosten per stekker deze maand
                usage = await self._optional("verbruik per stekker", self.client.plug_usage(midnight.replace(day=1).timestamp()))
                details.plugs = {p["id"]: p for p in (usage or {}).get("plugs", [])}
            for key, day in (("today", now.date()), ("tomorrow", now.date() + timedelta(days=1))):
                if (totals := await self._optional(f"dagtotaal {day}", self.client.day_totals(day))) and totals.get("batteries"):
                    details.totals[key] = totals
            # De historie van gisteren en vandaag: het laatste slot per batterij (net na middernacht nog dat van gisteren)
            audit = await self._optional("historie", self.client.audit((midnight - timedelta(days=1)).timestamp(), now.timestamp()))
            details.last_slots = last_slots(audit or {})
            if details.stats.get("pv"):  # alleen met een zonmodel (dakvlakken), en hooguit elk kwartier
                if self._pv is None or now - self._pv[0] >= PV_INTERVAL:
                    if (pv := await self._optional("zonmodel", self.client.pv())) is not None:
                        self._pv = (now, pv)
                details.pv = self._pv[1] if self._pv else {}
            details.updates = await self._optional("updates", self.client.updates()) or {}
            details.storage = await self._optional("opslag", self.client.storage()) or {}
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SbcError as exc:
            raise UpdateFailed(str(exc)) from exc
        return details

    @staticmethod
    async def _optional(what: str, call):
        """Een onderdeel dat een oudere SmartBatteryControl nog niet heeft, of dat even niet lukt: zonder dat verder."""
        try:
            return await call
        except SbcAuthError:
            raise
        except SbcError as exc:
            _LOGGER.debug("Geen %s: %s", what, exc)
            return None

    async def _periods(self, now, midnight) -> tuple[dict[str, dict], dict]:
        """De cijfers per periode uit /api/stats; een oudere SmartBatteryControl zonder: alleen vandaag uit /api/metrics."""
        stats = await self._optional("cijfers", self.client.stats())
        if stats and stats.get("periods"):
            periods = {p["key"]: {**p, "batteries": {b["id"]: b for b in p.get("batteries") or []}} for p in stats["periods"]}
            return periods, stats
        metrics = await self.client.metrics((now - midnight).total_seconds() / 3600)
        return {"today": {**(metrics.get("totals") or {}), "batteries": metrics.get("batteries") or {}}}, {}

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
        self._failing: set[str] = set()

    async def _async_update_data(self) -> dict[str, list[dict]]:
        out = dict(self.data or {})
        try:
            for battery in (self.state.data or {}).get("batteries", []):
                try:
                    out[battery["id"]] = (await self.client.details(battery["id"])).get("sensors", [])
                    if battery["id"] in self._failing:
                        self._failing.discard(battery["id"])
                        _LOGGER.info("Sensoren van %s komen weer binnen", battery["name"])
                except SbcAuthError:
                    raise
                except SbcError as exc:  # één batterij onbereikbaar: de andere gewoon bijwerken, deze houdt zijn laatste waarden
                    if battery["id"] not in self._failing:  # één keer melden, niet elke minuut
                        self._failing.add(battery["id"])
                        _LOGGER.warning("Sensoren van %s niet op te halen: %s", battery["name"], exc)
        except SbcAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        return out
