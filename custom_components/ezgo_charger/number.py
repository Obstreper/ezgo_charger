"""Number platform - charge-current setpoint."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CURRENT_MIN, DOMAIN
from .coordinator import EzgoCoordinator
from .entity import EzgoEntity

# Serialise writes - they go out over one BLE link.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzgoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EzgoChargeCurrentNumber(coordinator)])


class EzgoChargeCurrentNumber(EzgoEntity, NumberEntity):
    """number.ezgo_charge_current -> register 0x16 / sub 0x12 (one byte, amps)."""

    _attr_translation_key = "charge_current"
    _attr_native_min_value = CURRENT_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: EzgoCoordinator) -> None:
        super().__init__(coordinator, "charge_current")

    @property
    def native_max_value(self) -> float:
        """Follow the charger's pigtail rating (0x77 +24) when it's known."""
        return self.coordinator.current_max

    @property
    def native_value(self) -> float | None:
        return self._data.get("charge_current")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_current(int(round(value)))
