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
API_URL = "https://apitemp.futurefounderco.com"
API_KEY = "the same value as FURNACE_INGEST_KEY"
DATA_DIR = "/var/lib/furnace"
```

Then:

```bash
python3 -m pip install -r collector/requirements.txt
python3 collector/collector.py
```

It detects the CH343/ESP32 serial port when `SERIAL_PORT` is empty, appends each
sample to `DATA_DIR/samples.jsonl`, and uploads it in batches. The file and
`checkpoint.json` are on the host. A serial disconnect only pauses capture; a
network outage only delays upload, because the file remains on disk.

The collector already accepts this 3-channel row. A 4-channel board (four pins)
is accepted as well.

## Test without hardware

```bash
python3 simple-max31855/test_logic.py -v
python3 -m unittest discover -s collector/tests -q
```

The first test covers the signed decoder, floating-bus rejection, fault bits,
the `-1000` sentinel and range boundaries. The second covers the collector's
3/4-channel parsing and checkpoint behaviour.
