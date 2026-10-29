"""Sensoren per batterij (laadniveau, vermogen, planning, doorrekening, kosten en opbrengst, rendement, en alle
sensoren die SmartBatteryControl uit de batterij leest) en algemeen (net, zon, prijzen)."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import (PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfElectricCurrent,
                                 UnitOfElectricPotential, UnitOfEnergy, UnitOfFrequency, UnitOfPower, UnitOfTemperature)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SbcConfigEntry, SensorCoordinator
from .entity import SbcBatteryEntity, SbcHubEntity, next_block, schedule_text, slot_text

EUR, EUR_KWH = "EUR", "EUR/kWh"


@dataclass(frozen=True, kw_only=True)
class BatterySensor(SensorEntityDescription):
    value: Callable[[dict, dict], Any]  # (batterij, details) → waarde
    attrs: Callable[[dict, dict], dict] | None = None
    details: bool = False  # waarde komt uit de doorrekening (elke 5 minuten)
    reset: str = "day"  # bij state_class TOTAL: vanaf middernacht ("day") of vanaf de eerste van de maand ("month")


def _status(key):
    return lambda b, d: (b.get("status") or {}).get(key)


def _plan(day_offset: int):
    def value(b, d):
        day = (dt_util.now().date() + timedelta(days=day_offset)).isoformat()
        plan = (b.get("plans") or {}).get(day)
        return plan["estimate_eur"] if plan else None

    def attrs(b, d):
        day = (dt_util.now().date() + timedelta(days=day_offset)).isoformat()
        plan = (b.get("plans") or {}).get(day) or {}
        return {"date": day, "cycles": plan.get("cycles"),
                "slots": [slot_text(s) for s in plan.get("slots", []) if s.get("enabled")]}
    return value, attrs


def _calc(key: str):
    """Verwachte opbrengst uit de doorrekening ("today"/"tomorrow"), met alle onderdelen als attributen."""
    def value(b, d):
        return (((d.get("calculations") or {}).get(key) or {}).get("totals") or {}).get("net_eur")

    def attrs(b, d):
        calc = (d.get("calculations") or {}).get(key) or {}
        return {**calc.get("totals", {}),
                "blocks": [f"{x['start'][11:16]}–{x['end'][11:16]} {x['kind']} {x['energy_kwh']} kWh" for x in calc.get("blocks", [])]}
    return value, attrs


def _money(period: str, key: str):
    """Wat laden kostte en ontladen opleverde (plan tegen werkelijkheid van SmartBatteryControl)."""
    return lambda b, d: ((d.get("money") or {}).get(period) or {}).get(key)


def _money_attrs(period: str):
    def attrs(b, d):
        m = (d.get("money") or {}).get(period) or {}
        return {"charged_kwh": m.get("charge_kwh"), "discharged_kwh": m.get("discharge_kwh"), "wear_eur": m.get("wear_eur")}
    return attrs


def _schedule(day_offset: int):
    def value(b, d):
        return schedule_text(b, (dt_util.now().date() + timedelta(days=day_offset)).isoformat())

    def attrs(b, d):
        day = (dt_util.now().date() + timedelta(days=day_offset)).isoformat()
        plan = (b.get("plans") or {}).get(day) or {}
        blocks = [{"start": x["start"], "end": x["end"], "kind": "laden" if x["power_w"] < 0 else "ontladen", "power_w": abs(x["power_w"])}
                  for x in plan.get("slots", []) if x.get("enabled") and x.get("power_w")]
        return {"date": day, "blocks": blocks, "hours_charging": _hours(blocks, "laden"), "hours_discharging": _hours(blocks, "ontladen")}
    return value, attrs


def _hours(blocks: list[dict], kind: str) -> float:
    def minutes(hhmm: str) -> int:
        return 1440 if hhmm == "23:59" else int(hhmm[:2]) * 60 + int(hhmm[3:])
    return round(sum(minutes(x["end"]) - minutes(x["start"]) for x in blocks if x["kind"] == kind) / 60, 2)


def _metric(key: str):
    return lambda b, d: ((d.get("metrics") or {}).get("batteries") or {}).get(b["id"], {}).get(key)


def _next_value(b, d):
    found = next_block(b)
    return found[0] if found else None


def _next_attrs(b, d):
    found = next_block(b)
    if not found:
        return {}
    start, slot = found
    return {"end": slot["end"], "power_w": slot["power_w"], "kind": "laden" if slot["power_w"] < 0 else "ontladen"}


today_value, today_attrs = _plan(0)
tomorrow_value, tomorrow_attrs = _plan(1)
calc_today, calc_today_attrs = _calc("today")
calc_tomorrow, calc_tomorrow_attrs = _calc("tomorrow")
schedule_today, schedule_today_attrs = _schedule(0)
schedule_tomorrow, schedule_tomorrow_attrs = _schedule(1)

BATTERY_SENSORS: tuple[BatterySensor, ...] = (
    BatterySensor(key="soc", device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE,
                  state_class=SensorStateClass.MEASUREMENT, value=_status("soc")),
    BatterySensor(key="ac_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  state_class=SensorStateClass.MEASUREMENT, value=_status("ac_power_w")),
    BatterySensor(key="battery_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  state_class=SensorStateClass.MEASUREMENT, value=_status("battery_power_w"), entity_registry_enabled_default=False),
    BatterySensor(key="mode", value=_status("mode"), entity_category=EntityCategory.DIAGNOSTIC),
    BatterySensor(key="connection", value=_status("via"), entity_category=EntityCategory.DIAGNOSTIC),
    BatterySensor(key="expected_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  state_class=SensorStateClass.MEASUREMENT, value=lambda b, d: (b.get("power_check") or {}).get("expected_w")),
    BatterySensor(key="realtime_setpoint", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  state_class=SensorStateClass.MEASUREMENT,
                  value=lambda b, d: (b.get("realtime") or {}).get("setpoint_w") if (b.get("realtime") or {}).get("active") else 0),
    BatterySensor(key="rte", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
                  suggested_display_precision=1, value=lambda b, d: b.get("measured_rte_pct")),
    BatterySensor(key="next_block", device_class=SensorDeviceClass.TIMESTAMP, value=_next_value, attrs=_next_attrs),
    BatterySensor(key="plan_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=today_value, attrs=today_attrs),
    BatterySensor(key="plan_tomorrow", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=tomorrow_value, attrs=tomorrow_attrs),
    BatterySensor(key="plan_today_net", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=calc_today, attrs=calc_today_attrs, details=True),
    BatterySensor(key="plan_tomorrow_net", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=calc_tomorrow, attrs=calc_tomorrow_attrs, details=True),
    BatterySensor(key="charged_today", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                  state_class=SensorStateClass.TOTAL, value=_metric("charged_kwh"), details=True),
    BatterySensor(key="discharged_today", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                  state_class=SensorStateClass.TOTAL, value=_metric("discharged_kwh"), details=True),
    BatterySensor(key="schedule_today", value=schedule_today, attrs=schedule_today_attrs),
    BatterySensor(key="schedule_tomorrow", value=schedule_tomorrow, attrs=schedule_tomorrow_attrs),
    BatterySensor(key="charge_cost_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("today", "cost_eur"),
                  attrs=_money_attrs("today"), details=True),
    BatterySensor(key="discharge_value_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("today", "value_eur"),
                  attrs=_money_attrs("today"), details=True),
    BatterySensor(key="net_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("today", "margin_eur"),
                  attrs=_money_attrs("today"), details=True),
    BatterySensor(key="charge_cost_month", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("month", "cost_eur"),
                  attrs=_money_attrs("month"), details=True, reset="month"),
    BatterySensor(key="discharge_value_month", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("month", "value_eur"),
                  attrs=_money_attrs("month"), details=True, reset="month"),
    BatterySensor(key="net_month", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  state_class=SensorStateClass.TOTAL, suggested_display_precision=2, value=_money("month", "margin_eur"),
                  attrs=_money_attrs("month"), details=True, reset="month"),
)


class BatterySensorEntity(SbcBatteryEntity, SensorEntity):
    entity_description: BatterySensor
    _unrecorded_attributes = frozenset({"slots", "blocks", "hours_charging", "hours_discharging"})

    def __init__(self, entry: SbcConfigEntry, battery: dict, description: BatterySensor) -> None:
        super().__init__(entry, battery, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.details:  # ook bijwerken als de doorrekening binnenkomt
            self.async_on_remove(self.entry.runtime_data.details.async_add_listener(self._handle_coordinator_update))

    def _details(self) -> dict:
        details = self.entry.runtime_data.details.data
        if details is None:
            return {}
        return {"calculations": details.calculations.get(self.battery_id, {}), "metrics": details.metrics,
                "money": details.money.get(self.battery_id, {})}

    @property
    def native_value(self):
        return self.entity_description.value(self.battery, self._details())

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = self.entity_description.attrs
        return attrs(self.battery, self._details()) if attrs else None

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.state_class != SensorStateClass.TOTAL:
            return None
        midnight = dt_util.start_of_local_day()
        return midnight.replace(day=1) if self.entity_description.reset == "month" else midnight


# ── algemeen ─────────────────────────────────────────────────────────────────

def _quarters(state: dict, offset: int = 0) -> list[dict]:
    day = (dt_util.now().date() + timedelta(days=offset)).isoformat()
    return (state.get("quarter_prices") or {}).get(day, [])


def _current(state: dict) -> dict | None:
    now = dt_util.now()
    for q in _quarters(state):
        start = datetime.fromisoformat(q["start"])
        if start <= now < start + timedelta(minutes=15):
            return q
    return None


@dataclass(frozen=True, kw_only=True)
class HubSensor(SensorEntityDescription):
    value: Callable[[dict], Any]
    attrs: Callable[[dict], dict] | None = None


def _extreme(fn):
    def value(state):
        quarters = _quarters(state)
        return fn(quarters, key=lambda q: q["buy"])["buy"] if quarters else None

    def attrs(state):
        quarters = _quarters(state)
        return {"start": fn(quarters, key=lambda q: q["buy"])["start"]} if quarters else {}
    return value, attrs


lowest, lowest_attrs = _extreme(min)
highest, highest_attrs = _extreme(max)

HUB_SENSORS: tuple[HubSensor, ...] = (
    HubSensor(key="grid_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
              state_class=SensorStateClass.MEASUREMENT, value=lambda s: (s.get("meter") or {}).get("power_w"),
              attrs=lambda s: {"source": (s.get("meter") or {}).get("source")}),
    HubSensor(key="solar_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
              state_class=SensorStateClass.MEASUREMENT,
              value=lambda s: (s.get("solar") or {}).get("power_w") if s.get("solar_sources") else None),
    HubSensor(key="price_now", native_unit_of_measurement=EUR_KWH, state_class=SensorStateClass.MEASUREMENT,
              suggested_display_precision=4, value=lambda s: (_current(s) or {}).get("buy"),
              attrs=lambda s: {"spot": (_current(s) or {}).get("spot"),
                               "today": [{"start": q["start"], "price": q["buy"]} for q in _quarters(s)],
                               "tomorrow": [{"start": q["start"], "price": q["buy"]} for q in _quarters(s, 1)]}),
    HubSensor(key="feed_in_now", native_unit_of_measurement=EUR_KWH, state_class=SensorStateClass.MEASUREMENT,
              suggested_display_precision=4, value=lambda s: (_current(s) or {}).get("feed_in")),
    HubSensor(key="price_lowest_today", native_unit_of_measurement=EUR_KWH, suggested_display_precision=4,
              value=lowest, attrs=lowest_attrs),
    HubSensor(key="price_highest_today", native_unit_of_measurement=EUR_KWH, suggested_display_precision=4,
              value=highest, attrs=highest_attrs),
    HubSensor(key="price_source", entity_category=EntityCategory.DIAGNOSTIC,
              value=lambda s: (s.get("price_source") or {}).get(dt_util.now().date().isoformat())),
    HubSensor(key="strategy", entity_category=EntityCategory.DIAGNOSTIC, value=lambda s: s.get("strategy")),
)


class HubSensorEntity(SbcHubEntity, SensorEntity):
    entity_description: HubSensor
    _unrecorded_attributes = frozenset({"today", "tomorrow"})

    def __init__(self, entry: SbcConfigEntry, description: HubSensor) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = self.entity_description.attrs
        return attrs(self.coordinator.data or {}) if attrs else None


# ── alle sensoren uit de batterij ────────────────────────────────────────────
#
# Welke er zijn, bepaalt SmartBatteryControl (per merk en per verbinding: via Modbus veel meer dan via de Local
# API). We maken ze zoals ze binnenkomen, met een eenheid en een soort waar die bekend is. De namen komen van
# SmartBatteryControl (Nederlands), met de groep ervoor: "Batterij Spanning", "Net (AC) Spanning".

UNITS = {
    "%": (None, PERCENTAGE), "V": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    "A": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE), "W": (SensorDeviceClass.POWER, UnitOfPower.WATT),
    "°C": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS), "Hz": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    "kWh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "dBm": (SensorDeviceClass.SIGNAL_STRENGTH, SIGNAL_STRENGTH_DECIBELS_MILLIWATT),
}
DIAGNOSTIC_GROUPS = {"Apparaat", "Status", "Instellingen", "Wifi"}


def detail_key(sensor: dict) -> str:
    """Vast per sensor: het register (Modbus) of de groep en de naam (Local API)."""
    if sensor.get("register") is not None:
        return f"r{sensor['register']}"
    return re.sub(r"[^a-z0-9]+", "_", f"{sensor['group']}_{sensor['label']}".lower()).strip("_")


class BatteryDetailSensor(CoordinatorEntity[SensorCoordinator], SensorEntity):
    """Eén sensor uit de batterij, zoals SmartBatteryControl hem uitleest."""

    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, battery: dict, sensor: dict) -> None:
        super().__init__(entry.runtime_data.sensors)
        self.battery_id = battery["id"]
        self.key = detail_key(sensor)
        self._attr_unique_id = f"{battery['id']}_detail_{self.key}"
        self._attr_name = f"{sensor['group']} {sensor['label']}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, battery["id"])})
        unit = sensor.get("unit") or ""
        numeric = isinstance(sensor.get("value"), (int, float)) and not isinstance(sensor.get("value"), bool)
        self._numeric = numeric  # een meetwaarde blijft een getal, ook als hij even als tekst binnenkomt
        if numeric and unit in UNITS:
            device_class, native = UNITS[unit]
            if unit == "%" and sensor["group"] == "Batterij":
                device_class = SensorDeviceClass.BATTERY
            if unit == "kWh" and "Opgeslagen" in sensor["label"]:
                device_class = SensorDeviceClass.ENERGY_STORAGE
            self._attr_device_class = device_class
            self._attr_native_unit_of_measurement = native
            if unit == "kWh" and device_class == SensorDeviceClass.ENERGY:
                # Totalen tellen op; "vandaag" en "deze maand" ook, en gaan dan terug naar nul (dat herkent HA zelf)
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            else:
                self._attr_state_class = SensorStateClass.MEASUREMENT
        elif numeric and not unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT  # bijv. laadcycli, versies
        if sensor["group"] in DIAGNOSTIC_GROUPS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _sensor(self) -> dict | None:
        for s in (self.coordinator.data or {}).get(self.battery_id, []):
            if detail_key(s) == self.key:
                return s
        return None

    @property
    def available(self) -> bool:
        return super().available and self._sensor() is not None

    @property
    def native_value(self):
        s = self._sensor()
        if s is None:
            return None
        v = s.get("value")
        if isinstance(v, bool):
            return "ja" if v else "nee"
        if isinstance(v, (int, float)) or self._numeric:
            return v
        return str(v)[:255]


async def async_setup_entry(hass: HomeAssistant, entry: SbcConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    state = entry.runtime_data.state
    async_add_entities(HubSensorEntity(entry, d) for d in HUB_SENSORS)
    known: set[str] = set()

    @callback
    def add_batteries() -> None:
        new = [b for b in (state.data or {}).get("batteries", []) if b["id"] not in known]
        known.update(b["id"] for b in new)
        async_add_entities(BatterySensorEntity(entry, b, d) for b in new for d in BATTERY_SENSORS)

    add_batteries()
    entry.async_on_unload(state.async_add_listener(add_batteries))  # een nieuwe batterij krijgt vanzelf sensoren

    sensors = entry.runtime_data.sensors
    known_details: set[str] = set()

    @callback
    def add_details() -> None:
        """Sensoren uit de batterij zodra ze er zijn (een batterij die eerst onbereikbaar was, krijgt ze later)."""
        batteries = {b["id"]: b for b in (state.data or {}).get("batteries", [])}
        new = []
        for battery_id, items in (sensors.data or {}).items():
            if battery_id not in batteries:
                continue
            for item in items:
                uid = f"{battery_id}_detail_{detail_key(item)}"
                if uid not in known_details:
                    known_details.add(uid)
                    new.append(BatteryDetailSensor(entry, batteries[battery_id], item))
        if new:
            async_add_entities(new)

    add_details()
    entry.async_on_unload(sensors.async_add_listener(add_details))
