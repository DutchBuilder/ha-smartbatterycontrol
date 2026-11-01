"""Basisklassen: één apparaat per batterij en één voor SmartBatteryControl zelf, en entity-id's die met sbc_ beginnen."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN, MANUFACTURER, PREFIX
from .coordinator import DetailCoordinator, SbcConfigEntry, StateCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import EntityPlatform


class SbcEntityId(Entity):
    """De entity-id van een nieuwe entiteit begint met sbc_, voor wat Home Assistant zelf zou kiezen (het apparaat en de
    naam): sensor.sbc_venus_thuis_laadniveau. Een entiteit die al bestaat, houdt haar entity-id."""

    def add_to_platform_start(self, hass: HomeAssistant, platform: EntityPlatform,
                              parallel_updates: asyncio.Semaphore | None) -> None:
        super().add_to_platform_start(hass, platform, parallel_updates)
        if self.entity_id is not None or self.unique_id is None or \
                er.async_get(hass).async_get_entity_id(platform.domain, platform.platform_name, self.unique_id):
            return
        name = self.suggested_object_id  # de naam zoals Home Assistant hem voor entity-id's gebruikt
        info = self.device_info or {}
        device = dr.async_get(hass).async_get_device(identifiers=info.get("identifiers") or set())
        device_name = (device.name_by_user or device.name) if device else info.get("name")
        if full := " ".join(x for x in (device_name, name) if x):
            self.entity_id = f"{platform.domain}.{PREFIX}{slugify(full)}"


def prefix_entity_ids(hass: HomeAssistant, entry: SbcConfigEntry) -> int:
    """De entiteiten van deze koppeling van vóór het voorvoegsel: sbc_ ervoor (de geschiedenis gaat mee)."""
    registry = er.async_get(hass)
    renamed = 0
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        domain, object_id = split_entity_id(item.entity_id)
        if not object_id.startswith(PREFIX):
            registry.async_update_entity(item.entity_id, new_entity_id=registry.async_get_available_entity_id(domain, PREFIX + object_id))
            renamed += 1
    return renamed


def hub_device(entry: SbcConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="SmartBatteryControl", manufacturer="SmartBatteryControl",
                      sw_version=entry.runtime_data.version, configuration_url=entry.runtime_data.client.base.removesuffix("/api"))


class SbcHubEntity(SbcEntityId, CoordinatorEntity[StateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.state)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = hub_device(entry)


class SbcHubDetailEntity(SbcEntityId, CoordinatorEntity[DetailCoordinator]):
    """Van SmartBatteryControl zelf, uit wat elke 5 minuten binnenkomt (cijfers, dagtotalen, zon, controles …)."""

    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, key: str, placeholders: dict[str, str] | None = None) -> None:
        super().__init__(entry.runtime_data.details)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = hub_device(entry)
        if placeholders:
            self._attr_translation_placeholders = placeholders


class SbcBatteryEntity(SbcEntityId, CoordinatorEntity[StateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, battery: dict, key: str) -> None:
        super().__init__(entry.runtime_data.state)
        self.entry = entry
        self.battery_id = battery["id"]
        self._attr_unique_id = f"{battery['id']}_{key}"
        self._attr_translation_key = key
        anker = battery.get("brand") == "anker"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, battery["id"])}, name=battery["name"],
                                            manufacturer="Anker" if anker else MANUFACTURER,
                                            model="SOLIX Solarbank Max AC" if anker else "Venus E 3.0",
                                            via_device=(DOMAIN, entry.entry_id))

    @property
    def battery(self) -> dict:
        return next((b for b in (self.coordinator.data or {}).get("batteries", []) if b["id"] == self.battery_id), {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.battery)


def slot_text(slot: dict, soc: dict | None = None) -> str:
    kind = "laden" if slot["power_w"] < 0 else "ontladen"
    return f"{slot['start']}–{slot['end']} {kind} {abs(slot['power_w'])} W" + (f" ({soc_text(soc)})" if soc else "")


def soc_for(battery: dict, day: str, slot: dict) -> dict | None:
    """Wat een blok met het laadniveau doet ({soc_start_pct, soc_end_pct}), volgens SmartBatteryControl."""
    return next((s for s in (((battery.get("plans") or {}).get(day) or {}).get("soc") or []) if s["start"] == slot["start"]), None)


def soc_text(step: dict | None) -> str:
    return f"{step['soc_start_pct']}% → {step['soc_end_pct']}%" if step else ""


def plan_blocks(battery: dict) -> list[tuple[datetime, datetime, dict]]:
    """Alle geplande blokken van vandaag en morgen als (begin, eind, slot); 23:59 telt als het eind van de dag."""
    tz = dt_util.get_default_time_zone()
    out = []
    for day, plan in sorted((battery.get("plans") or {}).items()):
        d = datetime.fromisoformat(day).date()
        for slot in plan.get("slots", []):
            if not slot.get("enabled") or not slot.get("power_w"):
                continue
            start = datetime.combine(d, time.fromisoformat(slot["start"]), tzinfo=tz)
            end = datetime.combine(d, time.fromisoformat(slot["end"]), tzinfo=tz)
            if slot["end"] == "23:59":
                end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz)
            if end > start:
                out.append((start, end, {**slot, "soc": soc_for(battery, day, slot)}))
    return sorted(out, key=lambda x: x[0])


def schedule_text(battery: dict, day: str) -> str | None:
    """De blokken van één dag als één regel (een sensor mag hooguit 255 tekens), of "geen" zonder blokken."""
    plan = (battery.get("plans") or {}).get(day)
    if plan is None:
        return None
    parts = [slot_text(s, soc_for(battery, day, s)) for s in plan.get("slots", []) if s.get("enabled") and s.get("power_w")]
    text = ", ".join(parts) or "geen blokken"
    return text if len(text) <= 255 else text[:252] + "…"


def next_block(battery: dict) -> tuple[datetime, dict] | None:
    """Het eerstvolgende blok uit de plannen van vandaag en morgen."""
    now = dt_util.now()
    for day, plan in sorted((battery.get("plans") or {}).items()):
        for slot in plan.get("slots", []):
            if not slot.get("enabled"):
                continue
            start = datetime.combine(datetime.fromisoformat(day).date(), time.fromisoformat(slot["start"]),
                                     tzinfo=dt_util.get_default_time_zone())
            if start > now:
                return start, slot
    return None
