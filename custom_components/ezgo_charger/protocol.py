"""Framing / parsing for the EZgo charger BLE protocol.

Frame layout (see PROTOCOL.md):

    68 | LEN | 00 00 00 00 | ROUTE(3) | REG | DATA[LEN] | CRC16

    * LEN    = number of DATA bytes
    * ROUTE  = 00 00 00 (app->charger)  /  56 0e 0d (charger->app)
    * REG    = register / report selector
    * CRC16  = CRC-16/MODBUS over bytes [0 .. end of DATA], big-endian trailer
"""

from __future__ import annotations

from .const import SOF, STATE_NAMES

_HEADER_LEN = 10   # SOF, LEN, 4x00, ROUTE(3), REG
_CRC_LEN = 2
_MIN_FRAME = _HEADER_LEN + _CRC_LEN


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(reg: int, data: bytes) -> bytes:
    """Build an app->charger frame with the big-endian CRC trailer."""
    if len(data) > 0xFF:
        raise ValueError("EZgo frame DATA may not exceed 255 bytes")
    body = bytes((SOF, len(data), 0, 0, 0, 0, 0, 0, 0, reg)) + data
    crc = crc16_modbus(body)
    return body + bytes(((crc >> 8) & 0xFF, crc & 0xFF))


def parse_frames(buffer: bytearray) -> list[tuple[int, bytes]]:
    """Pull every complete, CRC-valid frame out of *buffer* (mutated in place).

    Returns a list of ``(reg, data)`` tuples. Handles notifications that carry
    several frames back to back, and re-syncs past garbage.
    """
    out: list[tuple[int, bytes]] = []
    while len(buffer) >= _MIN_FRAME:
        if buffer[0] != SOF:
            del buffer[0]
            continue
        length = buffer[1]
        total = _HEADER_LEN + length + _CRC_LEN
        if len(buffer) < total:
            break
        frame = bytes(buffer[:total])
        trailer = frame[-2] << 8 | frame[-1]
        if crc16_modbus(frame[:-2]) == trailer:
            out.append((frame[9], frame[_HEADER_LEN:-_CRC_LEN]))
            del buffer[:total]
        else:
            # bad CRC - drop one byte and try to re-sync
            del buffer[0]
    return out


def _u16(block: bytes, offset: int) -> int:
    return int.from_bytes(block[offset : offset + 2], "little")


def state_name(value: int | None) -> str | None:
    if value is None:
        return None
    return STATE_NAMES.get(value, f"unknown_{value}")


def parse_status(data: bytes) -> dict:
    """Decode the DATA of a 0x77 status frame.

    ``data`` = 25-byte ASCII device id + 80-byte status block. Offsets below are
    into the status block. See PROTOCOL.md for the full table.
    """
    if len(data) < 25 + 64:
        return {}

    device_id = data[:25]
    s = data[25:]

    result: dict = {
        "device_id_bytes": device_id,
        "device_id": device_id.decode("ascii", "replace").rstrip("\x00"),
        "state": s[0],
        "state_name": state_name(s[0]),
        "rated_current": _u16(s, 24),        # +24  model rating (10 for 10A unit)
        "charge_current": _u16(s, 26),       # +26  configured setpoint (A)
        "voltage": _u16(s, 34) / 10.0,       # +34  AC input voltage
        "temp_1": _u16(s, 44),               # +44  already in degF
        "temp_2": _u16(s, 46),
        "temp_3": _u16(s, 48),
        "temp_4": _u16(s, 50),
        "schedule_minutes": _u16(s, 60),     # +60  minutes until scheduled start
        "schedule_seconds": _u16(s, 62),     # +62  seconds until start (live -1/s)
        "heartbeat": _u16(s, 76),            # +76
        "substatus": _u16(s, 78),            # +78
    }

    # -----------------------------------------------------------------
    # Placeholders. 0x77 +36..+43 (8 bytes) is believed to carry the live
    # charging current / power / energy but every capture so far was idle, so the
    # split is unconfirmed. Keep the entities but report unavailable until a real
    # charging session is captured and PROTOCOL.md is updated.
    # -----------------------------------------------------------------
    result["live_current"] = None
    result["live_power"] = None
    result["live_energy"] = None

    return result
