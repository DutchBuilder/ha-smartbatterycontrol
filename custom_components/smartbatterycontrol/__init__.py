"""SmartBatteryControl: thuisbatterijen, plannen en prijzen van een lokale SmartBatteryControl in Home Assistant."""
from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SbcAuthError, SbcClient, SbcError
from .const import DEFAULT_PORT
from .coordinator import DetailCoordinator, SbcConfigEntry, SbcData, SensorCoordinator, StateCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CALENDAR]


async def async_setup_entry(hass: HomeAssistant, entry: SbcConfigEntry) -> bool:
    client = SbcClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT),
                       entry.data.get(CONF_TOKEN, ""))
    try:
        version = await client.version()
    except SbcAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except SbcError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc
    state = StateCoordinator(hass, entry, client)
    await state.async_config_entry_first_refresh()
    details = DetailCoordinator(hass, entry, client, state)
    await details.async_config_entry_first_refresh()
    sensors = SensorCoordinator(hass, entry, client, state)
    await sensors.async_config_entry_first_refresh()
    entry.runtime_data = SbcData(client, state, details, sensors, version)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SbcConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
