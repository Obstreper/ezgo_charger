"""Constants for the MSI EZgo EV charger integration.

Protocol details live in PROTOCOL.md (reverse engineered from btsnoop captures).
"""

from __future__ import annotations

DOMAIN = "ezgo_charger"

# We already know the charger address from the capture work; it is offered as the
# default in the config flow so the user normally just clicks through.
DEFAULT_ADDRESS = "B4:0E:CF:53:F1:42"
DEFAULT_NAME = "EZgo Charger"

# ---------------------------------------------------------------------------
# GATT
# ---------------------------------------------------------------------------
# The charger exposes a Microchip / ISSC "Transparent UART" style service.
# UUIDs were pulled from the GATT discovery in the capture (handles 0x0011 write,
# 0x000e notify). We address characteristics by UUID so this keeps working
# through an ESPHome Bluetooth proxy (handles are not stable across backends).
SERVICE_UUID = "55535343-fe7d-4ae5-8fa9-9fafd205e455"
WRITE_CHAR_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"   # app  -> charger (Write Command / no response)
NOTIFY_CHAR_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3"  # charger -> app (notifications)

# ---------------------------------------------------------------------------
# Application protocol
# ---------------------------------------------------------------------------
SOF = 0x68

REG_POLL = 0x04   # data: <25b id> 01 77  (status) / 01 71 (config) / 01 01 (id)
REG_CMD = 0x16    # settings writes: 10 / 12 <amps> / 13 <lo> <hi>
REG_RTC_A = 0x7A  # data: <25b id> <ts6>
REG_RTC_B = 0x02  # data: <25b id> 01 <ts6>

STATUS_REG = 0x77

CMD_ENTER = 0x10          # "enter settings" marker sent before each setting write
CMD_SET_CURRENT = 0x12    # value: one byte, amps
CMD_SET_SCHEDULE = 0x13   # value: u16 LE, minutes from now until scheduled start

# 25-byte ASCII device id carried in most frames. Auto-learned from the first
# status notification; this is only the bootstrap default.
DEFAULT_DEVICE_ID = b"EFAC32FC11-80611253900095"
ZERO_DEVICE_ID = b"\x00" * 25

# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------
POLL_INTERVAL = 10          # seconds between 0x77 status polls
NOTIFY_SETTLE = 3.0         # max seconds to wait for the notification after a poll
WRITE_GAP = 0.2             # seconds between the 0x10 marker and the setting
MAX_BUFFER = 4096           # reassembly buffer hard cap

CURRENT_MIN = 6
CURRENT_MAX = 15

# The 0x77 status field at +26 is the setpoint; +24 is a constant that reads 10
# in every capture and is believed to be the rating of the installed current-limit
# pigtail (the aConnect app clamps selectable amperage to it: 8/10 A on the 10 A
# tail). Only the 10 A tail has been captured - 15 A is inferred from the hardware
# options. Values outside this set trigger a warning and fall back to the lowest
# known rating.
KNOWN_RATED_CURRENTS = (10, 15)

# Human-readable names for 0x77 offset +0. Only "2" (idle/standby) is confirmed;
# extend once charging / fault states have been captured.
STATE_NAMES: dict[int, str] = {
    2: "standby",
}
