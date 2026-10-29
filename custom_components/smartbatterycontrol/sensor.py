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
from .coordinator import SbcConfigEntry, SensorCoordinator, StateCoordinator
from .entity import SbcBatteryEntity, SbcHubEntity, next_block, schedule_text, slot_text, soc_for

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
                "slots": [slot_text(s, soc_for(b, day, s)) for s in plan.get("slots", []) if s.get("enabled")],
                # slim: één of twee cycli doorgerekend; controleschema: wat er gecorrigeerd is
                "considered": plan.get("considered"), "corrections": plan.get("corrections") or []}
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
        blocks = [{"start": x["start"], "end": x["end"], "kind": "laden" if x["power_w"] < 0 else "ontladen", "power_w": abs(x["power_w"]),
                   **({"soc_start_pct": soc["soc_start_pct"], "soc_end_pct": soc["soc_end_pct"]} if (soc := soc_for(b, day, x)) else {})}
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
    soc = soc_for(b, start.date().isoformat(), slot)
    return {"end": slot["end"], "power_w": slot["power_w"], "kind": "laden" if slot["power_w"] < 0 else "ontladen",
            **({"soc_start_pct": soc["soc_start_pct"], "soc_end_pct": soc["soc_end_pct"]} if soc else {})}


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
    # Vermogens en capaciteit uit de instellingen van de batterij in SmartBatteryControl
    BatterySensor(key="capacity", device_class=SensorDeviceClass.ENERGY_STORAGE, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                  entity_category=EntityCategory.DIAGNOSTIC, value=lambda b, d: b.get("capacity_kwh")),
    BatterySensor(key="max_charge_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  entity_category=EntityCategory.DIAGNOSTIC, value=lambda b, d: b.get("max_charge_w")),
    BatterySensor(key="max_discharge_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  entity_category=EntityCategory.DIAGNOSTIC, value=lambda b, d: b.get("max_discharge_w")),
    # Laadgrenzen uit de instellingen van de batterij in SmartBatteryControl
    BatterySensor(key="min_soc", native_unit_of_measurement=PERCENTAGE, value=lambda b, d: b.get("min_soc_pct"),
                  icon="mdi:battery-arrow-down-outline"),
    BatterySensor(key="max_soc", native_unit_of_measurement=PERCENTAGE, value=lambda b, d: b.get("max_soc_pct"),
                  icon="mdi:battery-arrow-up-outline"),
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
    # Planning en pushen volgens schema (leeg als dat automatisch gaat)
    HubSensor(key="next_planning", device_class=SensorDeviceClass.TIMESTAMP,
              value=lambda s: _timestamp(((s.get("schedule") or {}).get("next_planning") or {}).get("at")),
              attrs=lambda s: {"day": ((s.get("schedule") or {}).get("next_planning") or {}).get("day"),
                               "schedule": (s.get("schedule") or {}).get("plan_schedule"),
                               "times": _week((s.get("schedule") or {}).get("plan_times"))}),
    HubSensor(key="next_control", device_class=SensorDeviceClass.TIMESTAMP,
              value=lambda s: _timestamp((s.get("schedule") or {}).get("next_control")),
              attrs=lambda s: {"schedule": (s.get("schedule") or {}).get("control_schedule"),
                               "times": _week((s.get("schedule") or {}).get("control_times"))}),
    HubSensor(key="next_push", device_class=SensorDeviceClass.TIMESTAMP,
              value=lambda s: _timestamp((s.get("schedule") or {}).get("next_push")),
              attrs=lambda s: {"schedule": (s.get("schedule") or {}).get("push_schedule"),
                               "writes_today": (s.get("schedule") or {}).get("writes_today"),
                               "times": _week((s.get("schedule") or {}).get("push_times"))}),
    HubSensor(key="check_interval", device_class=SensorDeviceClass.DURATION, native_unit_of_measurement="s",
              entity_category=EntityCategory.DIAGNOSTIC, value=lambda s: (s.get("schedule") or {}).get("check_interval_s")),
)

WEEKDAYS = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")


def _week(times: list | None) -> dict | None:
    """De tijden per weekdag als {"maandag": "15:00", ...}; leeg = die dag niet."""
    return dict(zip(WEEKDAYS, times)) if times else None


def _timestamp(value):
    return datetime.fromisoformat(value) if value else None


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


# ── stekkers (Tapo en HomeWizard): vermogen, verbruik en kosten ──────────────

@dataclass(frozen=True, kw_only=True)
class PlugSensor(SensorEntityDescription):
    value: Callable[[dict, dict], Any]  # (stekker uit /api/state, verbruik deze maand) → waarde
    reset: str | None = None  # bij state_class TOTAL: "day" of "month"


PLUG_SENSORS: tuple[PlugSensor, ...] = (
    PlugSensor(key="plug_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
               state_class=SensorStateClass.MEASUREMENT, value=lambda p, m: p.get("power_w")),
    PlugSensor(key="plug_energy_today", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
               state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=2, value=lambda p, m: p.get("today_kwh")),
    PlugSensor(key="plug_cost_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
               state_class=SensorStateClass.TOTAL, suggested_display_precision=2, reset="day",
               value=lambda p, m: p.get("today_cost_eur")),
    PlugSensor(key="plug_energy_month", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
               state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=2, value=lambda p, m: m.get("kwh")),
    PlugSensor(key="plug_cost_month", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
               state_class=SensorStateClass.TOTAL, suggested_display_precision=2, reset="month",
               value=lambda p, m: m.get("cost_eur")),
    PlugSensor(key="plug_price_month", native_unit_of_measurement=EUR_KWH, state_class=SensorStateClass.MEASUREMENT,
               suggested_display_precision=4, value=lambda p, m: m.get("avg_eur_per_kwh")),
)


class PlugSensorEntity(CoordinatorEntity[StateCoordinator], SensorEntity):
    """Eén waarde van een stekker; elke stekker is een eigen apparaat onder SmartBatteryControl."""

    _attr_has_entity_name = True
    entity_description: PlugSensor

    def __init__(self, entry: SbcConfigEntry, plug: dict, description: PlugSensor) -> None:
        super().__init__(entry.runtime_data.state)
        self.entry = entry
        self.plug_id = plug["id"]
        self.entity_description = description
        self._attr_unique_id = f"plug_{plug['id']}_{description.key}"
        self._attr_translation_key = description.key
        homewizard = plug.get("kind") == "homewizard"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"plug_{plug['id']}")}, name=plug.get("name") or plug["id"],
                                            manufacturer="HomeWizard" if homewizard else "TP-Link",
                                            model=plug.get("model") or ("HomeWizard" if homewizard else "Tapo"),
                                            via_device=(DOMAIN, entry.entry_id))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.entry.runtime_data.details.async_add_listener(self._handle_coordinator_update))

    def _plug(self) -> dict:
        return next((p for p in (self.coordinator.data or {}).get("plugs", []) if p["id"] == self.plug_id), {})

    def _month(self) -> dict:
        details = self.entry.runtime_data.details.data
        return (details.plugs.get(self.plug_id) if details else None) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._plug())

    @property
    def native_value(self):
        return self.entity_description.value(self._plug(), self._month())

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "plug_cost_month":
            month = self._month()
            return {"days": month.get("days"), "kwh": month.get("kwh"), "avg_eur_per_kwh": month.get("avg_eur_per_kwh")}
        if self.entity_description.key == "plug_power":
            plug = self._plug()
            return {"on": plug.get("on"), "error": plug.get("error"), "host": plug.get("host")}
        return None

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.state_class != SensorStateClass.TOTAL:
            return None
        midnight = dt_util.start_of_local_day()
        return midnight.replace(day=1) if self.entity_description.reset == "month" else midnight


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

    known_plugs: set[str] = set()

    @callback
    def add_plugs() -> None:
        new = [p for p in (state.data or {}).get("plugs", []) if p["id"] not in known_plugs]
        known_plugs.update(p["id"] for p in new)
        async_add_entities(PlugSensorEntity(entry, p, d) for p in new for d in PLUG_SENSORS)

    add_plugs()
    entry.async_on_unload(state.async_add_listener(add_plugs))  # een nieuwe stekker krijgt vanzelf sensoren

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
