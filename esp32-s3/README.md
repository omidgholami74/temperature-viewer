# ESP32-S3 firmware — Wi-Fi straight to the API

Firmware for an **ESP32-S3** that samples four furnace channels at 10 Hz and
uploads them **directly to the PHP API** over Wi-Fi. There is no middle layer:
no PC, no `collector/`, no USB cable, and no file on a flash drive. The board is
the collector.

Two sensor variants are included, and the rest of the project is unchanged:

| Folder                  | Sensor                        | Probe              | Pins (see the folder README)                    |
| ----------------------- | ----------------------------- | ------------------ | ----------------------------------------------- |
| [`max31855/`](max31855/README.md) | 4x MAX31855 + K-type thermocouple | 4x T / K | SCK 14, MISO 12, CS 15/16/17/18        |
| [`max31865/`](max31865/README.md) | 4x MAX31865 + PT100/PT1000 RTD    | 2/3/4-wire | SCK 14, **MOSI 11**, MISO 12, CS 15/16/17/18 |

`common/` holds the files both variants share: `config.example.py`, `wifi.py`
and `ingest.py`.

## Why no collector

```
   ESP32-S3 (Wi-Fi)                        API                    Browser
 ┌──────────────────────┐   POST    ┌──────────────────┐   GET   ┌──────────┐
 │ 4 x sensor @ 10 Hz   │ ────────▶ │ /api/ingest      │ ──────▶ │  ui/     │
 │ buffer → JSON batch   │  X-Api-Key│ SQLite + runs    │  JSON   │ dashboard│
 └──────────────────────┘           └──────────────────┘         └──────────┘
```

The payload is byte-for-byte what `collector/collector.py` would have sent, so
the API, the database and the dashboard need no change:

```json
{"samples": [
  {"u": 1200, "at": 1792523131000, "t": [781.25, 660.5, null, 545.75], "f": [0, 0, 1, 0]}
]}
```

- `u` — milliseconds since the board booted (the 100 ms grid inside a batch).
- `at` — optional absolute Unix time in ms, sent when NTP synced. It is what
  keeps the history correct after a reboot or a Wi-Fi outage.
- `t` — four temperatures in °C, `null` for a faulted channel (never `NaN`).
- `f` — fault bit mask per channel: `1` open, `2` short to GND, `4` short to
  VCC, `8` read error.

## Files

```
esp32-s3/
├── README.md                 this file
├── common/
│   ├── config.example.py     copy to config.py and edit
│   ├── wifi.py               association, reconnect, NTP clock
│   └── ingest.py             batch buffer + HTTP(S) POST to /api/ingest
├── max31855/
│   ├── main.py               firmware for 4x MAX31855
│   └── README.md             wiring, thermocouple notes, troubleshooting
├── max31865/
│   ├── main.py               firmware for 4x MAX31865 RTD
│   ├── max31865.py           RTD driver: CDC maths, fault register
│   └── README.md             wiring, 2/3/4-wire, RREF, calibration
└── tests/
    └── test_firmware.py      HTTP client and RTD maths, no hardware needed
```

## Install

### 1. Flash MicroPython

Any recent **ESP32-S3** build works (`ESP32_GENERIC_S3-*.bin` from
<https://micropython.org/download/ESP32_GENERIC_S3/>). Some boards need the
`SPIRAM` variant; pick what the board's vendor documents.

```bash
python -m pip install esptool mpremote
esptool.py --chip esp32s3 --port COM6 erase_flash
esptool.py --chip esp32s3 --port COM6 --baud 460800 write_flash -z 0 ESP32_GENERIC_S3-20240602-v1.23.0.bin
```

`COM6` is the port the board gets on Windows. The CH343 bridge used on many
ESP32-S3 boards shows up as `USB-Enhanced-SERIAL CH343 (COM6)`; if the port is
missing, install the WCH CH343 driver. On Linux it is `/dev/ttyACM0` or
`/dev/ttyUSB0`, on macOS `/dev/cu.usbmodem*` or `/dev/cu.wchusbserial*`.

### 2. Create `config.py`

```bash
cp esp32-s3/common/config.example.py /tmp/config.py
# edit /tmp/config.py: WIFI_SSID, WIFI_PASSWORD, API_URL, API_KEY
```

### 3. Upload the firmware

For the thermocouple variant:

```bash
mpremote connect COM6 \
  cp /tmp/config.py :config.py + \
  cp esp32-s3/common/wifi.py :wifi.py + \
  cp esp32-s3/common/ingest.py :ingest.py + \
  cp esp32-s3/max31855/main.py :main.py
```

For the RTD variant, add `max31865.py` and use the other `main.py`:

```bash
mpremote connect COM6 \
  cp /tmp/config.py :config.py + \
  cp esp32-s3/common/wifi.py :wifi.py + \
  cp esp32-s3/common/ingest.py :ingest.py + \
  cp esp32-s3/max31865/max31865.py :max31865.py + \
  cp esp32-s3/max31865/main.py :main.py
```

With Thonny it is the same idea: open each file and *Save to board* under the
name after the colon (`config.py`, `wifi.py`, `ingest.py`, `main.py`). With the
files at the board root, `main.py` runs automatically after every reset.

```bash
mpremote connect COM6 reset      # boot the firmware
mpremote connect COM6 repl       # watch the console (Ctrl-] to leave)
```

### 4. Confirm the dashboard sees it

Open the dashboard (`ui/`, production <https://temp.futurefounderco.com>). The
cards fill within one batch (= `BATCH_SIZE` samples, 2 s by default) and the
chip switches from *Paused* to *Recording*.

## Configuration

Everything is a constant in `config.py`; there are no command-line arguments on
a microcontroller.

| Setting         | Default                          | Meaning                                        |
| --------------- | -------------------------------- | ---------------------------------------------- |
| `WIFI_SSID`     | —                                | 2.4 GHz network; the ESP32-S3 has no 5 GHz     |
| `WIFI_PASSWORD` | —                                |                                                |
| `WIFI_HOSTNAME` | `furnace-esp32`                  | Name in the router's client list               |
| `WIFI_TIMEOUT_S`| `20`                             | Association timeout at boot                    |
| `WIFI_RETRY_S`  | `15`                             | Reconnect attempt interval while offline       |
| `API_URL`       | `https://apitemp.futurefounderco.com` | API host; `/api/ingest` is appended       |
| `API_KEY`       | `""`                             | Must equal `FURNACE_INGEST_KEY` on the server  |
| `API_TIMEOUT_S` | `8`                              | Socket timeout per request                     |
| `BATCH_SIZE`    | `20`                             | Samples per POST (max 600); 20 = 2 s of data   |
| `BUFFER_LIMIT`  | `600`                            | Samples kept while the API is down (60 s)      |
| `NTP_ENABLED`   | `True`                           | Send absolute `at` timestamps                  |
| `NTP_HOST`      | `pool.ntp.org`                   |                                                |
| `SERIAL_ECHO`   | `True`                           | Also print the CSV line on USB serial          |
| `TLS_VERIFY`    | `False`                          | Validate the `https` certificate (see below)   |

Each queued sample costs roughly 150 bytes of RAM, so the default 600-sample
buffer is about 90 KB — fine on an ESP32-S3, too much for a plain ESP8266.

### TLS

`https://` works through the MicroPython `ssl` module (axtls). Its root store is
small, so a certificate from a CA that is not bundled fails the handshake. With
`TLS_VERIFY = False` the firmware still encrypts the connection but does not
validate the certificate, which is the only option on such a board; keep
`API_KEY` set and the network trusted. Set `TLS_VERIFY = True` once the server's
CA is in the bundle. `http://` also works and skips TLS entirely.

## Behaviour

- **10 Hz grid.** Each pass reads all four channels, prints `ms,f1..f4`, queues
  the frame and sleeps until the next 100 ms slot. The time column is
  *measured* (`ticks_ms`), so a slow upload cannot make it drift; if an upload
  takes longer than a frame, the loop resynchronises instead of burning through
  missed slots, and that gap is visible in the data.
- **Batching.** One request carries `BATCH_SIZE` samples. The API anchors a
  batch without `at` on its arrival time and places the rest on the 0.1 s grid,
  so the dashboard timer (which counts samples) is unaffected by batching.
- **Outages.** A failed upload keeps the batch and retries on the next flush. A
  `400/413/422` is a refused payload and is dropped instead of blocking the
  queue; `401/403` and a wrong URL are retried while the console prints a hint.
  Beyond `BUFFER_LIMIT` the oldest samples are dropped, so RAM cannot fill up.
- **Wi-Fi.** A dropped link is retried every `WIFI_RETRY_S`; sampling and
  buffering never stop. When the link comes back and NTP was never synced, the
  clock is fetched at that moment.
- **One board per run.** The dashboard timer counts stored samples as 0.1 s
  each. Two boards streaming into the same run therefore make it count twice as
  fast — use one board per run (or a separate database per furnace group).
- **Console.** The USB serial line is identical to `module/`, so Thonny, Serial
  Studio and [`serial-web/`](../serial-web/README.md) still work while the
  board uploads. Set `SERIAL_ECHO = False` to silence it.

## Safety

This is a monitoring prototype, not a safety-rated controller. Keep the
independent hardware limit controller and thermal cut-out from the main
[README](../README.md#safety) in place.

## Tests

No board is needed. The HTTP client is exercised against a real local server
and the RTD maths against IEC 60751 reference values:

```bash
python3 -m unittest discover -s esp32-s3/tests -v
```

## Troubleshooting

| Symptom | Check |
| ------- | ----- |
| `Wi-Fi: no association…` | The network is 2.4 GHz, the password is right, the access point allows new clients. A 5 GHz-only SSID is invisible to this chip. |
| `API: … not sent: [Errno 103] ECONNABORTED` / `-202` | The API host is unreachable from that network; check `API_URL` and DNS (`socket.getaddrinfo`), not the dashboard host. |
| `API 401` | `API_KEY` does not match `FURNACE_INGEST_KEY`. The key is kept and retried, so fix the config and restart. |
| `API 404` | `API_URL` points at the dashboard or at a path without `/api/ingest`. |
| `API: TLS certificate not verified` | Expected with `TLS_VERIFY = False`; the connection is encrypted but unverified. A handshake failure instead means the CA is not in the axtls bundle — use `http://` or `TLS_VERIFY = False`. |
| Data arrives with a delay | `BATCH_SIZE` samples are buffered before each POST; lower it (for example 10) for faster updates at more requests per second. |
| All four channels `--.-` / `-1000` | Wiring: 3V3, GND, SCK and MISO (and MOSI on the MAX31865), one CS per board, thermocouple polarity. Run `module/check.py`-style spot checks or the serial console. |
| Dashboard shows a second run starting | The board's `at` jumped (NTP correction) or someone pressed *Start new run*; see [backend/README.md](../backend/README.md#post-apiingest). |
