"""Aan/uit-sensoren per batterij: bereikbaar, sturen, zelfconsumptie, afwijking en schema gelijk aan het plan."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass, BinarySensorEntity,
                                                    BinarySensorEntityDescription)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SbcConfigEntry
from .entity import SbcBatteryEntity


@dataclass(frozen=True, kw_only=True)
class BatteryBinary(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]
    attrs: Callable[[dict], dict] | None = None


BINARY_SENSORS: tuple[BatteryBinary, ...] = (
    BatteryBinary(key="reachable", device_class=BinarySensorDeviceClass.CONNECTIVITY,
                  value=lambda b: b.get("error") is None and b.get("status") is not None,
                  attrs=lambda b: {"error": b.get("error")}),
    BatteryBinary(key="control", entity_category=EntityCategory.DIAGNOSTIC, value=lambda b: b.get("control"),
                  attrs=lambda b: {"schedule_access": (b.get("schedule_access") or {}).get("reason")}),
    BatteryBinary(key="self_consumption", device_class=BinarySensorDeviceClass.RUNNING,
                  value=lambda b: (b.get("realtime") or {}).get("active")),
    BatteryBinary(key="deviating", device_class=BinarySensorDeviceClass.PROBLEM,
                  value=lambda b: (b.get("power_check") or {}).get("active")),
    BatteryBinary(key="schedule_in_sync", entity_category=EntityCategory.DIAGNOSTIC,
                  value=lambda b: b.get("slots") == b.get("planned") if b.get("control") and b.get("slots") is not None else None),
)


class BatteryBinaryEntity(SbcBatteryEntity, BinarySensorEntity):
    entity_description: BatteryBinary

    def __init__(self, entry: SbcConfigEntry, battery: dict, description: BatteryBinary) -> None:
        super().__init__(entry, battery, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.battery)

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = self.entity_description.attrs
        return attrs(self.battery) if attrs else None


async def async_setup_entry(hass: HomeAssistant, entry: SbcConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    state = entry.runtime_data.state
    known: set[str] = set()

    @callback
    def add_batteries() -> None:
        new = [b for b in (state.data or {}).get("batteries", []) if b["id"] not in known]
        known.update(b["id"] for b in new)
        async_add_entities(BatteryBinaryEntity(entry, b, d) for b in new for d in BINARY_SENSORS)

    add_batteries()
    entry.async_on_unload(state.async_add_listener(add_batteries))
