"""De planning van elke batterij als agenda: elk laad- en ontlaadblok van vandaag en morgen is een afspraak.

Zo zie je de geplande uren in de agenda van Home Assistant, en kun je automatiseren op het begin of eind van een
blok (een agenda-trigger). De afspraak die nu loopt (of als eerste komt) is de toestand van de agenda.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SbcConfigEntry
from .entity import SbcBatteryEntity, plan_blocks


def _event(start: datetime, end: datetime, slot: dict, battery: str) -> CalendarEvent:
    laden = slot["power_w"] < 0
    kw = f"{abs(slot['power_w']) / 1000:.1f}".replace(".", ",")
    return CalendarEvent(start=start, end=end, summary=f"{'Laden' if laden else 'Ontladen'} {kw} kW",
                         description=f"{battery}: gepland door SmartBatteryControl ({abs(slot['power_w'])} W)",
                         uid=f"{start.isoformat()}-{slot['power_w']}")


class PlanCalendar(SbcBatteryEntity, CalendarEntity):
    def __init__(self, entry: SbcConfigEntry, battery: dict) -> None:
        super().__init__(entry, battery, "plan_calendar")

    def _events(self) -> list[CalendarEvent]:
        return [_event(s, e, slot, self.battery.get("name", "")) for s, e, slot in plan_blocks(self.battery)]

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        return next((e for e in self._events() if e.end > now), None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        return [e for e in self._events() if e.end > start_date and e.start < end_date]


async def async_setup_entry(hass: HomeAssistant, entry: SbcConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    state = entry.runtime_data.state
    known: set[str] = set()

    @callback
    def add_batteries() -> None:
        new = [b for b in (state.data or {}).get("batteries", []) if b["id"] not in known]
        known.update(b["id"] for b in new)
        async_add_entities(PlanCalendar(entry, b) for b in new)

    add_batteries()
    entry.async_on_unload(state.async_add_listener(add_batteries))
