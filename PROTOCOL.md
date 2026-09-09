# MSI / EZgo EV charger — BLE ATT protocol notes

Source capture: `FS/data/misc/bluetooth/logs/btsnoop_hci.log` (Android bugreport btsnoop)
Charger: `b4:0e:cf:53:f1:42`  name `EV_133900095`  ("EFAC32FC11-80611253900095" is the 25‑byte device ID string carried in every frame)
Phone:   `64:9d:38:24:97:8f`  ("Pixel 10 Pro XL")

## tshark extraction

`bluetooth.addr` does **not** resolve for these ATT packets, so filter on the
connection‑tracked address fields instead:

```
tshark -r btsnoop_hci.log \
  -Y "btatt.value and (bthci_acl.src.bd_addr==b4:0e:cf:53:f1:42 or bthci_acl.dst.bd_addr==b4:0e:cf:53:f1:42)" \
  -T fields -e frame.number -e frame.time_relative \
  -e bthci_acl.src.name -e btatt.opcode -e btatt.handle -e btatt.value
```

## GATT layer

| direction        | ATT opcode                | handle  | note                        |
|------------------|---------------------------|---------|-----------------------------|
| phone -> charger | `0x52` Write Command      | `0x0011`| all app commands            |
| charger -> phone | `0x1b` Handle Value Notify | `0x000e`| all charger responses/telemetry |
| phone -> charger | `0x12` Write Request       | `0x0004`, `0x000f` | CCCD enable (notifications) |

Notifications are ~1400‑byte-capable; long frames (type `0x71`/`0x77`) arrive in one notification.

## Application framing

```
+----+-----+----------------+----------+-----+------------------+--------+
|0x68| LEN | 00 00 00 00    | ROUTE(3) | REG | DATA (LEN bytes) | CRC16  |
+----+-----+----------------+----------+-----+------------------+--------+
  0     1        2..5          6..8       9      10 .. 10+LEN-1    last 2
```

* **SOF** = `0x68`
* **LEN** = number of DATA bytes (single byte; long frames still fit, max seen 0x69)
* bytes 2..5 = always `00 00 00 00`
* **ROUTE** (bytes 6..8):
  * `00 00 00` = app -> charger
  * `56 0e 0d` = charger -> app
* **REG** (byte 9) = register / command selector (app) or report type (charger)
* **DATA** = payload, `LEN` bytes. For most messages it begins with the 25‑byte
  ASCII device‑ID string ("EFAC32FC11-80611253900095"); the register `0x16`
  short commands omit it.
* **CRC16** = CRC‑16/MODBUS (poly 0xA001, init 0xFFFF) over **bytes 0 .. end‑of‑DATA**,
  transmitted **big‑endian** (high byte first). Verified on 698/700 frames; the 2
  "failures" are frames where tshark delivered two notifications in one ATT PDU
  (each half CRCs fine on its own).

### Timestamp encoding

6 bytes = `YY MM DD HH MI SS`, each a **plain binary value** (not BCD):
year+2000, month, day, hour, minute, second. Minute byte reaching `0x3b` (59)
before rolling over confirms it is not BCD.

```
1a 09 08 0f 13 29  ->  2026-09-08 15:19:41
26 09 08 15 19 41
```

## App -> charger commands (REG = byte 9)

| REG   | DATA (after device‑ID)        | meaning (inferred)                              |
|-------|-------------------------------|------------------------------------------------|
| `0x04`| `01 71`                       | request config/info report -> charger sends `0x71` |
| `0x04`| `01 77`                       | **poll status** -> charger sends `0x77` (sent every ~3 s) |
| `0x04`| `01 01`                       | request report `0x01` (identify / handshake)   |
| `0x7a`| `<ts6>`                       | set/sync RTC (paired with `0x02`)              |
| `0x02`| `01 <ts6>`                    | set/sync RTC -> charger acks with `0x03`        |
| `0x16`| `10`                          | precedes each settings write — "enter settings / apply" marker. ack `0x15` `10 aa` |
| `0x16`| `12 <A>`                      | **set charge current** — `<A>` = amps, one byte (`08`=8 A, `0a`=10 A). ack `0x15` `12 aa`. Reflected in `0x77` at **+26** |
| `0x16`| `13 <lo> <hi>`                | **set scheduled start** — u16 LE = **minutes from now until the scheduled time** (`72 01` = 370 ≈ 23:15 − 17:04). ack `0x15` `13 aa`. Reflected in `0x77` at **+60** (min) / **+62** (sec) |
| `0x16`| `22`                          | short command (one‑shot, seen once)            |
| `0x72`| `ff ff ff 0d "1.0.02.2.6"…64 00…0a 00` | **write the `0x71` config block** (firmware string, `1234567` code, flag bytes, `64`=100). Does **not** carry amps or schedule. Echoed back as `0x71` |
| `0xf8`| `55 1a 08 1d 1a 09 12`        | schedule/charge command, sub `0x55`, resp `0xf7` `55 00 00 00` |
| `0xf8`| `5a 1a 08 1d 1a 09 12`        | schedule/charge command, sub `0x5a`, resp `0xf7` `aa 00 00 00` |

`0x55`/`0x5a` in `0xf8`, and `aa`/`55` acks elsewhere, look like OK/!OK or
on/off sub‑codes. The `1a 08 1d 1a 09 12` bytes are two 3‑byte values (start/stop
window?) — not yet confirmed.

## Charger -> app report `0x77` (status / telemetry) — DATA after the 25‑byte ID

Length 80 bytes. Offsets are within that 80‑byte block:

| off | bytes         | field                        | notes                                   |
|-----|---------------|------------------------------|-----------------------------------------|
| +0  | `02`          | status/state = 2             | 2 for the whole capture (idle/standby)  |
| +1  | 7×`00`        | —                            |                                         |
| +8  | `9f 00`       | 159 const                    | model / capability constant             |
| +10 | ts6           | **charger RTC "now"**         | tracks wall clock (starts 15:19:41)     |
| +16 | ts6           | RTC now **+ 60 s**            | always exactly ts1 + 1 min              |
| +22 | `00 00`       | —                            |                                         |
| +24 | `0a 00`       | 10 const                     | rated/max current (10 A model); unchanged when setpoint changed |
| +26 | u16 LE        | **configured charge current (A)** | `08`→`0a` the instant a `16/12 0a` write lands. = the `0x16/0x12` value |
| +28 | `00 00 00 00` | —                            |                                         |
| +32 | `78 00`       | 120 const                    | setting (target SOC / nominal V?)       |
| +34 | u16 LE /10    | **AC input voltage (V)**      | 231.0–234.7, varies realistically       |
| +36 | 8×`00`        | current / power / energy     | all zero — not charging in this capture |
| +44 | u16 LE        | **temp sensor 1 (°F)**        | ~69                                     |
| +46 | u16 LE        | **temp sensor 2 (°F)**        | 69 -> 73 over 45 min                     |
| +48 | u16 LE        | **temp sensor 3 (°F)**        | 73 -> 75                                |
| +50 | u16 LE        | **temp sensor 4 (°F)**        | 73 -> 75                                |
| +52 | ts6           | **fixed event time** 15:19:41| constant all capture — plug‑in/session start |
| +58 | `00 00`       | —                            |                                         |
| +60 | u16 LE        | **minutes until scheduled charge start** | set verbatim from the `0x16/0x13` write (`72 01` = 370). Was a stale `0b 00` before the app ever wrote a schedule this session |
| +62 | u16 LE        | **seconds until scheduled charge start** | = +60 × 60 at write time (22199), then −1/s live. Reaches 0 → charge begins |
| +64 | 12×`00`       | —                            |                                         |
| +76 | u16 LE        | 1 or 2, toggles              | heartbeat / link counter                |
| +78 | u16 LE        | 1 (occasionally 2–3)         | sub‑status / phase                      |

## Charger -> app other reports

| type  | DATA (after ID)                | meaning                                   |
|-------|--------------------------------|-------------------------------------------|
| `0x01`| `31 31 0e 31 00`               | identify/handshake response               |
| `0x03`| `<REG> 02 00 aa 00 00 00 01`   | **generic ACK**, echoes the REG acked (`0x7a`,`0x72`,…; REG `0x02` -> `03`). `aa` = OK |
| `0x15`| `<sub> aa`                     | ack of `0x16` command                     |
| `0x71`| `ff ff ff 0d "1.0.02.2.6" 14 07 10 14 14 14 "1234567" 00 … 64 00 … 0a 00` | config/info block. firmware `1.0.02.2.6`, build `14 07 10`, code `1234567`, `64`=100 |
| `0xf7`| `<sub> 00 00 00`               | ack of `0x f8` command                    |

## Capture 2 comparison (amperage -> 10 A, schedule -> 16:00 set via front buttons)

`my_ezgo_capture2/…/btsnoop_hci.log` is the **same btsnoop ring buffer** as
capture 1, extended: identical frames for t = 311–2906 s, then ~2900 s more
(t up to 5820, RTC 16:03 → 16:51).

**The settings change is not visible anywhere in capture 2's BLE traffic:**

* Message‑type counts are identical except for the extra status polls: `0x71` ×12,
  `0x72` ×4, `0xf8` ×8, `0x16` ×7, `0x01` ×3 — all in the shared t < 2906 region.
  The only new app traffic after t = 2910 is status polls (`04 / 01 77`) plus a
  single RTC sync (`0x7a`+`0x02`) at t = 5529.
* Every `0x71` / `0x72` config block is byte‑for‑byte identical to capture 1
  (they are literally the same packets).
* The `0x77` status payload is structurally unchanged. `(state,+24,+26,+32,+60,+78)`
  = `(2, 10, 8, 120, 11, {1..3})` in **both** captures. Only the expected
  time‑varying bytes differ: RTC (+10/+16), AC voltage (+34, 233–236 V),
  temps (+46/+48/+50 drift +1–2 °F), the +62 counter (keeps ticking −1/s with
  **no discontinuity** at the settings change), and the +76 heartbeat.

Conclusions (button change): the change never crossed the link — the app doesn't
push settings after a physical-button edit, and the charger doesn't volunteer
them. Capture 3 (edit made *in the app*) shows where they actually go.

## Capture 3 — amperage 8 A → 10 A and schedule → 23:15, changed *in the app*

Same ring buffer again, extended to t ≈ 6826 (RTC 17:08). When the user hit "save"
in the app's settings screen (RTC ≈ 17:03–17:04) the app:

1. read `0x71` (`04 / 01 71`) on opening the screen — block came back **unchanged**,
   confirming `0x71`/`0x72` do **not** hold amps or schedule;
2. wrote each setting over register **`0x16`**:

```
fr 7960  APP 0x16  10          "enter settings"
fr 7964  APP 0x16  12 0a       set charge current = 0x0a = 10 A     <-- AMPERAGE
fr 8047  APP 0x16  10
fr 8051  APP 0x16  13 72 01    set schedule delay = 0x0172 = 370 min  <-- SCHEDULE
```

* **Amperage** — `0x16` sub `0x12`, value = **one byte of amps**. Was `12 08` in
  captures 1–2, now `12 0a`. The `0x77` field at **+26** flips `08 00`→`0a 00` on
  the very next status frame (fr 7975).
* **Schedule** — `0x16` sub `0x13`, value = **u16 LE = minutes from "now" until the
  scheduled start**, not a wall-clock time. `72 01` = 370; RTC at the write was
  17:04, 23:15 − 17:04 = 6 h 11 min ≈ 370. Right after the write the `0x77`
  payload changes at **+60..+63**: `+60/+61` = `72 01` (= 370, the value echoed
  verbatim, in minutes) and `+62/+63` restarts at **22199 s** (= 370 × 60) and
  counts down 1/s toward charge start.

So the earlier "`+60` = 11 const" / "`+62` = mystery countdown" were a stale value
and a leftover countdown from a schedule set before capture 1; they are really the
scheduled-charge delay in minutes / seconds, and they get written together from
the `0x16/0x13` command.

`+24` stayed `0a` (10) throughout — it's the model's rated current, not the
setpoint.

## Correction to earlier finding

The `1a 09` → `0x091a` = 2330 → "233.0 V" match was a **false positive**: `1a 09`
is the first two bytes of the 6‑byte timestamp (`year=0x1a=26`, `month=0x09`),
which appears 3× per status frame. The real AC‑voltage field is at **+34** of the
`0x77` DATA block (`21 09` = `0x0921` = 2337 → 233.7 V) and it varies frame‑to‑frame
between ~231 and ~235 V like a real mains reading.
