# ESP32 Arduino (C++) firmware — Wi-Fi straight to the API

The Arduino/C++ twin of the MicroPython firmware in [`../`](../README.md). Same
job, same pins, same CSV line on USB serial, same `POST /api/ingest` payload —
written as one `.ino` sketch for the **Arduino IDE 2.x** or **arduino-cli**, with
no library to install beyond the ESP32 core.

```
   ESP32 / ESP32-S3 (Wi-Fi)                 API                    Browser
 ┌──────────────────────┐   POST    ┌──────────────────┐   GET   ┌──────────┐
 │ 4 x sensor @ 10 Hz   │ ────────▶ │ /api/ingest      │ ──────▶ │  ui/     │
 │ ring buffer → JSON   │  X-Api-Key│ SQLite + runs    │  JSON   │ dashboard│
 └──────────────────────┘           └──────────────────┘         └──────────┘
```

There is no PC in the middle: no `collector/`, no USB cable, no file to upload.
Works on an **ESP32-S3** (verified for `esp32:esp32:esp32s3`) and on a plain
**ESP32** (`esp32:esp32:esp32`); the pins below are valid on both.

## Files

```
esp32-s3/arduino/
├── README.md                     this file
├── furnace_arduino/              the sketch (open this folder in the IDE)
│   ├── furnace_arduino.ino       setup/loop: 10 Hz, CSV echo, Wi-Fi, upload
│   ├── config.example.h          copy to config.h and edit
│   ├── furnace_math.h            MAX31855/MAX31865 decoding + RTD curve
│   ├── furnace_payload.h         JSON body builder (no JSON library)
│   ├── furnace_net.h             Wi-Fi association, reconnect, NTP
│   ├── furnace_ingest.h          ring buffer + HTTP(S) POST
│   ├── max31855_sensor.h         thermocouple driver (read-only SPI)
│   ├── max31865_sensor.h         RTD driver (register SPI, mode 1)
│   └── README.md                 file-by-file notes
└── tests/
    ├── test_math.cpp             host tests for the pure logic
    └── run.sh                    g++ -std=c++17, no board needed
```

## Install

### 1. ESP32 core

Arduino IDE: **Boards Manager** → install **esp32 by Espressif Systems**
(3.3.x was used for the verified build). Board: **ESP32S3 Dev Module** for an
ESP32-S3, **ESP32 Dev Module** for a classic ESP32.

`arduino-cli` equivalent:

```bash
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
```

No other library is needed: SPI, Wi-Fi and HTTPClient ship with the core.

### 2. Create `config.h`

```bash
cp esp32-s3/arduino/furnace_arduino/config.example.h \
   esp32-s3/arduino/furnace_arduino/config.h
```

Edit `WIFI_SSID`, `WIFI_PASSWORD`, `API_URL` and `API_KEY`. Uncomment

```c
#define FURNACE_USE_MAX31865
```

for the RTD board; leave it commented for MAX31855 thermocouples. `config.h` is
git-ignored, so credentials never reach the repository. Without it the sketch
stops with a clear `#error` instead of building a board that cannot connect.

### 3. Build and upload

```bash
# ESP32-S3, port on Windows is COM6 for the CH343 bridge
arduino-cli compile --fqbn esp32:esp32:esp32s3 esp32-s3/arduino/furnace_arduino
arduino-cli upload -p COM6 --fqbn esp32:esp32:esp32s3 esp32-s3/arduino/furnace_arduino

# classic ESP32
arduino-cli compile --fqbn esp32:esp32:esp32 esp32-s3/arduino/furnace_arduino
```

In the IDE: open `furnace_arduino/furnace_arduino.ino`, choose the board and
port, press **Upload**, then open **Serial Monitor** at 115200 baud.

Expected console:

```
Furnace monitor: 4 channels, direct to /api/ingest
Wi-Fi: connected, ip=192.168.1.42 gateway=192.168.1.1
NTP: clock set to 2026-10-25 06:12:04 UTC
ms,furnace1,furnace2,furnace3,furnace4
312,781.25,660.00,940.00,545.75
413,781.50,660.25,940.25,545.50
```

Two build notes:

- The verified build uses **1 015 617 bytes of flash (77 %)** and **73 968 bytes
  of RAM (22 %)** on an ESP32-S3, most of it the TLS stack. The default
  partition scheme fits, but if a future change overflows, pick
  **Huge APP (3 MB No OTA)** in *Tools → Partition Scheme*.
- On a board with **native USB** (no CH343 chip), set *Tools → USB CDC On Boot:
  Enabled*, otherwise `Serial` output goes to the UART pins and the monitor
  stays empty. With a CH343/CP210x bridge leave it disabled.

## Pins

Every pin can be overridden in `config.h`; the defaults are:

### MAX31855 (thermocouple)

| ESP32 / ESP32-S3 | Signal | MAX31855 board (all four) |
| ---------------- | ------ | ------------------------- |
| 3V3              | 3V3    | `VIN` / `VCC` / `3V3`     |
| GND              | GND    | `GND`                     |
| **GPIO14**       | SCK    | `CLK` / `SCK`             |
| **GPIO12**       | MISO   | `DO` / `SO` / `MISO`      |
| **GPIO15**       | CS     | `CS` of furnace 1         |
| **GPIO16**       | CS     | `CS` of furnace 2         |
| **GPIO17**       | CS     | `CS` of furnace 3         |
| **GPIO18**       | CS     | `CS` of furnace 4         |
| —                | —      | `DO` of every board joins GPIO12 |

The MAX31855 has no MOSI/SDI pin, so GPIO11 stays free.

### MAX31865 (PT100 / PT1000 RTD)

| ESP32 / ESP32-S3 | Signal        | MAX31865 board (all four) |
| ---------------- | ------------- | ------------------------- |
| 3V3              | 3V3           | `VIN`                     |
| GND              | GND           | `GND`                     |
| **GPIO14**       | SCK (clock)   | `CLK`                     |
| **GPIO11**       | **MOSI** (SDI)| `SDI`                     |
| **GPIO12**       | MISO (SDO)    | `SDO`                     |
| **GPIO15**       | CS            | `CS` of furnace 1         |
| **GPIO16**       | CS            | `CS` of furnace 2         |
| **GPIO17**       | CS            | `CS` of furnace 3         |
| **GPIO18**       | CS            | `CS` of furnace 4         |
| —                | IRQ (optional)| `DRDY` — leave unconnected |

The driver talks to the MAX31865 in **SPI mode 1** (CPOL = 0, CPHA = 1), the
mode the datasheet and the reference library use, at 1 MHz. RTD settings live in
`config.h`:

| Probe  | `MAX31865_RREF` | `MAX31865_RNOMINAL` | Board jumper      |
| ------ | --------------- | ------------------- | ----------------- |
| PT100  | `430.0`         | `100.0`             | 2/3-wire default  |
| PT1000 | `4300.0`        | `1000.0`            | 2/3-wire default  |

`MAX31865_WIRE_MODE` is `2`, `3` or `4` and must match how the probe is wired;
`MAX31865_FILTER_HZ = 50` keeps the mains notch matched to Iran's 50 Hz grid.
Wiring detail for 2/3/4-wire probes is in
[`../../max31865/README.md`](../../max31865/README.md#probe-wiring-on-the-max31865-board).

## Behaviour

- **10 Hz grid.** Each pass reads the four channels, prints `ms,f1..f4`, queues
  the frame and sleeps until the next 100 ms slot. The time column is measured
  with `millis()`, so a slow upload cannot make it drift; if an upload takes
  longer than a frame the loop resynchronises instead of burning through missed
  slots, and that gap shows up in the data.
- **Batching.** `BATCH_SIZE` (default 20 = 2 s) frames per request. The API
  anchors a batch without `at` on arrival and places the rest on the 0.1 s grid,
  so the dashboard timer (which counts samples) is unaffected.
- **Timestamps.** With NTP the board sends `at` (Unix ms) per sample, so history
  stays correct across reboots and outages. Without a clock the key is omitted.
- **Outages.** A failed upload keeps the batch and retries at the next batch; a
  `400/413/422` is a refused payload and is dropped; `401/403` and a wrong URL
  are retried with a console hint. Beyond `BUFFER_LIMIT` the oldest samples are
  dropped, so RAM cannot fill up. The queue costs ~40 bytes per sample.
- **Wi-Fi.** A dropped link is re-associated in the background (no blocking
  wait), so sampling never stops; if the clock was never set it is fetched when
  the link comes back.
- **One board per run.** The dashboard timer counts stored samples as 0.1 s
  each, so two boards streaming into the same run make it count twice as fast.
- **Console.** The CSV line is identical to `module/` and the MicroPython
  firmware, so Thonny, Serial Studio and
  [`serial-web/`](../../../serial-web/README.md) work while the board uploads.
  Set `SERIAL_ECHO 0` to silence it.

The payload is the same the Python collector sent, with faults as `null`:

```json
{"samples":[{"u":1200,"at":1792523131000,"t":[781.25,null,940.00,545.75],"f":[0,1,0,0]}]}
```

## Tests

The maths and the JSON body are plain C++ with no Arduino dependency, so they
are tested on the PC:

```bash
bash esp32-s3/arduino/tests/run.sh
```

It compiles `test_math.cpp` with `g++ -std=c++17 -Wall -Wextra -Werror` and
checks:

- MAX31855 frames: 781.25 °C, −100.25 °C, the datasheet's 25 °C frame, and the
  four fault flags (1/2/4/8);
- two-decimal formatting without `printf("%f")` (`781.25`, `-1000.00`, `0.00`);
- the exact JSON body, including `null` for a fault and no `at` without a clock,
  plus a worst-case 20-sample batch fitting the sketch's buffer;
- the RTD curve against IEC 60751 (`100 Ω → 0 °C`, `138.5055 Ω → 100 °C`,
  `60.256 Ω → −100 °C`, `18.52 Ω → −200 °C`), the PT1000 base, the MAX31865
  resistance conversion and every fault bit.

The full sketch was compiled with arduino-cli 3.3.12 for `esp32:esp32:esp32s3`
(both sensor variants) and for `esp32:esp32:esp32`. Compiling is not running:
the sketch has **not** been exercised on a real board here.

## Troubleshooting

| Symptom | Check |
| ------- | ----- |
| `#error "Copy config.example.h to config.h …"` | That step was skipped; copy the file next to the `.ino`. |
| Serial Monitor stays empty | Native-USB board: *USB CDC On Boot* must be **Enabled**. CH343 board: keep it disabled, 115200 baud, close Thonny/Serial Studio first. |
| `Wi-Fi: no association after …` | 2.4 GHz network, right password, the access point allows new clients. A 5 GHz-only SSID is invisible to this chip. |
| `API: request failed (-1 …)` | DNS or TCP failed: `API_URL` unreachable from that network. It must be the API host, not the dashboard. |
| `API 401` | `API_KEY` does not match `FURNACE_INGEST_KEY`. The batch is kept and retried. |
| `API 404` | `API_URL` should end in `/api/ingest`. |
| `API: TLS certificate not verified` | Expected with `API_TLS_VERIFY false`. A *handshake* failure instead means the CA is not in the ESP32 store — keep verification off, or put the right PEM in `API_ROOT_CA`. |
| All channels `-1000` / `--.-` | Wiring: 3V3, GND, SCK and MISO (and MOSI on the MAX31865), one CS per board, thermocouple polarity. |
| RTD reads ≈ `-242 °C` on every channel | Open input: no probe, broken lead, or `MAX31865_WIRE_MODE` not matching the board jumper. |
| Sketch does not fit | *Tools → Partition Scheme → Huge APP (3 MB No OTA)*. |
| Dashboard timer runs twice as fast | Two boards are posting into the same run; use one board per run. |

## Safety

A monitoring prototype, **not** a safety-rated furnace controller. Keep the
independent hardware limit controller and thermal cut-out described in the
[main README](../../../README.md#safety) in place.
