"""Config flow for the MSI EZgo EV charger."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import format_mac

from .const import DEFAULT_ADDRESS, DEFAULT_NAME, DOMAIN


class EzgoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EZgo charger."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str = DEFAULT_NAME

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered over Bluetooth."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name, data={CONF_ADDRESS: self._address}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup (MAC pre-filled with the known charger address)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if len(address) != 17 or address.count(":") != 5:
                errors["base"] = "invalid_address"
            else:
                await self.async_set_unique_id(
                    format_mac(address), raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME, data={CONF_ADDRESS: address}
                )

        # Offer anything already seen by HA's Bluetooth stack whose name looks
        # like the charger, else fall back to the known default.
        default = DEFAULT_ADDRESS
        for info in async_discovered_service_info(self.hass):
            if (info.name or "").upper().startswith("EV_"):
                default = info.address
                break

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS, default=default): str}
            ),
            errors=errors,
        )
