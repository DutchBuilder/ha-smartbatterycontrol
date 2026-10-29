"""Instellen: adres van SmartBatteryControl en een API-token (Instellingen → Beveiliging in SmartBatteryControl).

Opnieuw toevoegen op een adres dat al gekoppeld is, werkt die koppeling bij (in plaats van te weigeren); via
"Opnieuw configureren" zijn adres, poort en token later aan te passen.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SbcAuthError, SbcClient, SbcError
from .const import DEFAULT_PORT, DOMAIN

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_TOKEN, default=""): str,
})


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {CONF_HOST: user_input[CONF_HOST].strip(), CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
            CONF_TOKEN: user_input.get(CONF_TOKEN, "").strip()}


def _unique_id(data: Mapping[str, Any]) -> str:
    return f"{data[CONF_HOST]}:{data.get(CONF_PORT, DEFAULT_PORT)}"


def _title(data: Mapping[str, Any]) -> str:
    return f"SmartBatteryControl ({data[CONF_HOST]})"


class SbcConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> dict[str, str]:
        client = SbcClient(async_get_clientsession(self.hass), data[CONF_HOST], data.get(CONF_PORT, DEFAULT_PORT),
                           data.get(CONF_TOKEN, ""))
        try:
            await client.version()
            await client.state()  # met token: controleert of het token klopt
        except SbcAuthError:
            return {"base": "invalid_auth"}
        except SbcError:
            return {"base": "cannot_connect"}
        return {}

    def _entry_for(self, unique_id: str) -> ConfigEntry | None:
        return next((e for e in self._async_current_entries(include_ignore=False) if e.unique_id == unique_id), None)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            errors = await self._validate(data)
            if not errors:
                unique_id = _unique_id(data)
                if (existing := self._entry_for(unique_id)) is not None:
                    # Al gekoppeld, bijvoorbeeld een koppeling met een ingetrokken token: bijwerken en opnieuw laden
                    return self.async_update_reload_and_abort(existing, data=data, reason="updated_existing")
                await self.async_set_unique_id(unique_id, raise_on_progress=False)
                return self.async_create_entry(title=_title(data), data=data)
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
                                    errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            data[CONF_TOKEN] = data[CONF_TOKEN] or entry.data.get(CONF_TOKEN, "")  # leeg: het huidige token houden
            unique_id = _unique_id(data)
            other = self._entry_for(unique_id)
            if other is not None and other.entry_id != entry.entry_id:
                errors = {"base": "already_configured"}
            else:
                errors = await self._validate(data)
            if not errors:
                # Een zelfgekozen naam laten staan; alleen de standaardnaam volgt het nieuwe adres
                title = _title(data) if entry.title == _title(entry.data) else entry.title
                return self.async_update_reload_and_abort(entry, unique_id=unique_id, title=title, data=data)
        current = {CONF_HOST: entry.data[CONF_HOST], CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT)}
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=self.add_suggested_values_to_schema(
            SCHEMA, {**current, **(user_input or {}), CONF_TOKEN: ""}))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN].strip()}
            errors = await self._validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
                                    errors=errors)
