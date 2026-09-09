# MSI EZgo EV Charger — Home Assistant integration

Local Bluetooth LE control and monitoring for the **MSI EZgo 10A + 15A IP66 EV
Portable Charger** (BLE name `EV_*`, e.g. `EV_133900095`).

The protocol was reverse-engineered from Android `btsnoop_hci.log` captures — see
[`PROTOCOL.md`](./PROTOCOL.md). It talks to the charger's Microchip/ISSC
"Transparent UART" GATT service using Home Assistant's native `bluetooth`
stack, so it works **transparently through an ESPHome Bluetooth Proxy** — the
charger does not need to be in range of the HA host.

> ⚠️ Reverse-engineered and only partially confirmed. Writing settings has been
> validated against the vendor app's own traffic; the live current/power/energy
> sensors are placeholders (offsets unknown until a charging session is
> captured). Use at your own risk.

## Entities

| Entity | Type | Source | Notes |
|---|---|---|---|
| `number.ezgo_charger_charge_current` | number (6–15 A) | write `0x16`/`0x12`, 1 byte amps | reads back from `0x77` +26 |
| `time.ezgo_charger_scheduled_start` | time | write `0x16`/`0x13`, u16 LE **minutes-from-now** | you pick a clock time; the component converts it. If the time has already passed today it schedules for tomorrow. Reads back from the charger's live countdown (`0x77` +62) |
| `sensor.ezgo_charger_voltage` | sensor (V) | `0x77` +34, u16 LE ÷10 | AC input voltage |
| `sensor.ezgo_charger_temperature_1..4` | sensor (°F) | `0x77` +44/+46/+48/+50, u16 LE | already Fahrenheit |
| `sensor.ezgo_charger_state` | sensor | `0x77` +0 | `standby` (2) confirmed; others show as `unknown_<n>`, raw value in the `raw` attribute |
| `sensor.ezgo_charger_scheduled_start_in` | sensor (min) | `0x77` +62 | live countdown to scheduled charge start |
| `sensor.ezgo_charger_charging_current` | sensor (A) | `0x77` +36..+43 (**unconfirmed**) | **placeholder**, disabled by default, reports unknown |
| `sensor.ezgo_charger_charging_power` | sensor (W) | ″ | placeholder, disabled by default |
| `sensor.ezgo_charger_session_energy` | sensor (kWh) | ″ | placeholder, disabled by default |

The integration also does a best-effort RTC sync on connect (mirrors the vendor
app) so the charger's own clock/logs stay right.

## Install (HACS custom repository)

1. HACS → **⋮** (top right) → **Custom repositories**.
2. Repository: the URL of *this* repo. Category: **Integration**. Add.
3. Find **“MSI EZgo EV Charger”** in HACS, **Download**.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration → “MSI EZgo EV Charger”.**
   The MAC address `B4:0E:CF:53:F1:42` is pre-filled — just submit. (If your
   charger is a different unit, put its MAC here; you can find it in the vendor
   app or from `bluetoothctl`/an nRF scan — the name starts with `EV_`.)

If HA's Bluetooth stack has already seen the charger it will also be
auto-discovered and offered directly.

### Manual install (no HACS)

Copy `custom_components/ezgo_charger/` into your HA `config/custom_components/`
directory and restart.

## ESPHome Bluetooth Proxy config

The charger is usually parked in a garage/carport out of range of the HA host.
Flash a cheap ESP32 near it as an **active** Bluetooth proxy. Active mode is
required — this integration *connects and writes*, not just passive scanning.

```yaml
# bt-proxy-ezgo.yaml
esphome:
  name: bt-proxy-ezgo

esp32:
  board: esp32dev
  framework:
    type: esp-idf        # esp-idf handles multiple BLE connections better than Arduino

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_encryption_key
ota:
  platform: esphome
logger:

# The important bit
bluetooth_proxy:
  active: true

esp32_ble_tracker:
  scan_parameters:
    interval: 1100ms
    window: 1100ms
    active: true         # request scan responses (gets the EV_* name)
```

Then in Home Assistant: **Settings → Devices & Services**, the ESPHome device
will be discovered; adopt it and enable its Bluetooth proxy when prompted.

Notes:

* An ESP32 supports **3 simultaneous** BLE connections total. Keep the proxy for
  this charger (plus maybe one or two other BLE devices) and it's fine.
* Put the proxy within a few metres of the charger — IP66 enclosures and garage
  walls eat signal.
* Use a plain ESP32 (dual-core). ESP32-C3/S2 single-core work but are tighter.

## Caveats

* **One connection at a time.** The charger accepts a single BLE client. While
  Home Assistant is connected, the phone app can't connect, and vice-versa. HA
  holds the connection open; “forget”/disable the integration to hand it back.
* **Schedule is a delay, not a clock time.** The charger is told “start in N
  minutes”, computed by the app/this integration from your picked time. It does
  not re-derive it from its own RTC, so if the charger reboots or you reconnect
  much later the effective start time can drift. Re-set it if that matters.
* **Physical-button changes are invisible.** If you change amps/schedule with the
  charger's own buttons, nothing is pushed over BLE and the entities won't
  update until the charger reports it in `0x77` (amps do show up there; the
  schedule only updates when written via BLE).
* **`6–15 A` range** on the number entity is a guess from the “10A + 15A” model
  name plus observed values (8, 10). Adjust `CURRENT_MIN`/`CURRENT_MAX` in
  `const.py` if your unit differs.

## Development

```bash
python tests/test_protocol.py      # frame build / CRC / status parse vs. real capture bytes
# or: pytest
```

`PROTOCOL.md` is the source of truth for offsets and command formats. If you
capture a real charging session, confirm the `0x77` +36..+43 layout and wire it
into `parse_status()` / un-disable the placeholder sensors.

## File layout

```
custom_components/ezgo_charger/
  __init__.py        entry setup / teardown
  manifest.json
  const.py           UUIDs, offsets, defaults
  protocol.py        framing, CRC-16/MODBUS, 0x77 parser  (no HA deps)
  coordinator.py     BLE connection + notification reassembly + 0x77 poll loop
  config_flow.py     Bluetooth discovery + manual MAC entry
  entity.py          shared CoordinatorEntity base + DeviceInfo
  number.py          charge current
  time.py            scheduled start
  sensor.py          voltage / temps / state / countdown / placeholders
  strings.json, translations/en.json
tests/test_protocol.py
```
