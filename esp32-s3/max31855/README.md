# MAX31855 variant — 4x K-type thermocouple on an ESP32-S3

This is the ESP32-S3 port of the original sketch in
[`module/`](../../module): same pins (`SCK 14`, `MISO 12`, `CS 15/16/17/18`),
same `read_temperature()`, same `ms,f1,f2,f3,f4` line on USB serial — plus
Wi-Fi and a direct upload to the API.

```
ESP32-S3 ──SPI──┬── MAX31855 #1 (CS 15) ── K-type probe 1
                ├── MAX31855 #2 (CS 16) ── K-type probe 2
                ├── MAX31855 #3 (CS 17) ── K-type probe 3
                └── MAX31855 #4 (CS 18) ── K-type probe 4
        └── Wi-Fi ── POST /api/ingest ── dashboard
```

## Wiring

The four amplifiers share one SPI bus. **The MAX31855 is read-only: it has no
MOSI/SDI pin**, so GPIO11 stays free on this variant.

| ESP32-S3 pin | Signal | MAX31855 board (all four)      |
| ------------ | ------ | ------------------------------ |
| 3V3          | 3V3    | `VIN` / `VCC` / `3V3`          |
| GND          | GND    | `GND`                          |
| GPIO14       | SCK    | `CLK` / `SCK`                  |
| GPIO12       | MISO   | `DO` / `SO` / `MISO`           |
| GPIO15       | CS     | `CS` of furnace 1              |
| GPIO16       | CS     | `CS` of furnace 2              |
| GPIO17       | CS     | `CS` of furnace 3              |
| GPIO18       | CS     | `CS` of furnace 4              |
| —            | —      | `DO` of every board joins GPIO12 |

Per board, one probe: **K-type thermocouple, `T+` to `T+` and `T-` to `T-`.**
Reversed polarity reads a falling temperature when the furnace heats up.

- Power the boards from **3V3**, not 5V: the MAX31855 logic pins are not 5V
  tolerant on every breakout, and the ESP32-S3 is a 3.3 V part.
- Keep the SPI wires short (under ~20 cm) and run them away from the heating
  elements; a 1 MHz clock is slow enough that wiring is rarely the problem.
- Use a separate twisted pair per probe, grounded at one end only, to avoid a
  ground loop through the thermocouple sheath.
- GPIO15–GPIO18 are free on a bare ESP32-S3 module; if your board wires them to
  flash/PSRAM or to an RGB LED, move `CS_PINS` in `main.py`.

## Install

```bash
cp ../common/config.example.py /tmp/config.py      # edit Wi-Fi + API settings
mpremote connect COM6 \
  cp /tmp/config.py :config.py + \
  cp ../common/wifi.py :wifi.py + \
  cp ../common/ingest.py :ingest.py + \
  cp main.py :main.py
mpremote connect COM6 repl                         # Ctrl-] to leave
```

The console should show:

```
Wi-Fi: connected, ip=192.168.1.42 gateway=192.168.1.1
NTP: clock set to 2026-10-25 06:12:04 UTC
ms,furnace1,furnace2,furnace3,furnace4
312,781.25,660.00,940.00,545.75
413,781.50,660.25,940.25,545.50
```

## How it works

- `read_channel(cs)` performs the same 4-byte SPI read as the original
  `read_temperature(cs)` and additionally decodes the fault bits the API
  expects:

  | Raw flag | API bit | Meaning           |
  | -------- | ------- | ----------------- |
  | `0x1`    | 1       | open thermocouple |
  | `0x2`    | 2       | short to GND      |
  | `0x4`    | 4       | short to VCC      |
  | `0x10000`| 8       | general fault     |

  `read_temperature()` is kept verbatim for reference and returns `nan` like
  before; the loop uses `read_channel()` so a fault becomes a JSON `null`
  instead of `NaN` (which is not valid JSON).
- The signed 14-bit value is scaled by 0.25 °C, the MAX31855 resolution.
- One frame per 100 ms, batched as described in the
  [ESP32-S3 README](../README.md#behaviour).

## Accuracy notes

- The MAX31855 measures the *cold junction* internally; its accuracy is about
  ±2 °C for the K range and it does not linearise beyond its table. If you need
  ±0.5 °C, use the MAX31856 (same pins, SPI writer) or the RTD variant in
  [`../max31865/`](../max31865/README.md).
- The board reports the temperature of the internal cold junction in the same
  frame; the API stores only the thermocouple temperature, which is what the
  furnace cares about.
- Grounded thermocouples in a mains-powered furnace can inject noise. A
  floating (ungrounded) junction, or an isolated probe amplifier, removes most
  of it.

## Troubleshooting

| Symptom | Cause |
| ------- | ----- |
| All channels `-1000` / `--.-` on the dashboard | No SPI traffic: wrong `MISO` pin, missing 3V3/GND, or two boards driving one CS line. |
| One channel `-1000`, others fine | That probe: broken welded tip, reversed `T+`/`T-`, or a CS pin used twice. |
| `f` = 1 (open) in the CSV | Open thermocouple or a probe with an internal break — check with a multimeter (it should read close to 0 Ω). |
| Temperature drops when the furnace heats | Reversed thermocouple polarity. |
| Reading is constant 0 °C | `CS` stuck low or MISO shared with another device; check the soldered header. |
| Data goes stale on the dashboard | Wi-Fi dropped; the console prints retries and the buffer holds the last `BUFFER_LIMIT` samples. |
