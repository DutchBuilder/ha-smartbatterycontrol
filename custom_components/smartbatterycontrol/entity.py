"""Basisklassen: één apparaat per batterij en één voor SmartBatteryControl zelf."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import DetailCoordinator, SbcConfigEntry, StateCoordinator


def hub_device(entry: SbcConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="SmartBatteryControl", manufacturer="SmartBatteryControl",
                      sw_version=entry.runtime_data.version, configuration_url=entry.runtime_data.client.base.removesuffix("/api"))


class SbcHubEntity(CoordinatorEntity[StateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.state)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = hub_device(entry)


class SbcHubDetailEntity(CoordinatorEntity[DetailCoordinator]):
    """Van SmartBatteryControl zelf, uit wat elke 5 minuten binnenkomt (cijfers, dagtotalen, zon, controles …)."""

    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, key: str, placeholders: dict[str, str] | None = None) -> None:
        super().__init__(entry.runtime_data.details)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = hub_device(entry)
        if placeholders:
            self._attr_translation_placeholders = placeholders


class SbcBatteryEntity(CoordinatorEntity[StateCoordinator]):
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
