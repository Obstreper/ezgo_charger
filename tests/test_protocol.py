"""Protocol round-trip checks against real bytes from the btsnoop captures.

Run standalone:  python tests/test_protocol.py
Or with pytest:  pytest
"""

import importlib
import os
import sys
import types

# Load custom_components/ezgo_charger/{const,protocol}.py without executing the
# component's __init__.py (which pulls in Home Assistant).
_PKG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "ezgo_charger")
)
_pkg = types.ModuleType("ezgo_charger")
_pkg.__path__ = [_PKG_DIR]
sys.modules.setdefault("ezgo_charger", _pkg)

protocol = importlib.import_module("ezgo_charger.protocol")
build_frame = protocol.build_frame
crc16_modbus = protocol.crc16_modbus
parse_frames = protocol.parse_frames
parse_status = protocol.parse_status

DEVICE_ID = b"EFAC32FC11-80611253900095"

# The status poll frame exactly as seen on the wire (capture 1, frame 788).
POLL_FRAME = (
    "681b0000000000000004454641433332464331312d3830363131323533393030303935"
    "017757e2"
)

# A full 0x77 status notification (capture 1, frame 794).
STATUS_FRAME = bytes.fromhex(
    "686900000000560e0d77454641433332464331312d38303631313235333930303039350"
    "2000000000000009f001a09080f13291a09080f142900000a00080000000000780021090"
    "00000000000000045004500490049001a09080f132900000b000c970000000000000000"
    "0000000002000100583d"
)


def _crc_ok(frame: bytes) -> bool:
    return crc16_modbus(frame[:-2]) == (frame[-2] << 8 | frame[-1])


def test_crc_matches_capture():
    assert _crc_ok(bytes.fromhex(POLL_FRAME))


def test_build_poll_frame_is_byte_identical():
    built = build_frame(0x04, DEVICE_ID + bytes.fromhex("0177"))
    assert built.hex() == POLL_FRAME


def test_build_set_current():
    built = build_frame(0x16, bytes.fromhex("120a"))
    assert built[:11].hex() == "6802000000000000001612"
    assert built[11] == 0x0A
    assert _crc_ok(built)


def test_build_enter_marker():
    built = build_frame(0x16, b"\x10")
    assert built[:11].hex() == "6801000000000000001610"
    assert _crc_ok(built)


def test_build_set_schedule_370_minutes():
    built = build_frame(0x16, b"\x13" + (370).to_bytes(2, "little"))
    assert built[:13].hex() == "68030000000000000016137201"
    assert _crc_ok(built)


def test_parse_status_decodes_known_values():
    frames = parse_frames(bytearray(STATUS_FRAME))
    assert len(frames) == 1
    reg, data = frames[0]
    assert reg == 0x77
    status = parse_status(data)
    assert status["device_id"] == "EFAC32FC11-80611253900095"
    assert status["state"] == 2
    assert status["state_name"] == "standby"
    assert status["voltage"] == 233.7
    assert status["rated_current"] == 10
    assert status["charge_current"] == 8
    assert [status[f"temp_{i}"] for i in (1, 2, 3, 4)] == [69, 69, 73, 73]


def test_parse_frames_handles_concatenated_notifications():
    buf = bytearray(STATUS_FRAME + STATUS_FRAME)
    assert len(parse_frames(buf)) == 2
    assert len(buf) == 0


def test_parse_frames_resyncs_past_garbage():
    buf = bytearray(b"\xff\x00\x11" + STATUS_FRAME)
    assert len(parse_frames(buf)) == 1


def test_parse_frames_keeps_partial_frame():
    buf = bytearray(STATUS_FRAME[:20])
    assert parse_frames(buf) == []
    assert len(buf) == 20  # nothing consumed, waiting for the rest


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS {_name}")
                passed += 1
            except Exception:  # noqa: BLE001
                print(f"FAIL {_name}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
