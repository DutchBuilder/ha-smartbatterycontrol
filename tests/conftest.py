"""Testopzet: een nagebootste SmartBatteryControl-API voor de integratie."""
from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def battery_state(day: str, tomorrow: str) -> dict:
    slot = {"days": [3], "enabled": True}
    return {
        "batteries": [{
            "id": "aabbccddeeff", "name": "Venus Thuis", "ip": "192.168.1.60", "primary": "modbus", "control": True,
            "error": None,
            "status": {"soc": 97.0, "ac_power_w": -1200, "battery_power_w": 1150, "mode": "manual",
                       "realtime_control": False, "via": "modbus", "timestamp": f"{day}T14:00:00+01:00"},
            "schedule_access": {"ok": True, "reason": "via Modbus"},
            "slots": [{**slot, "start": "17:00", "end": "19:00", "power_w": 2091}],
            "planned": [{**slot, "start": "17:00", "end": "19:00", "power_w": 2091}],
            "realtime": {"active": False, "setpoint_w": 0},
            "measured_rte_pct": 86.4,
            "min_soc_pct": 30.0, "max_soc_pct": 90.0, "capacity_kwh": 5.12, "max_charge_w": 2500, "max_discharge_w": 2000,
            "limit_stops": [{"start": "17:00", "end": "19:00", "reason": "min"}], "push_pending": False, "in_sync": True,
            "power_check": {"expected_w": -2500, "deviating": False, "active": False, "minutes": 0, "pushing": False,
                            "app_limited": False},
            "phase": "L1", "phase_info": {"source": "auto", "since": f"{day}T02:23:05+01:00", "votes": 12, "counts": [12, 0, 0],
                                          "needed": 5, "detected": "L1"},
            "topup": {"active": True, "power_w": 1500, "start": "14:00", "until": "14:30", "note": "bijladen tot vol"},
            "learning": {"enabled": True, "factor": 0.925, "samples": [
                {"day": day, "start": "02:00", "end": "05:00", "from": 20.0, "target_from": 20.0, "target": 100.0, "result": 92.0,
                 "ratio": 0.9, "factor": 0.9}]},
            "pushed_at": 1793487350.7, "enforce_w": 0,
            "plans": {
                day: {"estimate_eur": 1.76, "cycles": 2, "slots": [{**slot, "start": "23:00", "end": "23:59", "power_w": -1000}],
                      "soc": [{"start": "23:00", "end": "23:59", "power_w": -1000, "soc_start_pct": 30, "soc_end_pct": 48}],
                      "considered": {"split": "12:00", "one": 1.2, "two": 1.76, "chosen": 2}, "corrections": []},
                tomorrow: {"estimate_eur": 0.8, "cycles": 2, "slots": [{**slot, "start": "01:00", "end": "05:00", "power_w": -1258}]},
            },
        }],
        "meter": {"power_w": 430, "phases_w": [100, 200, 130], "source": "HomeWizard P1 (192.168.1.70)"},
        "solar": {"power_w": 1092, "complete": True},
        "solar_sources": [{"name": "Omvormer (192.168.1.80)", "kind": "sunspec", "ok": True, "power_w": 1092, "total_kwh": 24520.4,
                           "status": "produceert", "details": [
                               {"group": "Apparaat", "label": "Fabrikant", "value": "SolarEdge", "unit": ""},
                               {"group": "Apparaat", "label": "Type", "value": "SE5K", "unit": ""},
                               {"group": "AC", "label": "Spanning L1", "value": 231.2, "unit": "V"},
                               {"group": "AC", "label": "Schijnbaar vermogen", "value": 1100.0, "unit": "VA"},
                               {"group": "AC", "label": "Arbeidsfactor", "value": 99.1, "unit": "%"},
                               {"group": "Temperatuur", "label": "Koellichaam", "value": 31.5, "unit": "°C"},
                               {"group": "Status", "label": "Status", "value": "produceert", "unit": ""}]}],
        "quarter_prices": {day: [{"start": f"{day}T{h:02d}:{m:02d}:00+01:00", "spot": 0.1, "buy": 0.2 + h / 100,
                                  "feed_in": 0.2 + h / 100} for h in range(24) for m in (0, 15, 30, 45)]},
        "price_source": {day: "ENTSO-E"},
        "strategy": "classic",
        "plugs": [{"id": "3c39e7aabbcc", "host": "192.168.1.90", "name": "Wasmachine", "kind": "homewizard", "model": "HWE-SKT",
                   "power_w": 512.4, "today_kwh": 1.25, "today_cost_eur": 0.31, "on": True, "error": None}],
        "schedule": {"plan_schedule": True, "push_schedule": True, "writes_today": True, "control_schedule": True,
                     "next_control": f"{day}T23:50:00+01:00", "check_interval_s": 60,
                     "plan_times": ["15:00"] * 7, "push_times": ["23:55"] * 7, "control_times": ["23:50"] * 7,
                     "next_planning": {"day": tomorrow, "at": f"{day}T15:00:00+01:00"}, "next_push": f"{day}T23:55:00+01:00"},
    }


def period(key: str, since: str, scale: float) -> dict:
    """De cijfers van één periode zoals /api/stats ze geeft (alles maal `scale`)."""
    return {
        "key": key, "label": key, "since": since,
        "charged_kwh": 5.3 * scale, "discharged_kwh": 4.4 * scale, "battery_savings_eur": 0.52 * scale, "efficiency_pct": 83.0,
        "cheap_price_eur": 0.21, "discharge_price_eur": 0.33, "grid_to_battery_kwh": 4.0 * scale, "solar_to_battery_kwh": 1.3 * scale,
        "battery_share_pct": 40.0, "battery_exported_kwh": 0.5 * scale, "house_kwh": 11.0 * scale, "import_kwh": 7.0 * scale,
        "export_kwh": 2.0 * scale, "grid_to_house_kwh": 3.0 * scale, "grid_cost_eur": 1.4 * scale, "self_sufficiency_pct": 72.7,
        "has_solar": True, "solar_kwh": 8.0 * scale, "solar_direct_kwh": 4.0 * scale, "solar_exported_kwh": 1.5 * scale,
        "solar_kept_kwh": 5.3 * scale, "solar_self_use_pct": 77.9, "solar_share_pct": 36.4, "solar_value_eur": 2.1 * scale,
        "electricity": {"days": 1, "import_kwh": 7.0 * scale, "import_price": 0.25, "import_eur": 1.75 * scale, "export_kwh": 2.0 * scale,
                        "export_price": 0.17, "export_eur": 0.34 * scale, "export_bonus_eur": 0.0, "usage_eur": 1.41 * scale,
                        "standing_eur": 0.2, "grid_fee_eur": 1.3, "tax_reduction_eur": -1.7, "settlement_eur": -0.2,
                        "total_eur": 1.21 * scale, "total_per_kwh": 0.17},
        "batteries": [{"id": "aabbccddeeff", "name": "Venus Thuis", "charged_kwh": 5.3 * scale, "discharged_kwh": 4.4 * scale,
                       "savings_eur": 0.52 * scale, "efficiency_pct": 83.0, "cycles": 0.86 * scale, "charge_price_eur": 0.2134,
                       "discharge_price_eur": 0.3321}],
    }


def stats_payload(day: str, yesterday: str) -> dict:
    quarters = [0.0] * 32 + [1000.0] * 40 + [0.0] * 24  # 08:00 tot 18:00 elk kwartier 1000 W: 10 kWh
    return {
        "generated_at": f"{day}T14:07:00+01:00",
        "periods": [period("today", day, 1), period("week", "2026-10-23", 7), period("month", "2026-09-30", 30),
                    period("year", "2026-01-01", 300)],
        "batteries": [{"id": "aabbccddeeff", "name": "Venus Thuis", "rte_pct": 86.4, "reviews": [
            {"day": yesterday, "followed": True, "rte_pct": 84.1, "planned_eur": 0.70, "actual_eur": 0.64, "charge_kwh": 9.1,
             "discharge_kwh": 7.5}]}],
        "solar": {"power_w": 1092, "today_kwh": 8.0, "peak_w": 4210, "peak_at": "12:45"},
        "pv": {"kwp": 9.1, "kwh_year": 7773, "forecast_today_kwh": 10.0, "forecast_tomorrow_kwh": 8.8, "typical_today_kwh": 9.6,
               "typical_tomorrow_kwh": 9.7, "forecast_today_w": quarters, "forecast_tomorrow_w": quarters, "calibration": 1.0},
        "profile": {"days_learned": 21, "forecast_w": {day: [400] * 96, yesterday: [1] * 96}},
        "health": {"checked_at": 1793491231.9, "checks": [
            {"category": "Batterijen", "name": "Venus Thuis bereikbaar", "status": "ok", "message": "via Modbus"},
            {"category": "Prijzen", "name": "Prijzen morgen", "status": "info", "message": "nog niet beschikbaar"},
            {"category": "Apparaat", "name": "Back-up", "status": "warning", "message": "laatste back-up mislukt"}]},
    }


def day_totals(day: str, scale: float) -> dict:
    battery = {"id": "aabbccddeeff", "name": "Venus Thuis", "charge_kwh": 4.76, "charge_eur": -1.91 * scale,
               "charge_avg_eur_kwh": 0.4017, "discharge_kwh": 4.06, "discharge_eur": 2.09 * scale, "discharge_avg_eur_kwh": 0.5142,
               "total_eur": 0.18 * scale, "complete": True,
               "measured": {"charge_kwh": 4.76, "charge_eur": -1.91, "charge_avg_eur_kwh": 0.4017, "discharge_kwh": 0.0,
                            "discharge_eur": 0.0, "discharge_avg_eur_kwh": None, "total_eur": -1.91},
               "planned": {"charge_kwh": 0.0, "charge_eur": 0.0, "charge_avg_eur_kwh": None, "discharge_kwh": 4.06,
                           "discharge_eur": 2.09, "discharge_avg_eur_kwh": 0.5142, "total_eur": 2.09}}
    return {"day": day, "measured_until": f"{day}T14:00:00+01:00", "planned_from": f"{day}T14:00:00+01:00", "batteries": [battery],
            "charge_kwh": 4.76, "charge_eur": -1.91 * scale, "charge_avg_eur_kwh": 0.4017, "discharge_kwh": 4.06,
            "discharge_eur": 2.09 * scale, "discharge_avg_eur_kwh": 0.5142, "total_eur": 0.18 * scale, "complete": True}


def audit_payload(day: str, yesterday: str) -> dict:
    def block(start, end, kind, kwh, eur, status, **extra):
        return {"start": start, "end": end, "kind": kind, "power_w": -2000 if kind == "charge" else 2000, "manual": False, "by": "sbc",
                "planned_kwh": kwh, "actual_kwh": kwh, "planned_eur": eur, "actual_eur": eur, "status": status, **extra}
    return {"first": yesterday, "last": day, "days": [
        {"day": day, "batteries": [{"id": "aabbccddeeff", "name": "Venus Thuis", "blocks": [
            block("10:15", "13:30", "charge", 3.0, -1.2, "full", target_start_pct=48, target_pct=98, soc_start_pct=48.2, soc_end_pct=92.4),
            block("13:30", "13:31", "charge", 0.01, -0.0, "ok"),  # te klein om te tellen
            block("13:45", "18:00", "discharge", 1.0, 0.5, "running")]}]},  # nog bezig
        {"day": yesterday, "batteries": [{"id": "aabbccddeeff", "name": "Venus Thuis", "blocks": [
            block("17:00", "19:00", "discharge", 4.0, 2.0, "ok")]}]},
    ]}


PV = {
    "used": "openmeteo",
    "forecasters": [
        {"id": "openmeteo", "label": "Open-Meteo", "enabled": True, "made_at": 1793488500.5, "error": None, "today_kwh": 10.0,
         "tomorrow_kwh": 8.8, "calibration": 1.0},
        {"id": "solcast", "label": "Solcast", "enabled": True, "made_at": None, "error": None, "today_kwh": 9.1, "tomorrow_kwh": 7.7,
         "calibration": 0.96, "calls_today": 3, "calls_limit": 10},
        {"id": "fsolar", "label": "Forecast.Solar", "enabled": False, "today_kwh": None, "tomorrow_kwh": None},
    ],
    "scores": {"days": 14, "best": "solcast", "sources": {
        "openmeteo": {"days": 9, "score": 81.5, "energy_pct": 84.0, "quarter_pct": 78.0, "peak_pct": 80.0, "peak_shift_min": 20.0},
        "solcast": {"days": 9, "score": 88.2, "energy_pct": 90.0, "quarter_pct": 86.0, "peak_pct": 88.0, "peak_shift_min": 10.0}}},
    "accuracy": {"mape_pct": 12.3, "days": 9},
    "performance": {"measured_kwh": 140.2, "typical_kwh": 190.0, "pct": 74},
}
UPDATES = {"current": "0.4.1", "kind": "package", "can_install": True, "latest": {"version": "0.4.2", "title": "Nieuwe cijfers"},
           "downloaded": "0.4.2", "settings": {"auto_install": False}}
