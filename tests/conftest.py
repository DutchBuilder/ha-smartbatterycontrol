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
            "power_check": {"expected_w": -2500, "deviating": False, "active": False, "minutes": 0, "pushing": False},
            "plans": {
                day: {"estimate_eur": 1.76, "cycles": 2, "slots": [{**slot, "start": "23:00", "end": "23:59", "power_w": -1000}]},
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
    }
