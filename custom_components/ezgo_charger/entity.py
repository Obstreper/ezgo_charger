"""Shared base entity for the EZgo charger."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import EzgoCoordinator


class EzgoEntity(CoordinatorEntity[EzgoCoordinator]):
    """Base class wiring entities to the coordinator and one HA device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EzgoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer="MSI",
            model="EZgo EV Portable Charger (10A/15A)",
            name=DEFAULT_NAME,
        )

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}
