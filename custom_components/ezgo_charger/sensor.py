"""Sensor platform - telemetry decoded from the 0x77 status frame."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .coordinator import EzgoCoordinator
from .entity import EzgoEntity


@dataclass(frozen=True, kw_only=True)
class EzgoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], StateType]


SENSORS: tuple[EzgoSensorDescription, ...] = (
    EzgoSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("voltage"),
    ),
    *(
        EzgoSensorDescription(
            key=f"temp_{n}",
            translation_key=f"temp_{n}",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=(lambda d, n=n: d.get(f"temp_{n}")),
        )
        for n in (1, 2, 3, 4)
    ),
    EzgoSensorDescription(
        key="state",
        translation_key="state",
        icon="mdi:state-machine",
        value_fn=lambda d: d.get("state_name"),
    ),
    EzgoSensorDescription(
        key="schedule_countdown",
        translation_key="schedule_countdown",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:timer-outline",
        value_fn=lambda d: d.get("schedule_seconds"),
    ),
    # --- placeholder live-charging sensors ---------------------------------
    # 0x77 offsets +36..+43 are believed to hold current/power/energy but the
    # split is unconfirmed (every capture so far was idle). Disabled by default;
    # they report "unknown" until parse_status() fills them in.
    EzgoSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("live_current"),
    ),
    EzgoSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("live_power"),
    ),
    EzgoSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("live_energy"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzgoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(EzgoSensor(coordinator, desc) for desc in SENSORS)


class EzgoSensor(EzgoEntity, SensorEntity):
    entity_description: EzgoSensorDescription

    def __init__(
        self, coordinator: EzgoCoordinator, description: EzgoSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self._data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "state":
            return {"raw": self._data.get("state")}
        return None
