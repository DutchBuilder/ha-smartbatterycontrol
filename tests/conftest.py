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
            "power_check": {"expected_w": -2500, "deviating": False, "active": False, "minutes": 0, "pushing": False},
            "plans": {
                day: {"estimate_eur": 1.76, "cycles": 2, "slots": [{**slot, "start": "23:00", "end": "23:59", "power_w": -1000}],
                      "soc": [{"start": "23:00", "end": "23:59", "power_w": -1000, "soc_start_pct": 30, "soc_end_pct": 48}],
                      "considered": {"split": "12:00", "one": 1.2, "two": 1.76, "chosen": 2}, "corrections": []},
                tomorrow: {"estimate_eur": 0.8, "cycles": 2, "slots": [{**slot, "start": "01:00", "end": "05:00", "power_w": -1258}]},
            },
        }],
        "meter": {"power_w": 430, "phases_w": [100, 200, 130], "source": "HomeWizard P1 (192.168.1.70)"},
        "solar": {"power_w": 1092, "complete": True},
        "solar_sources": [{"name": "Omvormer (192.168.1.80)", "ok": True}],
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
