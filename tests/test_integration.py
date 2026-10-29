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

        async def details(self, battery_id, max_age=600):
            return {"via": "modbus", "age_s": 12, "sensors": [
                {"group": "Batterij", "label": "Spanning", "value": 51.72, "unit": "V", "register": 30100},
                {"group": "Net (AC)", "label": "Spanning", "value": 228.3, "unit": "V", "register": 32200},
                {"group": "Temperatuur", "label": "Cel maximaal", "value": 25.1, "unit": "°C", "register": 35010},
                {"group": "Energie", "label": "Totaal geladen", "value": 0.71, "unit": "kWh", "register": 33000},
                {"group": "Batterij", "label": "Laadcycli", "value": 195, "unit": "", "register": 34003},
                {"group": "Status", "label": "Omvormer", "value": "stand-by", "unit": "", "register": 35100},
                {"group": "Instellingen", "label": "Maximaal ontlaadvermogen", "value": 3000, "unit": "W", "register": 44003},
            ]}

        async def review(self, battery_id, day):
            return {"actual": {"charge_kwh": 5.2, "discharge_kwh": 1.0, "cost_eur": 0.46, "value_eur": 0.31, "wear_eur": 0.03,
                               "margin_eur": -0.15}}

        async def reviews(self, battery_id, days=31):
            # Eerder deze maand, en één van vorige maand (die telt niet mee)
            month = NOW.strftime("%Y-%m")
            return {"days": [
                {"day": f"{month}-01", "actual": {"charge_kwh": 4.0, "discharge_kwh": 3.5, "cost_eur": 0.40, "value_eur": 1.20, "margin_eur": 0.80}},
                {"day": "2026-09-30", "actual": {"charge_kwh": 9, "discharge_kwh": 9, "cost_eur": 9, "value_eur": 9, "margin_eur": 9}},
            ]}
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

    # De planning als tekst, met de uren erbij
    schedule = hass.states.get("sensor.venus_thuis_schedule_today")
    assert schedule.state == "23:00–23:59 laden 1000 W" and schedule.attributes["hours_charging"] == 1.0
    assert hass.states.get("sensor.venus_thuis_schedule_tomorrow").attributes["hours_charging"] == 4.0
    # Wat laden kostte en ontladen opleverde: vandaag, en deze maand (de bewaarde dagen van deze maand plus vandaag)
    assert float(hass.states.get("sensor.venus_thuis_charging_cost_today").state) == pytest.approx(0.46)
    assert float(hass.states.get("sensor.venus_thuis_discharge_value_today").state) == pytest.approx(0.31)
    assert float(hass.states.get("sensor.venus_thuis_net_result_today").state) == pytest.approx(-0.15)
    assert float(hass.states.get("sensor.venus_thuis_charging_cost_this_month").state) == pytest.approx(0.86)
    assert float(hass.states.get("sensor.venus_thuis_discharge_value_this_month").state) == pytest.approx(1.51)
    month = hass.states.get("sensor.venus_thuis_charging_cost_this_month")
    assert month.attributes["state_class"] == "total" and month.attributes["unit_of_measurement"] == "EUR"

    # Alle sensoren uit de batterij, met eenheid en soort
    spanning = hass.states.get("sensor.venus_thuis_batterij_spanning")
    assert spanning.state == "51.72" and spanning.attributes["device_class"] == "voltage"
    assert hass.states.get("sensor.venus_thuis_net_ac_spanning").state == "228.3"
    assert hass.states.get("sensor.venus_thuis_temperatuur_cel_maximaal").attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.venus_thuis_energie_totaal_geladen").attributes["state_class"] == "total_increasing"
    assert hass.states.get("sensor.venus_thuis_batterij_laadcycli").state == "195"
    assert hass.states.get("sensor.venus_thuis_status_omvormer").state == "stand-by"


async def test_planning_als_agenda(hass: HomeAssistant, state) -> None:
    await setup(hass, state)
    agenda = hass.states.get("calendar.venus_thuis_plan")
    assert agenda is not None
    # Het blok van 23:00 tot middernacht vandaag komt als eerste (het is nu 14:07)
    assert agenda.attributes["message"] == "Laden 1,0 kW" and agenda.attributes["start_time"].startswith("2026-10-29 23:00")
    from homeassistant.components.calendar import DOMAIN as CALENDAR

    events = await hass.services.async_call(CALENDAR, "get_events", {"entity_id": "calendar.venus_thuis_plan",
                                                                     "start_date_time": "2026-10-29 00:00:00",
                                                                     "end_date_time": "2026-10-31 00:00:00"},
                                            blocking=True, return_response=True)
    summaries = [e["summary"] for e in events["calendar.venus_thuis_plan"]["events"]]
    assert summaries == ["Laden 1,0 kW", "Laden 1,3 kW"]


async def test_invalid_token_starts_reauth(hass: HomeAssistant, state) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.50", "port": 80, "token": "fout"}, unique_id="x")
    entry.add_to_hass(hass)
    with patch("custom_components.smartbatterycontrol.SbcClient", fake_client(state)):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())
