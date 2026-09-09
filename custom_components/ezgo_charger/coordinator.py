"""Bluetooth connection + polling coordinator for the EZgo charger."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
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
    SERVICE_UUID,
    STATUS_REG,
    WRITE_CHAR_UUID,
    WRITE_GAP,
)
from .protocol import build_frame, parse_frames, parse_status

_LOGGER = logging.getLogger(__name__)


def _reverse_uuid(uuid: str) -> str:
    """Return *uuid* with its 16 raw bytes reversed.

    Connecting to this charger through an ESPHome Bluetooth proxy has been seen
    to report every 128-bit UUID with the byte order flipped end-to-end, e.g.
    ``49535343-8841-43f4-a8d4-ecbe34729bb3`` comes back as
    ``b39b7234-beec-d4a8-f443-418843535349``. We look the characteristics up
    under both spellings so the integration works regardless of which side of
    that quirk we land on.
    """
    return str(UUID(bytes=UUID(uuid).bytes[::-1]))


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
        # Resolved from the discovered GATT DB on connect - may be found under the
        # expected UUID or its byte-reversed variant (ESPHome proxy quirk).
        self._notify_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
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

            self._client = await self._connect(ble_device)

        # best-effort clock sync, mirrors what the phone app does on connect
        if not self._rtc_synced:
            try:
                await self._sync_rtc()
                self._rtc_synced = True
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("RTC sync skipped: %s", err)

    async def _connect(self, ble_device) -> BleakClientWithServiceCache:
        """Connect, verify the charger's GATT layout, and subscribe to notifies.

        The charger's UART service (``SERVICE_UUID``) carries the notify char at
        ``NOTIFY_CHAR_UUID`` and the write char at ``WRITE_CHAR_UUID`` - verified
        against the btsnoop GATT discovery (see PROTOCOL.md). When we connect
        through an ESPHome Bluetooth proxy two quirks have been observed:

        * the services can come back from a stale cache that predates a full
          discovery (the charger only accepts one connection, so an early
          attempt while the phone app was paired can cache a half-populated DB);
        * every 128-bit UUID can come back byte-reversed, so the characteristics
          appear under ``_reverse_uuid(...)`` instead of their real UUIDs.

        Resolve the notify/write characteristics under either spelling; on a
        genuine miss, clear the cache and rediscover once before giving up.
        """
        for attempt in range(2):
            _LOGGER.debug(
                "Connecting to EZgo charger %s (attempt %d/2)", self.address, attempt + 1
            )
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                disconnected_callback=self._handle_disconnect,
            )

            notify_char = self._resolve_char(client, NOTIFY_CHAR_UUID)
            write_char = self._resolve_char(client, WRITE_CHAR_UUID)
            missing = [
                name
                for name, char in (("notify", notify_char), ("write", write_char))
                if char is None
            ]
            if not missing:
                try:
                    await client.start_notify(notify_char, self._handle_notify)
                except BleakError as err:
                    _LOGGER.debug("start_notify failed: %s", err)
                    missing = ["notify"]
                else:
                    self._notify_char = notify_char
                    self._write_char = write_char
                    self._buffer.clear()
                    _LOGGER.debug("Connected to EZgo charger %s", self.address)
                    return client

            _LOGGER.warning(
                "EZgo charger %s: %s characteristic(s) not in the discovered GATT "
                "services (attempt %d/2), under either the expected UUIDs or their "
                "byte-reversed variants. Expected them under service %s. "
                "Discovered: %s",
                self.address,
                "/".join(missing),
                attempt + 1,
                SERVICE_UUID,
                self._describe_services(client),
            )

            if attempt == 0:
                with contextlib.suppress(Exception):
                    await client.clear_cache()
            with contextlib.suppress(Exception):
                await client.disconnect()

        raise UpdateFailed(
            f"EZgo charger {self.address}: notify characteristic {NOTIFY_CHAR_UUID} "
            f"(or its byte-reversed form {_reverse_uuid(NOTIFY_CHAR_UUID)}) not found "
            "even after clearing the GATT cache and rediscovering. The Bluetooth "
            "proxy may not expose the charger's 128-bit UART service - check that it "
            "runs recent ESPHome with active connections enabled."
        )

    @staticmethod
    def _resolve_char(
        client: BleakClientWithServiceCache, uuid: str
    ) -> BleakGATTCharacteristic | None:
        """Find a characteristic by its UUID or its byte-reversed variant."""
        char = client.services.get_characteristic(uuid)
        if char is None:
            char = client.services.get_characteristic(_reverse_uuid(uuid))
            if char is not None:
                _LOGGER.debug(
                    "Matched %s via byte-reversed UUID %s (ESPHome proxy quirk)",
                    uuid,
                    char.uuid,
                )
        return char

    @staticmethod
    def _describe_services(client: BleakClientWithServiceCache) -> str:
        """One-line dump of the discovered services for the failure log."""
        try:
            described = "; ".join(
                f"{service.uuid}[{','.join(c.uuid for c in service.characteristics)}]"
                for service in client.services
            )
            return described or "(no services)"
        except Exception:  # noqa: BLE001
            return "(unavailable)"

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        notify_char, self._notify_char = self._notify_char, None
        self._write_char = None
        if client is None:
            return
        try:
            if client.is_connected:
                await client.stop_notify(notify_char or NOTIFY_CHAR_UUID)
                await client.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error during disconnect: %s", err)

    @callback
    def _handle_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("EZgo charger %s disconnected", self.address)
        self._client = None
        self._notify_char = None
        self._write_char = None
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
        # Use the characteristic resolved on connect (may have been found under
        # the byte-reversed UUID); fall back to the plain UUID just in case.
        await self._client.write_gatt_char(
            self._write_char or WRITE_CHAR_UUID, frame, response=False
        )

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
