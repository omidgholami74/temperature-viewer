# Simple MAX31855 logger

This folder is the deliberately small path:

```
MAX31855 -> ESP32-S3 (this main.py) -> USB serial CSV
                                      -> collector/collector.py
                                         -> samples.jsonl on the host
                                         -> POST /api/ingest
```

It has **no Wi-Fi, HTTP, OTA, heartbeat, settings file, median filter or live
control**. The board only reads the sensors and prints one CSV row every
100 ms. The existing collector owns the file and the upload.

The file is written on the **host**, not on the ESP32 flash. That is intentional:
the collector can tail it, upload it and retry without wearing the board's
flash or filling its small filesystem.

## Files

| File | Runs on | Purpose |
| --- | --- | --- |
| `main.py` | ESP32-S3 / Pico | Read MAX31855 and print CSV on USB serial |
| `test_logic.py` | PC | Tests for decoding and temperature-range offsets |
| `server/public/index.php` | PHP host | `temp` API and the small router |
| `server/public/index.html` | Browser | Dark three-furnace dashboard |
| `server/public/app.js` | Browser | Fast `/api/recent` polling and chart |
| `server/data/` | PHP host | Runtime `recent.json`; outside the document root |

## Wiring

Defaults are for the ESP32-S3:

| MAX31855 | ESP32-S3 |
| --- | --- |
| VIN | 3V3 |
| GND | GND |
| DO / MISO | GPIO12 |
| CLK / SCK | GPIO14 |
| CS | GPIO15, GPIO16, GPIO17 |

Use 3.3 V only. The MAX31855 is not a 5 V part. Keep the SPI wires short and
give each amplifier a decoupling capacitor close to VIN/GND.

For a Pico, change the top of `main.py`:

```python
SPI_ID = 0
SCK_PIN = 6
MISO_PIN = 4
CS_PINS = (5, 7, 9, 11)
```

Add a fourth pin to `CS_PINS` for a fourth furnace. The header and CSV adapt to
the number of pins automatically.

## Install on the board

Copy only `main.py` to the board as `main.py`:

```bash
mpremote connect /dev/ttyUSB0 cp simple-max31855/main.py :main.py
```

Use the CH343/UART port at 115200 baud. On a native USB ESP32-S3 the port may
be `/dev/ttyACM0`; use the stable `/dev/serial/by-id/...` path on the host.

The old `boot.py`, `config.py`, `wifi.py` and other files can stay; this
`main.py` does not import them. For the cleanest setup, a board with only
MicroPython and this `main.py` is enough.

The board prints:

```text
# max31855: 3 channels, 100 ms, USB only
ms,t1,t2,t3
105,781.25,-1000.00,940.00
205,781.25,660.00,940.00
```

`-1000.00` means the channel reported a fault. The collector turns that into
`null` and a fault bit, so the dashboard shows a broken probe rather than
0 °C.

## Temperature offsets

Edit `OFFSET_BANDS` at the top of `main.py`:

```python
OFFSET_BANDS = (
    (-270.0, 200.0, 0.0),    # cold range
    (200.0, 600.0, -1.5),    # middle range
    (600.0, 1800.0, -3.0),   # hot range
)
```

- A band is `min <= temperature < max`.
- The first matching band wins.
- Outside every band the offset is `0.0`.
- A faulted channel is never offset; it stays `-1000.00`.
- The same bands apply to every channel.

To find an offset, compare the raw reading with a reference thermometer at
two or three points, then put the correction in the matching band.

## Sampling speed

`SAMPLE_INTERVAL_MS = 100` gives 10 samples per second, which is the MAX31855
conversion limit. Lowering it further does not get fresh sensor data. The loop
is drift-free: it schedules from the previous slot, not from the time a serial
write happened to finish.

The code validates the frame before trusting it:

- rejects all-zero and all-one frames from a floating MISO line,
- rejects reserved bits and an impossible internal temperature,
- rejects fault bits, and rejects values outside -270…1800 °C,
- pulls MISO high when no chip is selected.

There is deliberately no median/EMA filter. A bad frame is shown as a fault;
the next 100 ms sample replaces it.

## Collector and file

Use the collector already in this repository. These settings normally need
editing at the top of `collector/collector.py`:

```python
SERIAL_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
API_URL = "https://temp.futurefounderco.com"
API_KEY = "the same value as FURNACE_INGEST_KEY"
BATCH_SIZE = 5
POLL_INTERVAL_S = 0.2
DATA_DIR = "/var/lib/furnace"
```

Then:

```bash
python3 -m pip install -r collector/requirements.txt
python3 collector/collector.py
```

It detects the CH343/ESP32 serial port when `SERIAL_PORT` is empty, appends each
sample to `DATA_DIR/samples.jsonl`, and uploads it in small batches. The file
and `checkpoint.json` are on the host. A serial disconnect only pauses capture;
a network outage only delays upload, because the file remains on disk.

The collector already accepts this 3-channel row. A 4-channel board (four pins)
is accepted as well.

## Live server and dashboard

`server/public/` is the complete app for **https://temp.futurefounderco.com**.
It serves the dark UI and accepts the collector batches on the same origin, so
the browser needs no CORS setup. There is no database, framework or build step.

```text
server/
  public/               <- document root
    index.php           API + router + static file headers
    index.html
    style.css
    app.js
    logo.svg
    favicon.svg
    .htaccess            Apache routing and headers
  data/                 <- outside public; must be writable by PHP
    recent.json         created automatically
    recent.lock
```

### Deploy

Requirements: PHP 8.1 or newer with the standard JSON extension, and an HTTPS
certificate for the hostname. Use PHP-FPM behind Apache or Nginx; do not expose
`server/data`.

1. Upload the project and point the site's document root at
   **`simple-max31855/server/public`**. Do not point it at `server/`.
2. Create `simple-max31855/server/data` and give the PHP user write permission:

   ```bash
   mkdir -p simple-max31855/server/data
   chown www-data:www-data simple-max31855/server/data   # use your PHP user
   chmod 775 simple-max31855/server/data
   ```

3. Set the ingest key in the PHP environment. In a hosting panel this is
   usually a “custom environment variable”/FastCGI parameter named
   `FURNACE_INGEST_KEY`; on Apache you can use `SetEnv FURNACE_INGEST_KEY ...`.
   Leave it empty only for a private/test host. Set the same value as
   `API_KEY` in the collector.

   Optionally set `FURNACE_DATA_DIR` to an absolute path outside the web root.
   The default is `server/data`.

4. Apache: allow `.htaccess` (`AllowOverride All`). Nginx: route requests to
   `index.php` and pass PHP to FPM, for example:

   ```nginx
   root /var/www/simple-max31855/server/public;
   index index.php;

   location / {
       try_files $uri $uri/ /index.php?$query_string;
   }

   location ~ \.php$ {
       include fastcgi_params;
       fastcgi_pass unix:/run/php/php8.3-fpm.sock;
       fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
   }
   ```

5. Open `https://temp.futurefounderco.com/`. The header should say
   **Connecting** until the collector posts the first batch, then **Live**.

> This app takes over the site root. If `temp.futurefounderco.com` currently
> serves another dashboard, back it up first or deploy this under a separate
> hostname/subdirectory. The collector must post to the same origin that serves
> this `index.php`.

### API

The collector uses the first endpoint; the browser uses the second:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/ingest` | Accept `{"samples":[...]}` with `X-Api-Key` |
| `GET` | `/api/recent?since=<id>&limit=600` | Samples newer than `id`, plus `last_id` and `total_samples` |
| `GET` | `/api/health` | Buffer count, total counter and latest id |

```bash
curl -s https://temp.futurefounderco.com/api/health
curl -s 'https://temp.futurefounderco.com/api/recent?since=0&limit=3'
```

`recent.json` keeps the newest 1,800 samples (three minutes at 10 Hz) and is
replaced atomically under a file lock. There is no long-term history. The UI
requests new samples every **150 ms** (every 1 s while the tab is hidden), keeps
the last 60 seconds and draws all three channels without a chart library. The
**Counter** is the total accepted sample count from the server, so it survives a
page reload. When a sample or channel is missing, the cards keep the last known
value marked **Held**, and the chart carries that value forward to `now` instead
of breaking or leaving a gap. A stalled request is retried after one second and
the previous points stay visible while reconnecting.

### Collector check

After deploying, run the collector once in the foreground. It should print
`Sent 5 samples` repeatedly and the page should move from *Connecting* to
*Live* within a second:

```bash
python3 collector/collector.py
```

If the page stays on *Connecting*, check `/api/health` first, then the collector
output. A `401` means `API_KEY` and `FURNACE_INGEST_KEY` differ. A Cloudflare
`403`/`1010` is an edge rule, not a PHP error; see `collector/README.md`.

## Test without hardware

```bash
python3 simple-max31855/test_logic.py -v
python3 -m unittest discover -s collector/tests -q
```

The first test covers the signed decoder, floating-bus rejection, fault bits,
the `-1000` sentinel and range boundaries. The second covers the collector's
3/4-channel parsing and checkpoint behaviour.
