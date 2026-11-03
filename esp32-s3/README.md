# ESP32-S3 firmware — Wi-Fi straight to the API

> Pins, probe wiring and power: [docs/hardware.md](../docs/hardware.md).
> The 100 ms loop, watchdog and fault handling: [docs/firmware.md](../docs/firmware.md).

Firmware for an **ESP32-S3** that samples the furnace channels at 10 Hz and
uploads them **directly to the PHP API** over Wi-Fi. There is no middle layer:
no PC, no `collector/`, no USB cable, and no file on a flash drive. The board is
the collector.

The MAX31855 build is wired for **three furnaces** (CS 15/16/17). The MAX31865
build keeps four. The API frame is four slots wide either way: a slot with no
probe is sent as `null` with no fault, so an unused channel is not reported as
a broken one.

Two sensor variants are included, and the rest of the project is unchanged:

| Folder                  | Sensor                        | Probe              | Pins (see the folder README)                    |
| ----------------------- | ----------------------------- | ------------------ | ----------------------------------------------- |
| [`max31855/`](max31855/README.md) | 3x MAX31855 + K-type thermocouple | 3x K | SCK 14, MISO 12, CS 15/16/17          |
| [`max31865/`](max31865/README.md) | 4x MAX31865 + PT100/PT1000 RTD    | 2/3/4-wire | SCK 14, **MOSI 11**, MISO 12, CS 15/16/17/18 |

`common/` holds the files both variants share: `config.example.py`, `wifi.py`,
`ingest.py`, `supervisor.py`, `ota.py`, `device.py` and `settings.py`.

### MicroPython or Arduino?

Both flavours are included and do the same job — pick by the toolchain you
already use:

| | MicroPython (`common/`, `max31855/`, `max31865/`) | Arduino/C++ ([`arduino/`](arduino/README.md)) |
| --- | --- | --- |
| Written for | Thonny, `mpremote` | Arduino IDE 2.x, `arduino-cli` |
| Upload | copy `.py` files to the board | compile and flash one sketch |
| Pins | identical | identical |
| CSV on USB serial | yes | yes |
| Payload | identical | identical |

The Arduino sketch covers the same two sensors (switch with one `#define`),
uses no extra library beyond the ESP32 core, and its maths/JSON are tested on a
PC with `g++`. Details: [`arduino/README.md`](arduino/README.md).

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
  {"u": 1200, "at": 1792523131000, "t": [781.25, 660.5, null, null], "f": [0, 0, 1, 0]}
]}
```

- `u` — milliseconds since the board booted (the 100 ms grid inside a batch).
- `at` — optional absolute Unix time in ms, sent when NTP synced. It is what
  keeps the history correct after a reboot or a Wi-Fi outage.
- `t` — four slots in °C. A faulted channel is `null` (never `NaN`), and so is
  a slot with no probe: the three-furnace build leaves slot 4 empty.
- `f` — fault bit mask per channel: `1` open, `2` short to GND, `4` short to
  VCC, `8` read error. An empty slot has fault `0`.

## Files

```
esp32-s3/
├── README.md                 this file
├── boot.py                   boot diagnostics: reset cause, missing files
├── common/
│   ├── config.example.py     copy to config.py and edit
│   ├── wifi.py               association, reconnect, NTP clock
│   ├── ingest.py             batch buffer + HTTP(S) POST to /api/ingest
│   ├── ota.py                bundle download, staging, apply and rollback
│   ├── device.py             heartbeat, remote commands, Wi-Fi change
│   ├── settings.py           live settings: rate, filter, heartbeat, offsets
│   └── supervisor.py         watchdog + restart, so no PC is needed
├── max31855/
│   ├── main.py               firmware for 3x MAX31855
│   └── README.md             wiring, thermocouple notes, troubleshooting
├── max31865/
│   ├── main.py               firmware for 4x MAX31865 RTD
│   ├── max31865.py           RTD driver: CDC maths, fault register
│   └── README.md             wiring, 2/3/4-wire, RREF, calibration
├── arduino/                  the same firmware as an Arduino/C++ sketch
│   ├── README.md             install, pins, build sizes, troubleshooting
│   ├── furnace_arduino/      open this folder in the Arduino IDE
│   └── tests/                host tests with g++ (no board needed)
├── tools/
│   └── publish.py            build an OTA bundle for the API
└── tests/
    ├── test_firmware.py      HTTP client, OTA bundle, RTD maths — no board
    └── test_max31855.py      frame validation and majority reads
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
  cp esp32-s3/boot.py :boot.py + \
  cp /tmp/config.py :config.py + \
  cp esp32-s3/common/wifi.py :wifi.py + \
  cp esp32-s3/common/ingest.py :ingest.py + \
  cp esp32-s3/common/supervisor.py :supervisor.py + \
  cp esp32-s3/common/ota.py :ota.py + \
  cp esp32-s3/common/device.py :device.py + \
  cp esp32-s3/common/settings.py :settings.py + \
  cp esp32-s3/max31855/main.py :main.py
```

For the RTD variant, add `max31865.py` and use the other `main.py`:

```bash
mpremote connect COM6 \
  cp esp32-s3/boot.py :boot.py + \
  cp /tmp/config.py :config.py + \
  cp esp32-s3/common/wifi.py :wifi.py + \
  cp esp32-s3/common/ingest.py :ingest.py + \
  cp esp32-s3/common/supervisor.py :supervisor.py + \
  cp esp32-s3/common/ota.py :ota.py + \
  cp esp32-s3/common/device.py :device.py + \
  cp esp32-s3/common/settings.py :settings.py + \
  cp esp32-s3/max31865/max31865.py :max31865.py + \
  cp esp32-s3/max31865/main.py :main.py
```

With Thonny it is the same idea: open each file and *Save to board* under the
name after the colon (`boot.py`, `config.py`, `wifi.py`, `ingest.py`,
`supervisor.py`, `ota.py`, `device.py`, `settings.py`, `main.py`). With the
files at the board root, `main.py` runs
automatically after every reset and every power-up.

```bash
mpremote connect COM6 reset      # boot the firmware
mpremote connect COM6 repl       # watch the console (Ctrl-] to leave)
```

`mpremote cp` does not reset the board, so finish an upload with `reset`.

### 4. It does not start by itself?

`main.py` runs on its own after every boot, so a board on a charger needs
nothing typed. Two things stop it:

- **A file is missing from the board root.** `main.py` imports `config`,
  `wifi`, `ingest`, `supervisor` and `settings`; one absent file is an
  `ImportError`, and
the board then sits at a REPL prompt. `boot.py` names them:

  ```
  # boot: reset cause PWRON_RESET
  # boot: MISSING from the board root: supervisor.py
  # boot: main.py cannot start until those files are uploaded
  ```

- **Thonny is attached.** Thonny stops the running program when it connects,
  so the Shell shows `>>>` and the sampling has stopped — that is the IDE, not
the firmware. Press **F5** (*Run current script*), press RST on the board, or
  close Thonny and run `mpremote connect COM6 reset`.

Check what is really at the root with `mpremote connect COM6 ls`.

### 5. Confirm the dashboard sees it

Open the dashboard (`ui/`, production <https://temp.futurefounderco.com>). The
cards fill within one batch (= `BATCH_SIZE` samples, 1 s by default) and the
chip switches from *Paused* to *Recording*.

## Running from a charger (no computer)

The board is meant to sit next to the furnace on a plain USB charger: the
charger only supplies 5 V, everything else happens over Wi-Fi. Any 5 V supply
with at least 500 mA works. With the files from [Install](#install) at the
board root, MicroPython runs `main.py` on every power-up, so there is nothing
to start and nothing to plug in.

| Situation | What the firmware does on its own |
| --------- | --------------------------------- |
| Power-up | `main.py` starts, `wifi.py` joins the network, sampling begins at once — even while Wi-Fi is still down. |
| Wi-Fi drops, router reboots | A rejoin is tried every `WIFI_RETRY_S`; samples keep queueing in RAM. |
| API unreachable | The batch is kept and retried, the queue is trimmed at `BUFFER_LIMIT` so RAM cannot fill up. |
| The sampling loop hangs (SPI, network) | The hardware watchdog reboots the board after `WATCHDOG_S`, and it starts sampling again by itself. |
| An exception escapes the loop | `supervisor.run_forever` logs it and rebuilds the session after a backoff of 5–60 s. |
| The upload thread dies | The loop notices within 15 s, starts it again, and falls back to inline sending if that fails. |
| A power blip | The board reboots and continues; NTP timestamps keep the history correct. |

**Remote management.** With `device.py` on the board, it reports every
`HEARTBEAT_S` seconds (firmware, IP, RSSI, uptime, sent/buffered) and receives
the commands queued in [`ui/admin.html`](../ui/admin.html): update firmware,
restart, or change Wi-Fi. A Wi-Fi change is written to `/wifi.json` and used on
the next boot, so the credentials in `config.py` are never edited over the air.
A board flashed without `device.py` still records; it just does not appear in
the admin panel.

**What a reboot costs.** The queue lives in RAM, so a watchdog reboot or a
power cut drops the samples that had not been POSTed yet — at most
`BUFFER_LIMIT`, which is one minute at 10 Hz. Everything already sent is stored
on the server. Under genuine heap pressure the firmware may trim to a smaller
low-water mark to keep the TLS uploader alive; the console logs the number
dropped.

**Nothing has to be pressed.** MicroPython runs `boot.py` and then `main.py` at
every power-up; the shipped `boot.py` only prints the reset cause and names a
missing file, it never starts the sampler itself. If the board was rebooted by
the watchdog, the first line on the console says so the next time you plug it
into a PC:

```
# boot: reset cause WDT_RESET
# firmware stopped: OSError: [Errno 116] ETIMEDOUT
# restarting in 5 s (restart 1)
```

`PWRON_RESET` is a normal power-up. A repeating `WDT_RESET` means something in
the loop blocks longer than `WATCHDOG_S` — see the troubleshooting table. A
repeating `# firmware stopped:` names the crash: usually a missing file
(`config.py`, `wifi.py`, `ingest.py`, `supervisor.py`) or a bad `API_URL`.

**NTP on a phone hotspot.** Many carriers and Android hotspots drop the
outbound UDP 123 that `ntptime` needs, so the console prints
`NTP: sync failed: [Errno 116] ETIMEDOUT` and the board sends no `at`. The
server then stamps each batch on arrival: the dashboard and the counter work
normally, only a backfill after a long outage would be placed at the wrong
time. Nothing to fix.

**The serial line** is printed on UART0 (the CH343/USB-UART port). A UART keeps
shifting bytes out with nothing connected, so it never stalls the loop; set
`SERIAL_ECHO = False` to save the work and the few milliamps. On a board whose
console is the native USB-Serial-JTAG instead (no CH343 chip), turn the echo
off before running from a charger.

## Remote updates (OTA)

After the first USB flash, the board updates only when an operator asks it to
from the admin panel (`ui/admin.html`). There is no periodic update check: the
next heartbeat (every `HEARTBEAT_S`, 15 s by default) delivers the `ota` command,
and only then does the board fetch a bundle. A bundle is built from this folder
and copied to the server once.

```bash
# 1. on the machine that has the repository
python3 esp32-s3/tools/publish.py --variant max31855
#    -> backend/var/firmware/max31855/{manifest.json,boot.py,main.py,...}

# 2. copy that folder to the API host (or set FURNACE_FIRMWARE_DIR)
rsync -a backend/var/firmware/max31855/ server:/srv/furnace/backend/var/firmware/max31855/

# 3. open ui/admin.html, pick the board and press "Update firmware";
#    the next heartbeat starts the download
```

The bundle contains `boot.py`, `wifi.py`, `ingest.py`, `supervisor.py`,
`ota.py`, `device.py`, `settings.py` and the variant's `main.py` (plus
`max31865.py` on the RTD build).
`config.py` is **never** published, so Wi-Fi credentials and the API key are
not touched by an update.

What happens on the board:

1. The admin panel queues an `ota` command; the heartbeat reply carries it.
2. `ota.py` asks `GET /api/firmware/manifest?channel=max31855` with `X-Api-Key`.
3. If the version differs, it downloads only the files whose SHA-256 differs,
   checks each size and hash, compiles every `.py`, and writes them to
   `/ota/stage`.
4. `main.py` flushes the upload queue and resets.
4. `boot.py` applies the staged files, copies the replaced ones to `/ota/backup`
   and resets again into the new firmware.
5. After a minute of uptime `main.py` confirms the update and deletes the
   backup. If the new firmware crashes before confirming **three times**, the
   next boot restores the backup automatically.

Console output of a successful update:

```
# device: esp32-1, heartbeat every 15 s
# OTA: applied 20261103-120000 (6 files)
# OTA: update confirmed
```

Pressing Update before a bundle exists shows `# OTA: no bundle published for
'max31855' yet`; the board keeps sampling and the admin panel reports the failed
command. `401/403` means `OTA_API_KEY`/`API_KEY` does not match
`FURNACE_INGEST_KEY`.

This covers the **MicroPython** firmware. The Arduino sketch in
[`arduino/`](arduino/README.md) still needs a USB flash, because it is compiled
into a single image rather than living as files on the board.

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
| `BATCH_SIZE`    | `10`                             | Samples per POST (max 600); 10 = 1 s of data   |
| `BACKGROUND_UPLOAD` | `True`                       | Send from a thread so sampling never waits     |
| `BUFFER_LIMIT`  | `600`                            | Samples kept while the API is down (60 s)      |
| `WATCHDOG_S`    | `30`                             | Reboot when the loop stops feeding it; `0` = off |
| `NTP_ENABLED`   | `True`                           | Send absolute `at` timestamps                  |
| `NTP_HOST`      | `pool.ntp.org`                   |                                                |
| `SERIAL_ECHO`   | `True`                           | Also print the CSV line on USB serial          |
| `TLS_VERIFY`    | `False`                          | Validate the `https` certificate (see below)   |
| `DEVICE_ID`     | `esp32-1`                        | Name the admin panel uses; keep it unique      |
| `DEVICE_NAME`   | `Furnace monitor`                | Label shown in the panel                       |

### Live settings from the admin panel

The panel can change the frame interval (`50–60000 ms`), noise filter window
(`1–9` reads), read gap (`0–50000 µs`), EMA alpha (`0.05–1.0`), heartbeat
(`5–3600 s`) and each channel's enabled flag/offset without a reflash. The board
writes `/settings.json` and applies the change on the next frame; the file
survives a reboot, and the heartbeat re-sends the snapshot if the file is lost.
`config.py` and the constants in `main.py` remain the fallback defaults. A
faulted or disabled channel is never offset.
| `HEARTBEAT_S`   | `15`                             | Report + command interval                      |
| `OTA_ENABLED`   | `True`                           | Allow remote updates                           |
| `OTA_CHANNEL`   | `max31855`                       | Which published bundle this board runs         |
| `OTA_URL`       | `""`                            | Different API host for updates; empty = `API_URL` |
| `OTA_API_KEY`   | `""`                            | Different key for bundles; empty = `API_KEY`   |

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

- **10 Hz grid.** Each pass reads every installed channel, prints the CSV
  line, queues the frame and sleeps until the next 100 ms slot. The time column is
  *measured* (`ticks_ms`), so nothing makes it drift.
- **Uploads never pause sampling.** With `BACKGROUND_UPLOAD = True` (default) a
  worker thread does the POST while the main loop keeps reading sensors, so the
  serial column stays on a clean 100 ms grid even during a TLS handshake. The
  queue is shared under a lock and trimmed to `BUFFER_LIMIT`. Without
  `_thread` in the build (or with the setting off) the firmware sends inline as
  before, and a slow request then stretches the sample gap by its duration.
- **Batching.** One request carries `BATCH_SIZE` samples (10 by default, so
  the server has a sample about a second after it was read). The API anchors a
  batch without `at` on its arrival time and places the rest on the 0.1 s grid,
  so the chart and the working rate are unaffected by batching.
- **One connection, one lookup.** The TCP socket (and its TLS session) stays
  open between batches, and the host name is resolved once instead of per
  request: a handshake plus a DNS lookup costs several hundred milliseconds on
  this chip. The socket is dropped and redialled only after a failure or when
  the server closes it; a failed dial also clears the cached address so a new
  DHCP lease is picked up.
- **Outages.** A failed upload keeps the batch and retries on the next flush. A
  `400/413/422` is a refused payload and is dropped instead of blocking the
  queue; `401/403` and a wrong URL are retried while the console prints a hint.
  Beyond `BUFFER_LIMIT` the oldest samples are dropped, so RAM cannot fill up.
- **Wi-Fi.** A dropped link is retried every `WIFI_RETRY_S`; sampling and
  buffering never stop. When the link comes back and NTP was never synced, the
  clock is fetched at that moment.
- **Unattended.** The hardware watchdog is fed once per sample and during Wi-Fi
  waits, so a hang reboots the board instead of freezing it; a crash inside a
  session is logged and the session is rebuilt with a 5–60 s backoff; a dead
  upload thread is restarted by the loop. No keyboard, no reset button and no
  computer are needed after the first upload of the five files.
- **One board per run.** Two boards streaming into the same run interleave
  their frames on the same 0.1 s grid and both count toward the working rate,
  so keep one board per run (or a separate database per furnace group).
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
| `API: … ENOMEM` / `heartbeat failed: ENOMEM` | The heap is short while mbedTLS opens a connection. The firmware now sends only `BATCH_SIZE` samples per request, runs one TLS session at a time and trims the oldest queued samples under memory pressure. If it repeats, use `http://` for a test, lower `BUFFER_LIMIT`, or reduce `BATCH_SIZE`. |
| Data arrives with a delay | `BATCH_SIZE` samples are buffered before each POST; lower it (for example 5) for faster updates at more requests per second. |
| Serial gaps of ~1 s every few seconds | Not the upload: it runs in its own thread (`# upload runs in a background thread` at boot). A gap that size means the loop itself is slow — check the SPI wiring/power and the sensor reads. |
| All four channels `--.-` / `-1000` | Wiring: 3V3, GND, SCK and MISO (and MOSI on the MAX31865), one CS per board, thermocouple polarity. Run `module/check.py`-style spot checks or the serial console. |
| A disconnected channel still reads a plausible temperature | The MAX31855 is not being selected and floating MISO noise looks like a frame. The MAX31855 build pulls MISO up, validates reserved/internal bits and requires three of five good frames; check that channel's CS, 3V3 and GND wiring and replace the module if it persists. |
| `# boot: MISSING from the board root: …` | That file never reached the board: upload it (see [Install](#install)) and reset. `main.py` cannot import it. |
| Console shows `>>>` and nothing samples | Something stopped the program: a missing file (the line above names it) or Thonny attached to the board (press F5 or RST). |
| `# OTA: no bundle published for 'max31855' yet` | Nothing was copied to `FURNACE_FIRMWARE_DIR` on the API host; run `tools/publish.py` and deploy the folder. |
| `# OTA: the API rejected the key` | `OTA_API_KEY`/`API_KEY` does not match `FURNACE_INGEST_KEY`; fix the config and restart the board. |
| The board does not take an update | Nothing is queued: press **Update firmware** in the admin panel and wait for the next heartbeat. If it still does nothing, the published `version` equals `/ota/version` on the board — publish with a new version. |
| `# OTA: rolled back to the previous firmware` | The new bundle crashed three times before confirming; fix it and publish again. `config.py` and the Wi-Fi settings were never touched. |
| Dashboard shows a second run starting | The board's `at` jumped (NTP correction) or someone pressed *Start new run*; see [backend/README.md](../backend/README.md#post-apiingest). |
| `# boot: reset cause WDT_RESET` every few minutes | Something blocks the loop longer than `WATCHDOG_S`: a SPI read that never returns (wiring/power on a sensor board) or a network call. Raise `WATCHDOG_S` above `WIFI_TIMEOUT_S` if you lowered it. |
| `# firmware stopped: …` repeating every few seconds | The session crashes at boot and the message names the cause: a file missing from the board root (`config.py`, `wifi.py`, `ingest.py`, `supervisor.py`), or an `API_URL` that `split_url` rejects. |
| Board reboots in a loop only when the charger is plugged in | The supply sags below 500 mA (or restarts on inrush); use a 5 V/1 A charger and a short cable. |
