# Collector — USB serial samples to the API

> Where this sits in the stack: [docs/system-architecture.md](../docs/system-architecture.md).
> Timestamps and retention: [docs/data-model.md](../docs/data-model.md).

A Pico is a plain, non-networked board. An ESP32-S3 can also be put in
`USB_ONLY` mode, where it does not join Wi-Fi or call the API either. Both print
one CSV line per sample on USB serial (`105,781.25,660.00,940.00,545.75`). This
console program runs on a PC or a small Linux host and does two things:

1. converts those lines into samples and writes them into a JSON file on the
   computer, and
2. uploads that file to the PHP API in batches.

Because the accepted byte offset is stored in `data/checkpoint.json`, stopping
the program or losing the network never loses samples and never re-sends data
that the API already accepted.

## Install and run

```bash
python -m pip install -r collector/requirements.txt   # pyserial, serial mode only
python collector/collector.py
```

There are no command-line arguments — edit the constants at the top of
`collector.py`.

Output looks like this:

```
Reading /dev/ttyACM0 at 115200 baud into /home/you/collector/data/samples.jsonl
Sent 100 samples (total 100) | temperatures:  781.2   660.0   940.5   545.7
Sent 100 samples (total 200) | temperatures:  781.4   660.1   940.4   545.6
```

## Settings

| Constant          | Default                     | Meaning                                     |
| ----------------- | --------------------------- | ------------------------------------------- |
| `SOURCE`          | `"serial"`                  | `"serial"` live capture, or `"file"`        |
| `SERIAL_PORT`     | `""`                        | `COM5` / `/dev/serial/by-id/...`; empty auto-detects |
| `BAUD_RATE`       | `115200`                    | Must match the Pico                         |
| `SOURCE_FILE`     | `""`                        | Log to upload when `SOURCE = "file"`        |
| `LOG_ENDED_AT`    | `""`                        | ISO 8601 end of that log; empty uses mtime  |
| `DATA_DIR`        | `data`                      | Folder written on this computer             |
| `SPOOL_FILE`      | `<DATA_DIR>/samples.jsonl`  | File that grows at 10 Hz in serial mode     |
| `CHECKPOINT_FILE` | `<DATA_DIR>/checkpoint.json`| Accepted byte offset per file               |
| `API_URL`         | `http://127.0.0.1:8080`     | Backend base URL, no trailing slash         |
| `API_KEY`         | `""`                        | Must equal `FURNACE_INGEST_KEY` when set    |
| `BATCH_SIZE`      | `100`                       | Samples per request                         |
| `POLL_INTERVAL_S` | `1.0`                       | Idle wait between upload checks             |
| `RETRY_DELAYS_S`  | `(1, 2, 5, 10, 30)`         | Backoff between failed uploads              |

Relative paths are resolved against this folder, so the program works from any
working directory; absolute paths are used as they are.

### Choosing where the data is stored

`DATA_DIR` decides where samples and the checkpoint are written:

```python
DATA_DIR = "data"                       # collector/data (default)
DATA_DIR = "D:/furnace-data"            # Windows, any drive
DATA_DIR = "C:/Users/omid/furnace"      # Windows, user folder
DATA_DIR = "/var/lib/furnace"           # Linux/macOS
DATA_DIR = "/Volumes/USB/furnace"       # removable disk
```

Use forward slashes or a raw string (`r"D:\furnace-data"`) on Windows. The
folder is created automatically, and both `samples.jsonl` and `checkpoint.json`
follow it, so moving the data folder keeps the upload position consistent.

For production set:

```python
API_URL = "https://apitemp.futurefounderco.com"
API_KEY = "the same value as FURNACE_INGEST_KEY"
```

The `://` is part of the value. A typo such as `https//host` or `https:/host`
is reported at startup instead of crashing on the first upload.

If uploads fail with a certificate error (`CERTIFICATE_VERIFY_FAILED`,
`SSLCertVerificationError`, or "hostname mismatch"), the host is serving a
certificate that does not match `apitemp.futurefounderco.com` — typically the
hosting provider's default certificate. Either install a certificate for the
subdomain or point `API_URL` at `http://` for now. The collector talks to the
API server-side, so switching its scheme has no effect on the browser
dashboard, which uses its own `ui/config.js` origin.

## Serial mode

Default. The program detects a Pico or ESP32-S3 USB serial device, turns each
CSV line into a sample, appends it to `<DATA_DIR>/samples.jsonl` and a
background thread uploads from it every second. If the cable is unplugged, it
retries the port every 3 s; the Pico keeps logging to its own flash when
`LOG_TO_FLASH` is enabled.

## USB-only ESP32-S3 through a USB-to-LAN host

This is the no-Wi-Fi layout: the board's USB-UART (CH343) port goes to a
Raspberry Pi or OpenWrt host, and the host's **USB-to-LAN adapter** goes to the
LAN. The adapter belongs on the host: a USB-to-LAN adapter is a USB device and
cannot provide a network interface when plugged into the ESP32-S3's USB device
port. The board only streams CSV; the host runs this collector.

On the board, in `esp32-s3/config.py`:

```python
USB_ONLY = True
SERIAL_ECHO = True
```

`USB_ONLY` skips WLAN, NTP, heartbeat, OTA and the direct uploader; the firmware
forces serial echo on. `API_URL` and `API_KEY` on the board are unused in this
mode. An existing `/settings.json` still controls `sample_ms`; delete it or set
the interval you want if it was saved by the old Wi-Fi setup. Connect the
**CH343 / USB-UART** port (the one Thonny uses) to the host at 115200 baud.

On the host:

1. Plug the USB-to-LAN adapter into the host and confirm that it has a LAN
   address and a default route:

   ```bash
   ip -br addr
   ip route
   ping -c 3 apitemp.futurefounderco.com
   ```

2. Install Python and the serial dependency:

   ```bash
   cd /home/pi/temperature-viewer
   python3 -m venv .venv
   .venv/bin/pip install -r collector/requirements.txt
   sudo usermod -aG dialout "$USER"   # log out and back in afterwards
   ```

3. Find the board's stable serial path:

   ```bash
   ls -l /dev/serial/by-id/*
   ```

   Prefer the `by-id` path over `/dev/ttyUSB0`, which can change after a reboot.

4. Edit the constants at the top of `collector/collector.py`:

   ```python
   SERIAL_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
   API_URL = "https://apitemp.futurefounderco.com"
   API_KEY = "the same value as FURNACE_INGEST_KEY"
   DATA_DIR = "/var/lib/furnace"
   ```

   The collector stamps samples with the host clock, so keep the host's time
   synchronised (`timedatectl` on Raspberry Pi OS).

5. Run it once in the foreground and confirm that samples arrive:

   ```bash
   sudo mkdir -p /var/lib/furnace
   sudo chown "$USER":"$USER" /var/lib/furnace
   .venv/bin/python collector/collector.py
   ```

6. For unattended operation, copy the supplied systemd unit and adjust its
   user, checkout path and virtualenv path:

   ```bash
   sudo cp collector/furnace-collector.service /etc/systemd/system/
   sudo systemctl edit --full furnace-collector
   sudo systemctl daemon-reload
   sudo systemctl enable --now furnace-collector
   journalctl -u furnace-collector -f
   ```

OpenWrt does not use systemd; run the same command from its init system
(procd) or a container. It needs Python 3 and pyserial. Keep the USB-to-LAN
adapter on the host either way.

## File mode

When the PC is not connected to the Pico all the time, copy the log from the
Pico's flash (Thonny: open `samples.jsonl` on the Pico and save it to the PC,
or `mpremote cp :samples.jsonl samples.jsonl`), put it in this folder and run:

```python
SOURCE = "file"
SOURCE_FILE = "samples.jsonl"
LOG_ENDED_AT = "2026-10-20T14:05:00Z"  # when the log was written
```

The program uploads the file from its first unsent line and exits when
everything is accepted. Running it again continues where the last run stopped;
delete `data/checkpoint.json` to start the same file from the beginning.

Upload a copied log into a **fresh or reset run**: the first upload of an empty
database starts the run at the log's first sample, while a backlog sent into an
already running timeline can only be clamped to its start.

### Timestamps

The firmware has no clock, so its lines carry only `u` (milliseconds since it
booted). The collector turns that into an absolute time and sends it as `at`,
which the API stores as-is:

- **Serial mode** — the first line of each connection is mapped to the PC's
  clock; the mapping is reused for the whole connection, so a burst delivered
  after a stall is not squeezed together. A Pico reboot (uptime going
  backwards) starts a new mapping.
- **File mode** — the newest line is placed at `LOG_ENDED_AT`, or at the file's
  modification time when that is empty. Copying a log updates its mtime, so
  set `LOG_ENDED_AT` whenever you know when the Pico wrote it.

Without that mapping the API can only anchor each upload to the moment it
arrives, which compresses a long backlog into a few seconds.

## Files it creates

| Path (inside `DATA_DIR`) | Purpose                                       |
| ------------------------ | --------------------------------------------- |
| `samples.jsonl`          | Live serial capture, one JSON sample per line |

A CSV line has four or five numbers: uptime in ms and three (MAX31855) or four
(MAX31865/Pico) channel temperatures, with `-1000.00` for a channel that
faulted. The CSV carries no fault bits, so a `-1000.00` column is uploaded as
`null` with fault `8`; an absent fourth channel is padded as `null` with fault
`0`. JSON lines are accepted too, which is what `SOURCE = "file"` reads from
the Pico's own log.
| `checkpoint.json`        | `{"source": ..., "offset": ...}`              |

Both are ignored by git. They are safe to keep: deleting the spool only loses
samples that had not been uploaded yet.

## Notes

- Delivery is at-least-once. If the API stores a batch but the response is
  lost, the same samples are sent again after a restart; timestamps inside each
  sample keep the 10 Hz grid intact.
- A line without a trailing newline is treated as still being written and is
  left for the next cycle.
- Lines that are neither a sample JSON object nor four/five CSV numbers (boot
  banners, header rows, truncated writes) are skipped and logged.
- A payload the server refuses outright (`400`, `413`, `422`) is dropped so a
  single bad line cannot block the queue. Other statuses (`401`, `403`, `404`,
  `405`) keep the data and retry, because they mean the URL or key is wrong.
- `API_URL` should be `https://` in production. If the host serves a
  certificate for a different domain, the collector reports
  `TLS certificate problem ...` and keeps the data; install SSL for the domain
  (AutoSSL / Let's Encrypt) rather than disabling verification.

### Windows: `PermissionError: [WinError 5]` on `checkpoint.json`

Windows refuses to rename a file that another program holds open, and a
scanner or sync client touches `checkpoint.json` right after it is written:

```
PermissionError: [WinError 5] Access is denied:
  '...\data\checkpoint.json.tmp' -> '...\data\checkpoint.json'
```

The collector now retries the rename, then rewrites the file in place, and the
upload thread keeps running even when the save keeps failing; the samples stay
in `samples.jsonl` and the next batch retries. The warning printed in that case
names the file and the reason.

If it keeps happening, one of these is holding the file:

- Antivirus real-time scanning the project folder. Exclude `DATA_DIR`.
- OneDrive/Dropbox/Google Drive syncing `E:\dev\...`. Move `DATA_DIR` out of
the synced folder, for example `DATA_DIR = "C:/furnace-data"`.
- A second copy of the collector still running. Close it; two writers fight
  over the same checkpoint.
- The file was opened in Notepad or Excel and left open. Close it, or clear the
  read-only attribute with `attrib -r data\checkpoint.json`.

Checkpoints are written in place only as a fallback, so a crash during that
write can leave damaged JSON; the collector then reads it as offset `0` and
re-sends already accepted samples (delivery is at-least-once, so no data is
lost, only duplicated).

## Tests

No hardware and no network needed; the checkpoint behaviour is exercised with
temporary files:

```bash
python3 -m unittest discover -s collector/tests -v
```
