"""Sensoren per batterij (laadniveau, vermogen, planning, doorrekening, kosten en opbrengst, rendement, de cijfers per
periode, het laatste slot, en alle sensoren die SmartBatteryControl uit de batterij leest), algemeen (net en fasen, zon,
prijzen, de cijfers per periode, alle batterijen samen, de zonverwachting, het verwachte verbruik, de controles en
updates), en per stekker en per omvormer een apparaat."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import (PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfApparentPower,
                                 UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfFrequency, UnitOfPower,
                                 UnitOfReactivePower, UnitOfTemperature)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Details, SbcConfigEntry, SensorCoordinator, StateCoordinator, per_kwh
from .entity import SbcBatteryEntity, SbcHubDetailEntity, SbcHubEntity, next_block, schedule_text, slot_text, soc_for

EUR, EUR_KWH = "EUR", "EUR/kWh"


@dataclass(frozen=True, kw_only=True)
class BatterySensor(SensorEntityDescription):
    value: Callable[[dict, dict], Any]  # (batterij, details) → waarde
    attrs: Callable[[dict, dict], dict] | None = None
    details: bool = False  # waarde komt uit wat elke 5 minuten binnenkomt (doorrekening, cijfers, dagtotalen …)
    # bij state_class TOTAL: vanaf middernacht ("day"), de eerste van de maand ("month") of 1 januari ("year")
    reset: str | None = "day"


def last_reset(reset: str | None) -> datetime:
    midnight = dt_util.start_of_local_day()
    return {"month": midnight.replace(day=1), "year": midnight.replace(month=1, day=1)}.get(reset or "day", midnight)


# ── cijfers per periode ──────────────────────────────────────────────────────
#
# Vandaag, 7 dagen, 30 dagen en dit jaar, zoals op de schermen Opbrengst, Historie en Verbruik van SmartBatteryControl.
# Vandaag en dit jaar staan aan; 7 en 30 dagen staan klaar maar uit (aan te zetten bij de entiteiten). Energie, euro's
# en cycli van vandaag en dit jaar tellen op vanaf middernacht en 1 januari (met statistieken, en bruikbaar in het
# Energie-dashboard); 7 en 30 dagen schuiven elke dag op en hebben daarom geen statistieken.

PERIODS = ("today", "week", "month", "year")
ENABLED_PERIODS = ("today", "year")
RESET = {"today": "day", "year": "year"}
KINDS: dict[str, dict] = {
    "energy": {"device_class": SensorDeviceClass.ENERGY, "native_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
               "suggested_display_precision": 2},
    "money": {"device_class": SensorDeviceClass.MONETARY, "native_unit_of_measurement": EUR, "suggested_display_precision": 2},
    "count": {"suggested_display_precision": 2},
    "pct": {"native_unit_of_measurement": PERCENTAGE, "suggested_display_precision": 1, "state_class": SensorStateClass.MEASUREMENT},
    "price": {"native_unit_of_measurement": EUR_KWH, "suggested_display_precision": 3, "state_class": SensorStateClass.MEASUREMENT},
}


@dataclass(frozen=True)
class Metric:
    key: str
    value: Callable[[dict], Any]  # (cijfers van de periode) → waarde
    kind: str  # een van KINDS
    solar: bool = False  # alleen met zonnepanelen
    attrs: Callable[[dict], dict] | None = None


def _field(name: str):
    return lambda p: p.get(name)


def _electricity(name: str):
    return lambda p: (p.get("electricity") or {}).get(name)


def _spread(p: dict) -> float | None:
    low, high = p.get("cheap_price_eur"), p.get("discharge_price_eur")
    return round(high - low, 4) if low is not None and high is not None else None


def period_kwargs(metric: Metric, period: str) -> dict:
    kwargs = dict(KINDS[metric.kind])
    if "state_class" not in kwargs and period in RESET:  # optellend vanaf middernacht of 1 januari
        kwargs["state_class"] = SensorStateClass.TOTAL
    return {**kwargs, "reset": RESET.get(period), "entity_registry_enabled_default": period in ENABLED_PERIODS}


BATTERY_METRICS = (
    Metric("charged", _field("charged_kwh"), "energy"),
    Metric("discharged", _field("discharged_kwh"), "energy"),
    Metric("earnings", _field("savings_eur"), "money"),
    Metric("efficiency", _field("efficiency_pct"), "pct"),
    Metric("cycles", _field("cycles"), "count"),
    Metric("charge_price", _field("charge_price_eur"), "price"),
    Metric("discharge_price", _field("discharge_price_eur"), "price"),
)


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


def _battery_period(metric: Metric, period: str):
    def value(b, d):
        p = (((d.get("periods") or {}).get(period) or {}).get("batteries") or {}).get(b["id"])
        return metric.value(p) if p else None
    return value


TOTAL_FIELDS = ("charge_kwh", "charge_eur", "charge_avg_eur_kwh", "discharge_kwh", "discharge_eur", "discharge_avg_eur_kwh")


def _end_result(day: str):
    """Een batterij op één dag: gemeten tot nu, daarna volgens het plan (het totaal onderaan het scherm Plan)."""
    def value(b, d):
        return ((d.get("totals") or {}).get(day) or {}).get("total_eur")

    def attrs(b, d):
        t = (d.get("totals") or {}).get(day) or {}
        return {**{k: t.get(k) for k in TOTAL_FIELDS}, "measured": t.get("measured"), "planned": t.get("planned"),
                "complete": t.get("complete")}
    return value, attrs


SLOT_STATUS = {"ok": "uitgevoerd", "full": "vol: eerder klaar", "empty": "leeg: eerder klaar", "deviation": "afwijking"}


def soc_range(start: float | None, end: float | None) -> str | None:
    return f"{round(start)}% → {round(end)}%" if start is not None and end is not None else None


def _last_slot(b, d):
    slot = d.get("last_slot")
    return per_kwh(slot["kind"], slot.get("actual_eur"), slot.get("actual_kwh")) if slot else None


def _last_slot_attrs(b, d):
    slot = d.get("last_slot")
    if not slot:
        return {}
    return {"day": slot["day"], "start": slot["start"], "end": slot["end"], "kind": "laden" if slot["kind"] == "charge" else "ontladen",
            "power_w": abs(slot.get("power_w") or 0), "kwh": slot.get("actual_kwh"), "eur": slot.get("actual_eur"),
            "planned_kwh": slot.get("planned_kwh"), "planned_eur": slot.get("planned_eur"),
            "planned_per_kwh": per_kwh(slot["kind"], slot.get("planned_eur"), slot.get("planned_kwh")),
            "goal": soc_range(slot.get("target_start_pct"), slot.get("target_pct")),
            "result": soc_range(slot.get("soc_start_pct"), slot.get("soc_end_pct")),
            "status": SLOT_STATUS.get(slot.get("status"), slot.get("status")), "made_by": slot.get("by")}


def _yesterday(d: dict) -> dict | None:
    """De dagcontrole van gisteren: plan tegen werkelijkheid."""
    day = (dt_util.now().date() - timedelta(days=1)).isoformat()
    return next((r for r in d.get("reviews") or [] if r.get("day") == day), None)


def _yesterday_attrs(b, d):
    r = _yesterday(d)
    return {k: r.get(k) for k in ("day", "planned_eur", "followed", "rte_pct", "charge_kwh", "discharge_kwh")} if r else {}


PHASE_SOURCE = {"auto": "automatisch herkend", "handmatig": "zelf ingesteld"}


def _phase_attrs(b, d):
    info = b.get("phase_info") or {}
    counts = info.get("counts")
    return {"source": PHASE_SOURCE.get(info.get("source"), info.get("source")), "since": info.get("since"),
            "detected": info.get("detected"), "votes": info.get("votes"),
            "counts": dict(zip(("L1", "L2", "L3"), counts)) if counts else None}


def _adjustment(b, d):
    """Zelflerend laden: met hoeveel procent van het laadvermogen de planner rekent (100% = geen bijstelling)."""
    learning = b.get("learning")
    if not learning or not learning.get("enabled"):
        return None
    return round((learning.get("factor") or 1.0) * 100, 1)


def _adjustment_attrs(b, d):
    learning = b.get("learning") or {}
    return {"enabled": learning.get("enabled"),
            "samples": [f"{x.get('day')} {x.get('start')}–{x.get('end')}: doel {soc_range(x.get('target_from'), x.get('target')) or '?'}, "
                        f"bereikt {soc_range(x.get('from'), x.get('result')) or '?'}" for x in learning.get("samples") or []]}


def _epoch(value) -> datetime | None:
    return datetime.fromtimestamp(value, tz=dt_util.UTC) if value else None


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
end_today, end_today_attrs = _end_result("today")
end_tomorrow, end_tomorrow_attrs = _end_result("tomorrow")

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
    # De dag van alle laad- en ontlaadmomenten: gemeten tot nu, daarna volgens het plan, met de gemiddelde prijs per kWh
    BatterySensor(key="end_result_today", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=end_today, attrs=end_today_attrs, details=True),
    BatterySensor(key="end_result_tomorrow", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=end_tomorrow, attrs=end_tomorrow_attrs, details=True),
    # Het laatste laad- of ontlaadslot dat klaar is: wat een kWh gemiddeld kostte of opleverde, met het doel en het resultaat
    BatterySensor(key="last_slot", native_unit_of_measurement=EUR_KWH, state_class=SensorStateClass.MEASUREMENT,
                  suggested_display_precision=3, value=_last_slot, attrs=_last_slot_attrs, details=True, icon="mdi:cash-clock"),
    BatterySensor(key="result_yesterday", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                  suggested_display_precision=2, value=lambda b, d: (_yesterday(d) or {}).get("actual_eur"),
                  attrs=_yesterday_attrs, details=True),
    # Fase, zelflerend laden, de laatste push en afdwingen
    BatterySensor(key="phase", value=lambda b, d: b.get("phase") or None, attrs=_phase_attrs, icon="mdi:sine-wave",
                  entity_category=EntityCategory.DIAGNOSTIC),
    BatterySensor(key="charge_adjustment", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
                  value=_adjustment, attrs=_adjustment_attrs, icon="mdi:school-outline", entity_category=EntityCategory.DIAGNOSTIC),
    BatterySensor(key="last_push", device_class=SensorDeviceClass.TIMESTAMP, value=lambda b, d: _epoch(b.get("pushed_at")),
                  entity_category=EntityCategory.DIAGNOSTIC),
    BatterySensor(key="enforce_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                  state_class=SensorStateClass.MEASUREMENT, value=lambda b, d: b.get("enforce_w"),
                  entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    # De cijfers per periode (vandaag, 7 dagen, 30 dagen, dit jaar)
    *(BatterySensor(key=f"{m.key}_{period}", value=_battery_period(m, period), details=True, **period_kwargs(m, period))
      for m in BATTERY_METRICS for period in PERIODS),
)


class BatterySensorEntity(SbcBatteryEntity, SensorEntity):
    entity_description: BatterySensor
    _unrecorded_attributes = frozenset({"slots", "blocks", "hours_charging", "hours_discharging", "samples"})

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
        bid = self.battery_id
        stats = next((b for b in details.stats.get("batteries") or [] if b.get("id") == bid), {})
        return {"calculations": details.calculations.get(bid, {}), "periods": details.periods,
                "money": details.money.get(bid, {}), "last_slot": details.last_slots.get(bid), "reviews": stats.get("reviews"),
                "totals": {k: next((b for b in t.get("batteries", []) if b["id"] == bid), None) for k, t in details.totals.items()}}

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
        return last_reset(self.entity_description.reset)


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
    exists: Callable[[dict], bool] | None = None  # alleen als dit klopt (bijv. een meter per fase)


def _phase(index: int):
    return lambda s: ((s.get("meter") or {}).get("phases_w") or [None] * 3)[index]


def _has_phases(state: dict) -> bool:
    return len((state.get("meter") or {}).get("phases_w") or []) == 3


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
    # Het netvermogen per fase van de P1-meter (+ = afname, − = teruglevering)
    *(HubSensor(key=f"grid_power_l{i + 1}", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                state_class=SensorStateClass.MEASUREMENT, value=_phase(i), exists=_has_phases) for i in range(3)),
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


# ── algemeen, elke 5 minuten: cijfers per periode, alle batterijen samen, zon, verbruik, controles, updates ──

@dataclass(frozen=True, kw_only=True)
class DetailSensor(SensorEntityDescription):
    value: Callable[[Details], Any]
    attrs: Callable[[Details], dict] | None = None
    exists: Callable[[Details], bool] | None = None  # alleen als dit klopt (bijv. zonnepanelen, een zonmodel)
    reset: str | None = None  # bij state_class TOTAL: "day" (middernacht) of "year" (1 januari)


def _has_solar(d: Details) -> bool:
    return bool((d.periods.get("today") or {}).get("has_solar"))


def _hub_period(metric: Metric, period: str) -> DetailSensor:
    def value(d: Details):
        p = d.periods.get(period)
        return metric.value(p) if p else None

    def attrs(d: Details):
        p = d.periods.get(period) or {}
        return {**({"since": p["since"]} if p.get("since") else {}), **(metric.attrs(p) if metric.attrs and p else {})}

    def exists(d: Details) -> bool:
        return period in d.periods and (not metric.solar or _has_solar(d))
    return DetailSensor(key=f"{metric.key}_{period}", value=value, attrs=attrs, exists=exists, **period_kwargs(metric, period))


HUB_METRICS = (
    Metric("house", _field("house_kwh"), "energy"),
    Metric("grid_import", _field("import_kwh"), "energy"),
    Metric("grid_export", _field("export_kwh"), "energy"),
    Metric("grid_to_house", _field("grid_to_house_kwh"), "energy"),
    Metric("batteries_charged", _field("charged_kwh"), "energy"),
    Metric("batteries_discharged", _field("discharged_kwh"), "energy"),
    Metric("grid_to_batteries", _field("grid_to_battery_kwh"), "energy"),
    Metric("batteries_to_grid", _field("battery_exported_kwh"), "energy"),
    Metric("solar_to_batteries", _field("solar_to_battery_kwh"), "energy", solar=True),
    Metric("solar_production", _field("solar_kwh"), "energy", solar=True),
    Metric("solar_direct", _field("solar_direct_kwh"), "energy", solar=True),
    Metric("solar_exported", _field("solar_exported_kwh"), "energy", solar=True),
    Metric("solar_kept", _field("solar_kept_kwh"), "energy", solar=True),
    Metric("battery_earnings", _field("battery_savings_eur"), "money"),
    Metric("grid_cost", _field("grid_cost_eur"), "money"),
    # De stroomrekening: afname en teruglevering met de vaste kosten per dag, alle regels als attribuut
    Metric("electricity_bill", _electricity("total_eur"), "money", attrs=lambda p: dict(p.get("electricity") or {})),
    Metric("solar_value", _field("solar_value_eur"), "money", solar=True),
    Metric("self_sufficiency", _field("self_sufficiency_pct"), "pct"),
    Metric("battery_share", _field("battery_share_pct"), "pct"),
    Metric("batteries_efficiency", _field("efficiency_pct"), "pct"),
    Metric("solar_share", _field("solar_share_pct"), "pct", solar=True),
    Metric("solar_self_use", _field("solar_self_use_pct"), "pct", solar=True),
    Metric("grid_charge_price", _field("cheap_price_eur"), "price"),
    Metric("batteries_discharge_price", _field("discharge_price_eur"), "price"),
    Metric("price_spread", _spread, "price"),
    Metric("import_price", _electricity("import_price"), "price"),
    Metric("export_price", _electricity("export_price"), "price"),
)


def _totals(day: str, key: str):
    return lambda d: (d.totals.get(day) or {}).get(key)


def _totals_attrs(day: str):
    def attrs(d: Details):
        t = d.totals.get(day) or {}
        return {**{k: t.get(k) for k in TOTAL_FIELDS}, "measured_until": t.get("measured_until"), "planned_from": t.get("planned_from"),
                "complete": t.get("complete"), "batteries": {b["name"]: b.get("total_eur") for b in t.get("batteries", [])}}
    return attrs


def _has_totals(d: Details) -> bool:
    return "today" in d.totals


def _pv(key: str):
    return lambda d: (d.stats.get("pv") or {}).get(key)


def _has_pv(d: Details) -> bool:
    return bool(d.stats.get("pv"))


def _forecaster_label(d: Details, source: str | None) -> str | None:
    return next((f["label"] for f in d.pv.get("forecasters") or [] if f["id"] == source), source)


def _forecast_attrs(when: str):
    def attrs(d: Details):
        pv = d.stats.get("pv") or {}
        return {"typical_kwh": pv.get(f"typical_{when}_kwh"), "calibration": pv.get("calibration"),
                "forecaster": _forecaster_label(d, d.pv.get("used")), "quarters": pv.get(f"forecast_{when}_w")}
    return attrs


def _remaining(d: Details) -> float | None:
    """Wat de zon vandaag nog oplevert volgens de verwachting: de rest van dit kwartier en de kwartieren erna."""
    quarters = (d.stats.get("pv") or {}).get("forecast_today_w")
    if not quarters:
        return None
    now = dt_util.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q, part = divmod((now.timestamp() - midnight.timestamp()) / 900, 1)  # echte kwartieren, ook als de klok verzet
    q = int(q)
    rest = (quarters[q] * (1 - part) if q < len(quarters) else 0) + sum(quarters[q + 1:])
    return round(rest / 4000, 2)


def _house(offset: int):
    """Het verwachte verbruik van het huis op een dag, uit het geleerde verbruikspatroon (per kwartier)."""
    def quarters(d: Details):
        day = (dt_util.now().date() + timedelta(days=offset)).isoformat()
        return ((d.stats.get("profile") or {}).get("forecast_w") or {}).get(day)

    def value(d: Details):
        q = quarters(d)
        return round(sum(q) / 4000, 2) if q else None

    def attrs(d: Details):
        return {"days_learned": (d.stats.get("profile") or {}).get("days_learned"), "quarters": quarters(d)}
    return value, attrs


def _problems(d: Details) -> list[dict]:
    return [c for c in (d.stats.get("health") or {}).get("checks") or [] if c.get("status") in ("warning", "error")]


def _health_attrs(d: Details) -> dict:
    health = d.stats.get("health") or {}
    checks = health.get("checks") or []
    return {"checked_at": _epoch(health.get("checked_at")), "checks": len(checks),
            "errors": sum(c.get("status") == "error" for c in checks), "warnings": sum(c.get("status") == "warning" for c in checks),
            "problems": [f"{c['category']} · {c['name']}: {c['message']}" for c in _problems(d)]}


house_today, house_today_attrs = _house(0)
house_tomorrow, house_tomorrow_attrs = _house(1)

DETAIL_SENSORS: tuple[DetailSensor, ...] = (
    *(_hub_period(m, period) for m in HUB_METRICS for period in PERIODS),
    # Alle batterijen samen (het totaal onderaan het scherm Plan): gemeten tot nu, daarna volgens het plan
    *(DetailSensor(key=f"all_batteries_{day}", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement=EUR,
                   suggested_display_precision=2, value=_totals(day, "total_eur"), attrs=_totals_attrs(day), exists=_has_totals)
      for day in ("today", "tomorrow")),
    *(DetailSensor(key=f"all_batteries_{kind}_price_{day}", native_unit_of_measurement=EUR_KWH, state_class=SensorStateClass.MEASUREMENT,
                   suggested_display_precision=3, value=_totals(day, f"{kind}_avg_eur_kwh"), exists=_has_totals)
      for kind in ("charge", "discharge") for day in ("today", "tomorrow")),
    # De zon: verwachting (met de kwartieren als attribuut), wat er vandaag nog komt, de piek, tegen een gewoon jaar
    DetailSensor(key="solar_forecast_today", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 suggested_display_precision=1, value=_pv("forecast_today_kwh"), attrs=_forecast_attrs("today"), exists=_has_pv),
    DetailSensor(key="solar_forecast_tomorrow", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 suggested_display_precision=1, value=_pv("forecast_tomorrow_kwh"), attrs=_forecast_attrs("tomorrow"), exists=_has_pv),
    DetailSensor(key="solar_forecast_remaining", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 suggested_display_precision=1, value=_remaining, exists=_has_pv),
    DetailSensor(key="solar_typical_year", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 value=_pv("kwh_year"), attrs=lambda d: {"kwp": (d.stats.get("pv") or {}).get("kwp")}, exists=_has_pv,
                 entity_category=EntityCategory.DIAGNOSTIC),
    DetailSensor(key="solar_peak_today", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                 value=lambda d: (d.stats.get("solar") or {}).get("peak_w"),
                 attrs=lambda d: {"at": (d.stats.get("solar") or {}).get("peak_at")}, exists=lambda d: bool(d.stats.get("solar"))),
    DetailSensor(key="solar_vs_typical", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
                 value=lambda d: (d.pv.get("performance") or {}).get("pct"),
                 attrs=lambda d: {k: (d.pv.get("performance") or {}).get(k) for k in ("measured_kwh", "typical_kwh")},
                 exists=lambda d: bool(d.pv.get("performance"))),
    DetailSensor(key="solar_forecast_error", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
                 suggested_display_precision=1, value=lambda d: (d.pv.get("accuracy") or {}).get("mape_pct"),
                 attrs=lambda d: {"days": (d.pv.get("accuracy") or {}).get("days")}, exists=lambda d: bool(d.pv.get("accuracy"))),
    DetailSensor(key="best_forecaster", value=lambda d: _forecaster_label(d, (d.pv.get("scores") or {}).get("best")),
                 attrs=lambda d: {"days": (d.pv.get("scores") or {}).get("days"), "used": _forecaster_label(d, d.pv.get("used")),
                                  "scores": {_forecaster_label(d, k): v.get("score") for k, v in ((d.pv.get("scores") or {}).get("sources") or {}).items()}},
                 exists=lambda d: bool(d.pv.get("scores")), entity_category=EntityCategory.DIAGNOSTIC),
    # Het verwachte verbruik van het huis (uit het geleerde patroon)
    DetailSensor(key="house_forecast_today", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 suggested_display_precision=1, value=house_today, attrs=house_today_attrs, exists=lambda d: bool(d.stats.get("profile"))),
    DetailSensor(key="house_forecast_tomorrow", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                 suggested_display_precision=1, value=house_tomorrow, attrs=house_tomorrow_attrs, exists=lambda d: bool(d.stats.get("profile"))),
    # De controles (elk kwartier): hoeveel er een waarschuwing of fout geven, met welke
    DetailSensor(key="health_problems", state_class=SensorStateClass.MEASUREMENT, value=lambda d: len(_problems(d)),
                 attrs=_health_attrs, icon="mdi:stethoscope", exists=lambda d: bool(d.stats.get("health"))),
    # Versie en de nieuwste versie (de update zelf installeer je in SmartBatteryControl)
    DetailSensor(key="version", value=lambda d: d.updates.get("current"), exists=lambda d: bool(d.updates),
                 entity_category=EntityCategory.DIAGNOSTIC),
    DetailSensor(key="latest_version", value=lambda d: (d.updates.get("latest") or {}).get("version") or d.updates.get("current"),
                 attrs=lambda d: {"title": (d.updates.get("latest") or {}).get("title"), "downloaded": d.updates.get("downloaded"),
                                  "can_install": d.updates.get("can_install"),
                                  "auto_install": (d.updates.get("settings") or {}).get("auto_install")},
                 exists=lambda d: bool(d.updates), entity_category=EntityCategory.DIAGNOSTIC),
)


class DetailSensorEntity(SbcHubDetailEntity, SensorEntity):
    entity_description: DetailSensor
    _unrecorded_attributes = frozenset({"quarters", "batteries", "problems", "scores"})

    def __init__(self, entry: SbcConfigEntry, description: DetailSensor) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data) if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = self.entity_description.attrs
        return attrs(self.coordinator.data) if attrs and self.coordinator.data else None

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.state_class != SensorStateClass.TOTAL:
            return None
        return last_reset(self.entity_description.reset)


# Per voorspeller van de zon (Open-Meteo, Solcast, Forecast.Solar) die aan staat: de verwachting en de score
FORECASTER_PARTS = ("today", "tomorrow", "score")


class ForecasterSensor(SbcHubDetailEntity, SensorEntity):
    def __init__(self, entry: SbcConfigEntry, forecaster: dict, part: str) -> None:
        super().__init__(entry, f"forecaster_{forecaster['id']}_{part}", {"source": forecaster["label"]})
        self.source, self.part = forecaster["id"], part
        self._attr_translation_key = f"forecaster_{part}"
        if part == "score":
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 1
        else:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_suggested_display_precision = 1

    def _forecaster(self) -> dict:
        pv = self.coordinator.data.pv if self.coordinator.data else {}
        return next((f for f in pv.get("forecasters") or [] if f["id"] == self.source), {})

    def _score(self) -> dict:
        pv = self.coordinator.data.pv if self.coordinator.data else {}
        return ((pv.get("scores") or {}).get("sources") or {}).get(self.source) or {}

    @property
    def native_value(self):
        if self.part == "score":
            return self._score().get("score")
        return self._forecaster().get(f"{self.part}_kwh")

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.part == "score":
            score = self._score()
            return {k: score.get(k) for k in ("days", "energy_pct", "quarter_pct", "peak_pct", "peak_shift_min")}
        f = self._forecaster()
        return {"made_at": _epoch(f.get("made_at")), "error": f.get("error"), "calibration": f.get("calibration"),
                **{k: f[k] for k in ("calls_today", "calls_limit", "plan") if k in f}}


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
    "VA": (SensorDeviceClass.APPARENT_POWER, UnitOfApparentPower.VOLT_AMPERE),
    "var": (SensorDeviceClass.REACTIVE_POWER, UnitOfReactivePower.VOLT_AMPERE_REACTIVE),
}
DIAGNOSTIC_GROUPS = {"Apparaat", "Status", "Instellingen", "Wifi"}


def detail_key(sensor: dict) -> str:
    """Vast per sensor: het register (Modbus) of de groep en de naam (Local API)."""
    if sensor.get("register") is not None:
        return f"r{sensor['register']}"
    return re.sub(r"[^a-z0-9]+", "_", f"{sensor['group']}_{sensor['label']}".lower()).strip("_")


class DeviceValueSensor(SensorEntity):
    """Eén sensor uit een apparaat (batterij of omvormer), zoals SmartBatteryControl hem uitleest: de eenheid en de soort
    volgen uit de eenheid, de naam is de groep met de naam ervoor."""

    _attr_has_entity_name = True

    def _describe(self, sensor: dict, battery: bool) -> None:
        self._attr_name = f"{sensor['group']} {sensor['label']}"
        unit = sensor.get("unit") or ""
        numeric = isinstance(sensor.get("value"), (int, float)) and not isinstance(sensor.get("value"), bool)
        self._numeric = numeric  # een meetwaarde blijft een getal, ook als hij even als tekst binnenkomt
        if numeric and unit in UNITS:
            device_class, native = UNITS[unit]
            if unit == "%" and battery and sensor["group"] == "Batterij":
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
        raise NotImplementedError

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


class BatteryDetailSensor(DeviceValueSensor, CoordinatorEntity[SensorCoordinator]):
    """Eén sensor uit de batterij, zoals SmartBatteryControl hem uitleest."""

    def __init__(self, entry: SbcConfigEntry, battery: dict, sensor: dict) -> None:
        super().__init__(entry.runtime_data.sensors)
        self.battery_id = battery["id"]
        self.key = detail_key(sensor)
        self._attr_unique_id = f"{battery['id']}_detail_{self.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, battery["id"])})
        self._describe(sensor, battery=True)

    def _sensor(self) -> dict | None:
        for s in (self.coordinator.data or {}).get(self.battery_id, []):
            if detail_key(s) == self.key:
                return s
        return None


# ── omvormers en andere zonnebronnen: vermogen, totaal opgewekt, status en alle sensoren ─────────────

def solar_key(source: dict) -> str:
    """Vast per zonnebron: de naam die SmartBatteryControl hem geeft (bijv. "Omvormer (192.168.1.80)")."""
    return re.sub(r"[^a-z0-9]+", "_", source["name"].lower()).strip("_")


def _solar_detail(source: dict, group: str, label: str):
    return next((x.get("value") for x in source.get("details") or [] if x.get("group") == group and x.get("label") == label), None)


def solar_device(entry: SbcConfigEntry, source: dict) -> DeviceInfo:
    text = lambda v: str(v) if v not in (None, "") else None  # noqa: E731
    return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_solar_{solar_key(source)}")}, name=source["name"],
                      manufacturer=text(_solar_detail(source, "Apparaat", "Fabrikant")),
                      model=text(_solar_detail(source, "Apparaat", "Type")) or source.get("kind"),
                      sw_version=text(_solar_detail(source, "Apparaat", "Firmware")),
                      serial_number=text(_solar_detail(source, "Apparaat", "Serienummer")),
                      via_device=(DOMAIN, entry.entry_id))


class SolarEntity(CoordinatorEntity[StateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: SbcConfigEntry, source: dict, key: str) -> None:
        super().__init__(entry.runtime_data.state)
        self.source_key = solar_key(source)
        self._attr_unique_id = f"{entry.entry_id}_solar_{self.source_key}_{key}"
        self._attr_device_info = solar_device(entry, source)

    def _source(self) -> dict:
        return next((x for x in (self.coordinator.data or {}).get("solar_sources") or [] if solar_key(x) == self.source_key), {})


@dataclass(frozen=True, kw_only=True)
class SolarSensor(SensorEntityDescription):
    value: Callable[[dict], Any]


SOLAR_SENSORS: tuple[SolarSensor, ...] = (
    SolarSensor(key="solar_source_power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT,
                state_class=SensorStateClass.MEASUREMENT, value=lambda x: x.get("power_w")),
    SolarSensor(key="solar_source_total", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=1, value=lambda x: x.get("total_kwh") or None),
    SolarSensor(key="solar_source_status", value=lambda x: x.get("status") or x.get("error")),
)


class SolarSensorEntity(SolarEntity, SensorEntity):
    entity_description: SolarSensor

    def __init__(self, entry: SbcConfigEntry, source: dict, description: SolarSensor) -> None:
        super().__init__(entry, source, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def available(self) -> bool:
        return super().available and bool(self._source())

    @property
    def native_value(self):
        return self.entity_description.value(self._source())


class SolarDetailSensor(DeviceValueSensor, SolarEntity):
    """Eén sensor uit de omvormer (bijv. de spanning per fase of de temperatuur), zoals SmartBatteryControl hem uitleest."""

    def __init__(self, entry: SbcConfigEntry, source: dict, sensor: dict) -> None:
        self.key = detail_key(sensor)
        super().__init__(entry, source, f"detail_{self.key}")
        self._describe(sensor, battery=False)

    def _sensor(self) -> dict | None:
        return next((x for x in self._source().get("details") or [] if detail_key(x) == self.key), None)


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
        return last_reset(self.entity_description.reset)


async def async_setup_entry(hass: HomeAssistant, entry: SbcConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    state = entry.runtime_data.state
    known_hub: set[str] = set()

    @callback
    def add_hub() -> None:
        """De algemene sensoren; die voor iets wat er (nog) niet is, zoals een meter per fase, zodra het er is."""
        data = state.data or {}
        new = [d for d in HUB_SENSORS if d.key not in known_hub and (d.exists is None or d.exists(data))]
        known_hub.update(d.key for d in new)
        async_add_entities(HubSensorEntity(entry, d) for d in new)

    add_hub()
    entry.async_on_unload(state.async_add_listener(add_hub))

    details = entry.runtime_data.details
    known_detail: set[str] = set()

    @callback
    def add_detail() -> None:
        """Cijfers, dagtotalen, zon, verbruik, controles en updates; per voorspeller van de zon die aan staat."""
        data = details.data
        if data is None:
            return
        new: list[SensorEntity] = []
        for d in DETAIL_SENSORS:
            if d.key not in known_detail and (d.exists is None or d.exists(data)):
                known_detail.add(d.key)
                new.append(DetailSensorEntity(entry, d))
        for forecaster in data.pv.get("forecasters") or []:
            if forecaster.get("enabled"):
                for part in FORECASTER_PARTS:
                    key = f"forecaster_{forecaster['id']}_{part}"
                    if key not in known_detail:
                        known_detail.add(key)
                        new.append(ForecasterSensor(entry, forecaster, part))
        if new:
            async_add_entities(new)

    add_detail()
    entry.async_on_unload(details.async_add_listener(add_detail))

    known_solar: set[str] = set()

    @callback
    def add_solar() -> None:
        """Elke zonnebron (omvormer) als apparaat, met al zijn sensoren zodra ze er zijn."""
        new: list[SensorEntity] = []
        for source in (state.data or {}).get("solar_sources") or []:
            if not source.get("name"):
                continue
            key = solar_key(source)
            if key not in known_solar:
                known_solar.add(key)
                new += [SolarSensorEntity(entry, source, d) for d in SOLAR_SENSORS]
            for item in source.get("details") or []:
                if (item.get("group"), item.get("label")) == ("Status", "Status"):
                    continue  # die is er al (Status)
                uid = f"{key}_detail_{detail_key(item)}"
                if uid not in known_solar:
                    known_solar.add(uid)
                    new.append(SolarDetailSensor(entry, source, item))
        if new:
            async_add_entities(new)

    add_solar()
    entry.async_on_unload(state.async_add_listener(add_solar))

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
