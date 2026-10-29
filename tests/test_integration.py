"""Instellen via de configuratieflow, sensoren per batterij en algemeen, en een ongeldig token."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbatterycontrol.api import SbcAuthError, SbcError
from custom_components.smartbatterycontrol.const import DOMAIN

from .conftest import battery_state

NOW = datetime(2026, 10, 29, 14, 7, tzinfo=ZoneInfo("Europe/Amsterdam"))


def fake_client(state: dict, auth_error: bool = False):
    class Client:
        def __init__(self, session, host, port=80, token=""):
            self.base = f"http://{host}:{port}/api"
            self.token = token

        async def version(self):
            return "0.1.0"

        async def state(self):
            if auth_error or self.token == "fout":
                raise SbcAuthError("API-token ontbreekt of klopt niet")
            return state

        async def calculation(self, battery_id, day):
            if day.isoformat() != NOW.date().isoformat():
                raise SbcError("geen prijzen")
            return {"totals": {"net_eur": 0.76, "charge_kwh": 4.9}, "blocks": [
                {"start": "2026-10-29T12:00:00+01:00", "end": "2026-10-29T14:00:00+01:00", "kind": "charge", "energy_kwh": 4.85}]}

        async def metrics(self, hours):
            return {"batteries": {"aabbccddeeff": {"charged_kwh": 5.3, "discharged_kwh": 0.1}}}
    return Client


@pytest.fixture
def state():
    return battery_state(NOW.date().isoformat(), (NOW.date() + timedelta(days=1)).isoformat())


async def setup(hass: HomeAssistant, state: dict, token: str = "sbc_goed") -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.50", "port": 80, "token": token}, unique_id="192.168.1.50:80")
    entry.add_to_hass(hass)
    client = fake_client(state)
    with patch("custom_components.smartbatterycontrol.SbcClient", client), \
            patch("homeassistant.util.dt.now", return_value=NOW):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, state) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.smartbatterycontrol.config_flow.SbcClient", fake_client(state)):
        bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.1.50", "port": 80, "token": "fout"})
        assert bad["errors"] == {"base": "invalid_auth"}
        with patch("custom_components.smartbatterycontrol.SbcClient", fake_client(state)):
            done = await hass.config_entries.flow.async_configure(result["flow_id"],
                                                                  {"host": "192.168.1.50", "port": 80, "token": "sbc_goed"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["data"]["token"] == "sbc_goed"


async def test_sensors(hass: HomeAssistant, state) -> None:
    await setup(hass, state)
    assert hass.states.get("sensor.venus_thuis_state_of_charge").state == "97.0"
    assert hass.states.get("sensor.venus_thuis_power").state == "-1200"
    assert hass.states.get("sensor.venus_thuis_round_trip_efficiency_30_days").state == "86.4"
    plan = hass.states.get("sensor.venus_thuis_plan_today_planner_estimate")
    assert plan.state == "1.76" and plan.attributes["slots"] == ["23:00–23:59 laden 1000 W"]
    assert hass.states.get("sensor.venus_thuis_plan_today_calculated").state == "0.76"
    assert hass.states.get("sensor.venus_thuis_charged_today").state == "5.3"
    assert hass.states.get("sensor.smartbatterycontrol_grid_power").state == "430"
    assert hass.states.get("sensor.smartbatterycontrol_solar_power").state == "1092"
    assert float(hass.states.get("sensor.smartbatterycontrol_electricity_price_now").state) == pytest.approx(0.34)
    assert hass.states.get("binary_sensor.venus_thuis_reachable").state == "on"
    assert hass.states.get("binary_sensor.venus_thuis_schedule_matches_plan").state == "on"
    assert hass.states.get("binary_sensor.venus_thuis_not_following_schedule").state == "off"


async def test_invalid_token_starts_reauth(hass: HomeAssistant, state) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.50", "port": 80, "token": "fout"}, unique_id="x")
    entry.add_to_hass(hass)
    with patch("custom_components.smartbatterycontrol.SbcClient", fake_client(state)):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())
