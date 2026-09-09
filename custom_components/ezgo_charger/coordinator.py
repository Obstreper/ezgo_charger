"""Bluetooth connection + polling coordinator for the EZgo charger."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CMD_ENTER,
    CMD_SET_CURRENT,
    CMD_SET_SCHEDULE,
    DEFAULT_DEVICE_ID,
    DOMAIN,
    MAX_BUFFER,
    NOTIFY_CHAR_UUID,
    NOTIFY_SETTLE,
    POLL_INTERVAL,
    REG_CMD,
    REG_POLL,
    REG_RTC_A,
    REG_RTC_B,
    STATUS_REG,
    WRITE_CHAR_UUID,
    WRITE_GAP,
)
from .protocol import build_frame, parse_frames, parse_status

_LOGGER = logging.getLogger(__name__)


class EzgoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the persistent BLE connection and the 0x77 poll loop."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        # Set explicitly so async_config_entry_first_refresh works across the HA
        # versions that do/don't accept ``config_entry`` in __init__.
        self.config_entry = entry
        self.address: str = entry.data[CONF_ADDRESS].upper()

        self._client: BleakClientWithServiceCache | None = None
        self._buffer = bytearray()
        self._device_id: bytes = DEFAULT_DEVICE_ID
        self._status: dict[str, Any] = {}
        self._seq = 0
        self._rtc_synced = False
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._ensure_connected()
            seq_before = self._status.get("_seq")
            await self._send(build_frame(REG_POLL, self._device_id + b"\x01\x77"))
        except UpdateFailed:
            raise
        except Exception as err:  # noqa: BLE001 - surface any bleak error uniformly
            raise UpdateFailed(f"Error talking to EZgo charger: {err}") from err

        # The reply comes back as a notification handled in _handle_notify. Wait
        # briefly for a fresh one before returning the last-known status.
        deadline = self.hass.loop.time() + NOTIFY_SETTLE
        while self.hass.loop.time() < deadline:
            if self._status.get("_seq") != seq_before:
                break
            await asyncio.sleep(0.1)
        return dict(self._status)

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self._disconnect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return

        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if ble_device is None:
                raise UpdateFailed(
                    f"EZgo charger {self.address} not found - out of range of "
                    "every adapter and Bluetooth proxy, or already connected to "
                    "the phone app (the charger only accepts one connection)."
                )

            _LOGGER.debug("Connecting to EZgo charger %s", self.address)
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                disconnected_callback=self._handle_disconnect,
            )
            await client.start_notify(NOTIFY_CHAR_UUID, self._handle_notify)
            self._client = client
            self._buffer.clear()
            _LOGGER.debug("Connected to EZgo charger %s", self.address)

        # best-effort clock sync, mirrors what the phone app does on connect
        if not self._rtc_synced:
            try:
                await self._sync_rtc()
                self._rtc_synced = True
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("RTC sync skipped: %s", err)

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            if client.is_connected:
                await client.stop_notify(NOTIFY_CHAR_UUID)
                await client.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error during disconnect: %s", err)

    @callback
    def _handle_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("EZgo charger %s disconnected", self.address)
        self._client = None
        self._buffer.clear()
        self._rtc_synced = False

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    @callback
    def _handle_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._buffer += data
        if len(self._buffer) > MAX_BUFFER:
            del self._buffer[:-512]

        for reg, payload in parse_frames(self._buffer):
            if reg != STATUS_REG:
                _LOGGER.debug("Ignoring 0x%02x frame (%s bytes)", reg, len(payload))
                continue
            status = parse_status(payload)
            if not status:
                continue
            learned = status.pop("device_id_bytes", None)
            if learned and any(learned) and learned != self._device_id:
                _LOGGER.debug("Learned device id %r", status.get("device_id"))
                self._device_id = learned
            status["_seq"] = self._seq = self._seq + 1
            self._status = status
            self.async_set_updated_data(status)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def _send(self, frame: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise UpdateFailed("EZgo charger not connected")
        await self._client.write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

    async def _sync_rtc(self) -> None:
        now = dt_util.now()
        ts6 = bytes(
            (now.year % 100, now.month, now.day, now.hour, now.minute, now.second)
        )
        async with self._write_lock:
            await self._send(build_frame(REG_RTC_A, self._device_id + ts6))
            await asyncio.sleep(WRITE_GAP)
            await self._send(build_frame(REG_RTC_B, self._device_id + b"\x01" + ts6))

    async def async_set_current(self, amps: int) -> None:
        """Write the charge-current setpoint (0x16 / 0x12, one byte of amps)."""
        await self._ensure_connected()
        async with self._write_lock:
            await self._send(build_frame(REG_CMD, bytes((CMD_ENTER,))))
            await asyncio.sleep(WRITE_GAP)
            await self._send(
                build_frame(REG_CMD, bytes((CMD_SET_CURRENT, amps & 0xFF)))
            )
        await asyncio.sleep(WRITE_GAP)
        await self.async_request_refresh()

    async def async_set_schedule_minutes(self, minutes: int) -> None:
        """Write the scheduled-start delay (0x16 / 0x13, u16 LE minutes-from-now)."""
        minutes = max(0, min(0xFFFF, int(minutes)))
        await self._ensure_connected()
        async with self._write_lock:
            await self._send(build_frame(REG_CMD, bytes((CMD_ENTER,))))
            await asyncio.sleep(WRITE_GAP)
            await self._send(
                build_frame(
                    REG_CMD,
                    bytes((CMD_SET_SCHEDULE,)) + minutes.to_bytes(2, "little"),
                )
            )
        await asyncio.sleep(WRITE_GAP)
        await self.async_request_refresh()
