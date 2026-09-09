"""Time platform - scheduled charge start.

The charger stores the schedule as "minutes from now until start" (register
0x16 / sub 0x13, u16 LE), not a wall-clock time. This entity lets the user pick a
clock time; we convert it to a delay on write, and on read we convert the
charger's live seconds-remaining countdown (0x77 +62) back to a clock time.
"""

from __future__ import annotations

from datetime import time, timedelta

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EzgoCoordinator
from .entity import EzgoEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EzgoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EzgoScheduledStartTime(coordinator)])


class EzgoScheduledStartTime(EzgoEntity, TimeEntity, RestoreEntity):
    """time.ezgo_scheduled_start."""

    _attr_translation_key = "scheduled_start"
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: EzgoCoordinator) -> None:
        super().__init__(coordinator, "scheduled_start")
        self._picked: time | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._picked = time.fromisoformat(last.state)
            except ValueError:
                self._picked = None

    @property
    def native_value(self) -> time | None:
        # Prefer the charger's own countdown - it is authoritative and live.
        secs = self._data.get("schedule_seconds")
        if secs:
            target = dt_util.now() + timedelta(seconds=int(secs))
            return target.time().replace(second=0, microsecond=0)
        return self._picked

    async def async_set_value(self, value: time) -> None:
        now = dt_util.now()
        target = now.replace(
            hour=value.hour, minute=value.minute, second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        minutes = max(1, round((target - now).total_seconds() / 60))
        await self.coordinator.async_set_schedule_minutes(minutes)
        self._picked = value
        self.async_write_ha_state()
