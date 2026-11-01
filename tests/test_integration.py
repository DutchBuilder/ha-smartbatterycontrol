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

from custom_components.smartbatterycontrol.api import SbcAuthError, SbcError, SbcForbidden
from custom_components.smartbatterycontrol.const import DOMAIN

from .conftest import PV, UPDATES, audit_payload, battery_state, day_totals, stats_payload

NOW = datetime(2026, 10, 29, 14, 7, tzinfo=ZoneInfo("Europe/Amsterdam"))
TODAY, YESTERDAY = NOW.date().isoformat(), (NOW.date() - timedelta(days=1)).isoformat()


def fake_client(state: dict, auth_error: bool = False, old: bool = False, forbidden: bool = False):
    """Een nagebootste SmartBatteryControl; `old`: een oudere zonder /api/stats, dagtotalen, historie, zonmodel …;
    `forbidden`: het token mag de updates en de historie niet (403)."""
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

        async def plug_usage(self, since):
            return {"plugs": [{"id": "3c39e7aabbcc", "name": "Wasmachine", "kwh": 18.4, "cost_eur": 4.52, "avg_eur_per_kwh": 0.2457,
                               "days": {"2026-10-28": {"kwh": 1.1, "cost_eur": 0.27, "avg_eur_per_kwh": 0.2455}}}]}

        async def stats(self):
            if old:
                raise SbcError("HTTP 404 voor /stats")
            return stats_payload(TODAY, YESTERDAY)

        async def day_totals(self, day):
            if old:
                raise SbcError("HTTP 404 voor /plan/totals")
            return day_totals(day.isoformat(), 1 if day.isoformat() == TODAY else 2)

        async def audit(self, since, until):
            if old:
                raise SbcError("HTTP 404 voor /plan/audit")
            if forbidden:
                raise SbcForbidden("/plan/audit mag dit API-token niet")
            return audit_payload(TODAY, YESTERDAY)

        async def pv(self):
            return PV

        async def updates(self):
            if old:
                raise SbcError("HTTP 404 voor /updates")
            if forbidden:
                raise SbcForbidden("/updates mag dit API-token niet")
            return UPDATES

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


async def setup(hass: HomeAssistant, state: dict, token: str = "sbc_goed", old: bool = False,
                forbidden: bool = False) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.50", "port": 80, "token": token}, unique_id="192.168.1.50:80")
    entry.add_to_hass(hass)
    client = fake_client(state, old=old, forbidden=forbidden)
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
    assert plan.state == "1.76" and plan.attributes["slots"] == ["23:00–23:59 laden 1000 W (30% → 48%)"]
    assert hass.states.get("sensor.venus_thuis_plan_today_calculated").state == "0.76"
    assert hass.states.get("sensor.venus_thuis_charged_today").state == "5.3"
    assert hass.states.get("sensor.smartbatterycontrol_grid_power").state == "430"
    assert hass.states.get("sensor.smartbatterycontrol_solar_power").state == "1092"
    assert float(hass.states.get("sensor.smartbatterycontrol_electricity_price_now").state) == pytest.approx(0.34)
    assert hass.states.get("binary_sensor.venus_thuis_reachable").state == "on"
    assert hass.states.get("binary_sensor.venus_thuis_schedule_matches_plan").state == "on"
    assert hass.states.get("binary_sensor.venus_thuis_not_following_schedule").state == "off"
    # Vermogens, capaciteit, laadgrens bereikt, en de afweging één of twee cycli
    assert hass.states.get("sensor.venus_thuis_maximum_discharge_power").state == "2000"
    assert hass.states.get("sensor.venus_thuis_capacity").state == "5.12"
    limit = hass.states.get("binary_sensor.venus_thuis_charge_limit_reached")
    assert limit.state == "on" and limit.attributes["stops"] == ["17:00–19:00 ontlaadgrens"]
    assert hass.states.get("binary_sensor.venus_thuis_new_plan_waiting_for_push").state == "off"
    assert hass.states.get("sensor.venus_thuis_plan_today_planner_estimate").attributes["considered"]["chosen"] == 2
    # Controleschema en controle-interval
    control = hass.states.get("sensor.smartbatterycontrol_next_check")
    assert control.state == "2026-10-29T22:50:00+00:00" and control.attributes["times"]["maandag"] == "23:50"
    assert hass.states.get("sensor.smartbatterycontrol_check_interval").state == "60"
    # Stekkers: een eigen apparaat met vermogen, verbruik en kosten (vandaag en deze maand, met de gemiddelde prijs)
    assert hass.states.get("sensor.wasmachine_power").state == "512.4"
    assert hass.states.get("sensor.wasmachine_cost_today").state == "0.31"
    assert hass.states.get("sensor.wasmachine_energy_this_month").state == "18.4"
    month = hass.states.get("sensor.wasmachine_cost_this_month")
    assert month.state == "4.52" and month.attributes["days"]["2026-10-28"]["cost_eur"] == 0.27
    assert float(hass.states.get("sensor.wasmachine_average_price_this_month").state) == pytest.approx(0.2457)
    # Laadgrenzen van de batterij en het schema voor planning en pushen
    assert hass.states.get("sensor.venus_thuis_discharge_limit").state == "30.0"
    assert hass.states.get("sensor.venus_thuis_charge_limit").state == "90.0"
    assert hass.states.get("sensor.smartbatterycontrol_next_push_to_the_batteries").state == "2026-10-29T22:55:00+00:00"
    assert hass.states.get("sensor.smartbatterycontrol_next_planning").attributes["day"] == "2026-10-30"

    # De planning als tekst, met de uren erbij
    schedule = hass.states.get("sensor.venus_thuis_schedule_today")
    assert schedule.state == "23:00–23:59 laden 1000 W (30% → 48%)" and schedule.attributes["hours_charging"] == 1.0
    assert schedule.attributes["blocks"][0]["soc_end_pct"] == 48  # wat het blok met het laadniveau doet
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


def value(hass: HomeAssistant, entity_id: str) -> float:
    found = hass.states.get(entity_id)
    assert found is not None, entity_id
    return float(found.state)


async def test_metrics_per_period(hass: HomeAssistant, state) -> None:
    """De cijfers van de schermen: vandaag en dit jaar staan aan, 7 en 30 dagen staan klaar maar uit."""
    await setup(hass, state)
    hub = "sensor.smartbatterycontrol_"
    assert value(hass, f"{hub}house_consumption_today") == pytest.approx(11.0)
    assert value(hass, f"{hub}house_consumption_this_year") == pytest.approx(3300.0)
    assert value(hass, f"{hub}self_sufficiency_this_year") == pytest.approx(72.7)
    assert value(hass, f"{hub}battery_earnings_today") == pytest.approx(0.52)
    assert value(hass, f"{hub}price_spread_charging_and_discharging_today") == pytest.approx(0.12)
    assert value(hass, f"{hub}average_grid_charging_price_today") == pytest.approx(0.21)
    assert value(hass, f"{hub}solar_production_today") == pytest.approx(8.0)  # er zijn zonnepanelen
    bill = hass.states.get(f"{hub}electricity_bill_today")
    assert float(bill.state) == pytest.approx(1.21) and bill.attributes["grid_fee_eur"] == 1.3 and bill.attributes["since"] == TODAY
    today = hass.states.get(f"{hub}grid_import_today")
    assert today.attributes["state_class"] == "total" and today.attributes["device_class"] == "energy"
    assert "state_class" not in hass.states.get(f"{hub}self_sufficiency_today").attributes or \
        hass.states.get(f"{hub}self_sufficiency_today").attributes["state_class"] == "measurement"
    # 7 en 30 dagen: aangemaakt, maar uit
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    week = registry.async_get(f"{hub}house_consumption_7_days")
    assert week is not None and week.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(f"{hub}house_consumption_7_days") is None
    assert registry.async_get(f"{hub}battery_earnings_30_days") is not None

    # Per batterij: dezelfde cijfers per periode; geladen vandaag blijft dezelfde entiteit
    assert value(hass, "sensor.venus_thuis_charged_today") == pytest.approx(5.3)
    assert value(hass, "sensor.venus_thuis_charged_this_year") == pytest.approx(1590.0)
    assert value(hass, "sensor.venus_thuis_earnings_today") == pytest.approx(0.52)
    assert value(hass, "sensor.venus_thuis_cycles_this_year") == pytest.approx(258.0)
    assert value(hass, "sensor.venus_thuis_average_price_when_charging_today") == pytest.approx(0.2134)
    assert registry.async_get("sensor.venus_thuis_efficiency_7_days").disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_day_totals_and_slots(hass: HomeAssistant, state) -> None:
    """Alle batterijen samen (gemeten, daarna gepland), per batterij de eindstand, het laatste slot en gisteren."""
    await setup(hass, state)
    hub = "sensor.smartbatterycontrol_"
    assert value(hass, f"{hub}all_batteries_today") == pytest.approx(0.18)
    assert value(hass, f"{hub}all_batteries_tomorrow") == pytest.approx(0.36)
    assert value(hass, f"{hub}all_batteries_charging_per_kwh_today") == pytest.approx(0.4017)
    assert value(hass, f"{hub}all_batteries_discharging_per_kwh_tomorrow") == pytest.approx(0.5142)
    assert hass.states.get(f"{hub}all_batteries_today").attributes["batteries"] == {"Venus Thuis": 0.18}
    end = hass.states.get("sensor.venus_thuis_end_result_today")
    assert float(end.state) == pytest.approx(0.18) and end.attributes["planned"]["discharge_avg_eur_kwh"] == 0.5142
    # Het laatste slot dat klaar is (niet het slot dat nog bezig is, en niet een slotje van een minuut)
    slot = hass.states.get("sensor.venus_thuis_last_slot_per_kwh")
    assert float(slot.state) == pytest.approx(0.4)
    assert slot.attributes["goal"] == "48% → 98%" and slot.attributes["result"] == "48% → 92%"
    assert slot.attributes["kind"] == "laden" and slot.attributes["status"] == "vol: eerder klaar" and slot.attributes["day"] == TODAY
    yesterday = hass.states.get("sensor.venus_thuis_result_yesterday")
    assert float(yesterday.state) == pytest.approx(0.64) and yesterday.attributes["followed"] is True


async def test_battery_extras(hass: HomeAssistant, state) -> None:
    await setup(hass, state)
    phase = hass.states.get("sensor.venus_thuis_phase")
    assert phase.state == "L1" and phase.attributes["source"] == "automatisch herkend" and phase.attributes["counts"]["L1"] == 12
    learning = hass.states.get("sensor.venus_thuis_charge_adjustment_self_learning")
    assert float(learning.state) == pytest.approx(92.5) and learning.attributes["samples"][0].endswith("bereikt 20% → 92%")
    assert hass.states.get("sensor.venus_thuis_last_push_to_the_battery").state.startswith("2026-")
    topup = hass.states.get("binary_sensor.venus_thuis_topping_up_to_full")
    assert topup.state == "on" and topup.attributes["until"] == "14:30"
    assert hass.states.get("binary_sensor.venus_thuis_limited_in_the_app").state == "off"
    assert hass.states.get("binary_sensor.venus_thuis_self_consumption_active").attributes["setpoint_w"] == 0


async def test_solar_and_system(hass: HomeAssistant, state) -> None:
    """Fasen, de zon (verwachting, per voorspeller, de omvormer), het verwachte verbruik, de controles en updates."""
    await setup(hass, state)
    hub = "sensor.smartbatterycontrol_"
    assert [hass.states.get(f"{hub}grid_power_l{i}").state for i in (1, 2, 3)] == ["100", "200", "130"]
    forecast = hass.states.get(f"{hub}solar_forecast_today")
    assert float(forecast.state) == 10.0 and forecast.attributes["typical_kwh"] == 9.6 and forecast.attributes["forecaster"] == "Open-Meteo"
    # Om 14:07 komt er nog 3 kwartier en 52 minuten aan 1000 W: (8 min + 15 kwartieren) tot 18:00
    assert value(hass, f"{hub}solar_forecast_rest_of_today") == pytest.approx((8 / 15 + 15) * 1000 / 4000, abs=0.01)
    assert value(hass, f"{hub}solar_forecast_open_meteo_today") == 10.0
    assert value(hass, f"{hub}solar_forecast_solcast_tomorrow") == 7.7
    assert hass.states.get(f"{hub}solar_forecast_solcast_today").attributes["calls_today"] == 3
    assert value(hass, f"{hub}solar_forecast_score_solcast") == 88.2
    assert hass.states.get(f"{hub}solar_forecast_forecast_solar_today") is None  # staat uit
    assert hass.states.get(f"{hub}best_solar_forecaster").state == "Solcast"
    assert value(hass, f"{hub}solar_vs_typical_30_days") == 74
    assert value(hass, f"{hub}solar_peak_today") == 4210
    assert value(hass, f"{hub}expected_consumption_today") == pytest.approx(9.6)
    problems = hass.states.get(f"{hub}checks_with_a_problem")
    assert problems.state == "1" and problems.attributes["problems"] == ["Apparaat · Back-up: laatste back-up mislukt"]
    assert hass.states.get("binary_sensor.smartbatterycontrol_problem_in_checks").state == "on"
    assert hass.states.get("binary_sensor.smartbatterycontrol_update_available").state == "on"
    assert hass.states.get(f"{hub}latest_version").state == "0.4.2"
    assert hass.states.get(f"{hub}version").state == "0.4.1"
    # De omvormer als apparaat, met alle sensoren die SmartBatteryControl uitleest
    assert value(hass, "sensor.omvormer_192_168_1_80_power") == 1092
    total = hass.states.get("sensor.omvormer_192_168_1_80_total_produced")
    assert float(total.state) == 24520.4 and total.attributes["state_class"] == "total_increasing"
    assert hass.states.get("sensor.omvormer_192_168_1_80_status").state == "produceert"
    voltage = hass.states.get("sensor.omvormer_192_168_1_80_ac_spanning_l1")
    assert voltage.state == "231.2" and voltage.attributes["device_class"] == "voltage"
    assert hass.states.get("sensor.omvormer_192_168_1_80_ac_schijnbaar_vermogen").attributes["device_class"] == "apparent_power"
    assert "device_class" not in hass.states.get("sensor.omvormer_192_168_1_80_ac_arbeidsfactor").attributes
    assert hass.states.get("sensor.omvormer_192_168_1_80_status_status") is None  # die is er al als Status
    from homeassistant.helpers import device_registry as dr

    device = next(d for d in dr.async_get(hass).devices.values() if d.name == "Omvormer (192.168.1.80)")
    assert device.manufacturer == "SolarEdge" and device.model == "SE5K"


async def test_forbidden_part_is_not_an_invalid_token(hass: HomeAssistant, state) -> None:
    """Iets wat het token niet mag (403), zoals iets wat alleen vanuit de webinterface kan: dat onderdeel valt weg, maar
    Home Assistant vraagt niet om een nieuw token en de rest werkt."""
    entry = await setup(hass, state, forbidden=True)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert not any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())
    assert hass.states.get("sensor.smartbatterycontrol_latest_version") is None
    assert hass.states.get("sensor.venus_thuis_last_slot_per_kwh").state == "unknown"
    assert value(hass, "sensor.smartbatterycontrol_house_consumption_today") == pytest.approx(11.0)


async def test_older_smartbatterycontrol(hass: HomeAssistant, state) -> None:
    """Een oudere SmartBatteryControl zonder /api/stats: geladen vandaag uit /api/metrics, de rest komt er niet bij."""
    await setup(hass, state, old=True)
    assert value(hass, "sensor.venus_thuis_charged_today") == pytest.approx(5.3)
    assert hass.states.get("sensor.smartbatterycontrol_house_consumption_this_year") is None
    assert hass.states.get("sensor.smartbatterycontrol_all_batteries_today") is None
    assert hass.states.get("sensor.smartbatterycontrol_latest_version") is None
    assert hass.states.get("sensor.venus_thuis_last_slot_per_kwh").state == "unknown"
    assert hass.states.get("sensor.venus_thuis_state_of_charge").state == "97.0"


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


def clients(state: dict):
    """Dezelfde nagebootste client voor de configuratieflow en voor het (opnieuw) laden van de integratie."""
    client = fake_client(state)
    return (patch("custom_components.smartbatterycontrol.config_flow.SbcClient", client),
            patch("custom_components.smartbatterycontrol.SbcClient", client),
            patch("homeassistant.util.dt.now", return_value=NOW))


async def test_reconfigure(hass: HomeAssistant, state) -> None:
    entry = await setup(hass, state)
    other = MockConfigEntry(domain=DOMAIN, data={"host": "192.168.1.52", "port": 80, "token": ""}, unique_id="192.168.1.52:80")
    other.add_to_hass(hass)
    flow_patch, setup_patch, now_patch = clients(state)
    with flow_patch, setup_patch, now_patch:
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
        # Het adres van een andere koppeling, of een fout token: niet opslaan
        taken = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.1.52", "port": 80, "token": ""})
        assert taken["errors"] == {"base": "already_configured"}
        bad = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.1.51", "port": 8080, "token": "fout"})
        assert bad["errors"] == {"base": "invalid_auth"}
        # Nieuw adres en poort, token leeg: het huidige token blijft
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": " 192.168.1.51 ", "port": 8080, "token": ""})
        await hass.async_block_till_done()
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reconfigure_successful"
    assert entry.data == {"host": "192.168.1.51", "port": 8080, "token": "sbc_goed"}
    assert entry.unique_id == "192.168.1.51:8080" and entry.title == "Mock Title"  # een zelfgekozen naam blijft staan
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert hass.states.get("sensor.venus_thuis_batterij_spanning").state == "51.72"


async def test_add_again_updates_the_existing_link(hass: HomeAssistant, state) -> None:
    """Dezelfde SmartBatteryControl nog eens toevoegen (bijvoorbeeld met een nieuw token) werkt de koppeling bij."""
    entry = await setup(hass, state)
    flow_patch, setup_patch, now_patch = clients(state)
    with flow_patch, setup_patch, now_patch:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.1.50", "port": 80, "token": "sbc_nieuw"})
        await hass.async_block_till_done()
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "updated_existing"
    assert entry.data["token"] == "sbc_nieuw" and len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_remove_and_add_again(hass: HomeAssistant, state) -> None:
    entry = await setup(hass, state)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.venus_thuis_batterij_spanning") is None
    flow_patch, setup_patch, now_patch = clients(state)
    with flow_patch, setup_patch, now_patch:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.1.50", "port": 80, "token": "sbc_goed"})
        await hass.async_block_till_done()
    assert done["type"] is FlowResultType.CREATE_ENTRY
    # Alles komt terug, ook de sensoren uit de batterij en het apparaat van SmartBatteryControl zelf
    assert hass.states.get("sensor.venus_thuis_batterij_spanning").state == "51.72"
    assert hass.states.get("sensor.venus_thuis_state_of_charge").state == "97.0"
    assert hass.states.get("sensor.smartbatterycontrol_grid_power").state == "430"
