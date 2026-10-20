# Pico firmware — 4x MAX31855 at 10 Hz

Firmware for a **plain Raspberry Pi Pico** (no WiFi) with four MAX31855
thermocouple amplifiers on one SPI bus, each with its own chip-select pin.

The board has no network stack and MicroPython for the Pico ships no HTTP
client, so the firmware uses **only built-in modules** (`machine`, `time`,
`os`, `ujson`) and never uploads anything. Each sample is printed as one JSON
line on USB serial and optionally appended to a log file on the Pico's flash.
A PC then runs [`collector/collector.py`](../collector/README.md), which saves
those lines in a file on the computer and POSTs them to the PHP API at
`https://apitemp.futurefounderco.com`.

## Files

| File               | Runs on | Purpose                                              |
| ------------------ | ------- | ---------------------------------------------------- |
| `max31855.py`      | Pico    | SPI driver, signed 14-bit read, fault decoding       |
| `sampler.py`       | Pico    | Drift-free 100 ms sampling scheduler                 |
| `hardware.py`      | Pico    | Shared SPI bus setup                                 |
| `main.py`          | Pico    | Sample loop: JSON line to serial and to the log file |
| `check.py`         | Pico    | One-shot wiring check: raw frames and fault names    |
| `config.example.py`| —       | Copy to `config.py` and edit; `config.py` is ignored |
| `simulator.py`     | PC      | Sends fake furnace data to the API (no hardware)     |

No `network`, `urequests`, `requests` or API key is used on the Pico. The
collector owns the HTTP side and the ingest key.

## Wiring

Use **3.3 V** for VIN: the MAX31855 is a 3.3 V part and the Pico inputs are not
5 V tolerant.

| MAX31855 | Pico pin (physical)            | Notes                          |
| -------- | ------------------------------ | ------------------------------ |
| VIN      | 3V3(OUT) — pin 36              | 3.3 V, add 0.1 µF at the board |
| GND      | GND — pin 38                   | Common ground for all four     |
| DO       | GP4 — pin 6                    | Shared MISO                    |
| CLK      | GP6 — pin 9                    | Shared SCK                     |
| CS       | GP5 — pin 7                    | Furnace 1                      |
| CS       | GP7 — pin 10                   | Furnace 2                      |
| CS       | GP9 — pin 12                   | Furnace 3                      |
| CS       | GP11 — pin 15                  | Furnace 4                      |
| 3Vo      | not connected                  | Internal 3.3 V reference       |

Keep the SPI wires short (under ~30 cm) and give each amplifier a decoupling
capacitor close to VIN/GND. Long thermocouple leads should be shielded or
twisted, with the shield grounded at one end only.

## Firmware setup

1. Flash **MicroPython for the Raspberry Pi Pico** (not Pico W) with Thonny.
2. Copy `config.example.py` to `config.py` and edit the pins and output
   options.
3. Copy `config.py`, `hardware.py`, `max31855.py`, `sampler.py` and `main.py`
   to the Pico (Thonny: *Save as* → *Raspberry Pi Pico*).
4. Run `main.py`. It can be saved as `main.py` on the board, which is the file
   MicroPython runs at boot.

The board prints a short banner, then one line per sample:

```
# furnace monitor: 4 channels every 100 ms
# serial: one JSON sample per line, log file: samples.jsonl
{"u": 0, "t": [781.25, 660.0, 940.5, null], "f": [0, 0, 0, 1]}
{"u": 100, "t": [781.25, 660.0, 940.5, null], "f": [0, 0, 0, 1]}
```

### Flash log

With `LOG_TO_FLASH = True` the same lines are appended to `samples.jsonl` on
the Pico. When the file reaches `MAX_LOG_BYTES` (1 MB ≈ 25 minutes at 10 Hz) it
is renamed to `samples.jsonl.old` and a fresh file starts, so the most recent
data is always available even if the PC was off.

Writing 600 B/s to flash does wear it slowly. If the PC is always connected,
set `LOG_TO_FLASH = False` and keep `SERIAL_ECHO = True`.

To upload a copied log, copy `samples.jsonl` from the Pico to the PC (Thonny,
or `mpremote cp :samples.jsonl samples.jsonl`) and run the collector in
`SOURCE = "file"` mode — see [`collector/README.md`](../collector/README.md).

## Configuration

| Setting               | Default          | Meaning                                   |
| --------------------- | ---------------- | ----------------------------------------- |
| `SPI_ID`              | `0`              | RP2040 SPI peripheral                     |
| `SCK_PIN`             | `6`              | Shared clock                              |
| `MISO_PIN`            | `4`              | Shared data input                         |
| `CS_PINS`             | `(5,7,9,11)`     | One chip select per furnace               |
| `SPI_BAUD_RATE`       | `1_000_000`      | 1 MHz is reliable on short wires          |
| `SAMPLE_INTERVAL_MS`  | `100`            | 10 samples per second                     |
| `SERIAL_ECHO`         | `True`           | Print one JSON line per sample on USB     |
| `LOG_TO_FLASH`        | `True`           | Also keep a log file on the Pico          |
| `LOG_FILE`            | `samples.jsonl`  | Log path on the Pico filesystem           |
| `MAX_LOG_BYTES`       | `1_048_576`      | Rotate the log above this size            |

## Data protocol

Each sample is a JSON object:

```json
{ "u": 12300, "t": [781.25, null, 940.0, 545.75], "f": [0, 1, 0, 0] }
```

- `u` — milliseconds since the firmware started. The Pico has no clock, so
  this is all it can report; the collector adds an absolute `at` time before
  uploading, which is what the API stores.
- `t` — channel temperatures; `null` means the sensor reported a fault.
- `f` — fault bit masks:

| Bit | Meaning           |
| --- | ----------------- |
| 1   | Open thermocouple |
| 2   | Short to GND      |
| 4   | Short to VCC      |
| 8   | SPI read error    |

The collector batches these lines, stamps each one with an absolute time, and
posts them to `POST /api/ingest` (100 samples per request by default). Failed
uploads stay in the file on the PC and are retried, so the Pico never has to
buffer or retry network requests.

### Timing notes

`Sampler` advances its next slot from the previous slot, not from `sleep()`, so
read jitter does not accumulate. On a plain Pico the loop only does four SPI
reads, one JSON dump, one serial write and (optionally) one flash append per
100 ms, which leaves ample headroom. A long serial stall (for example a USB
host that stops reading) shows up as skipped slots in the chart.

## Collector

```bash
python -m pip install -r collector/requirements.txt
python collector/collector.py
```

Settings such as the serial port and `API_URL` are constants at the top of
[`collector/collector.py`](../collector/collector.py); there are no
command-line arguments. See [`collector/README.md`](../collector/README.md)
for file mode, checkpoints and retry behaviour.

## Simulator

For dashboard work without hardware, the PC simulator posts fake data straight
to the API:

```bash
python3 module/simulator.py
```

It takes no arguments either; edit `API_URL`, `API_KEY`, `SAMPLE_INTERVAL_S`
and `POST_INTERVAL_S` at the top of the file.

It generates four furnace curves at 10 Hz, posts one batch per second and
prints each accepted batch. A short open-thermocouple event is injected on
channel 4 every five minutes so the fault styling can be checked. Point it at
the local backend for tests; pointing it at production writes demo data into
the real firing history.

## Hardware check

When samples show `null` temperatures, run `check.py` on the board. It prints
the files on the Pico and three raw frames per channel:

```
channel 1 raw=0x00000001 probe=--      internal=24.00 fault=1 (open thermocouple)
channel 2 raw=0x00000000 probe=--      internal=--    fault=8 (SPI read error)
```

Reading the frames:

- `internal` is the MAX31855's own die temperature. A plausible value (for
example 24 °C) proves SPI and the CS pin work.
- `probe` is only shown when the fault bits are zero; `main.py` stores `null`
instead of a value the chip itself flagged.
- `fault=1` open thermocouple — the probe is not connected, loose in the screw
terminal, or the lead is broken. Refit the probe and check with a multimeter.
- `fault=2` / `fault=4` — T+ or T− shorted to GND or VCC.
- `fault=8` (raw `0x00000000` or `0xFFFFFFFF`) — no chip answers on that CS
pin: wrong CS GPIO, missing 3.3 V, missing ground, or no board fitted yet.

## Troubleshooting

| Symptom                          | Check                                                   |
| -------------------------------- | ------------------------------------------------------- |
| `Read error` (bit 8) on a channel | CS pin, MISO continuity, sensor VIN, common ground     |
| `Open thermocouple` (bit 1)       | Probe seated in T+/T−, unbroken lead, correct channel  |
| All channels read the same value  | Two sensors share a CS pin                              |
| No lines on serial                | Pico not running `main.py`, wrong COM port, baud 115200 |
| `OSError` when opening the log    | `LOG_FILE` path, free space on the Pico                 |
| Collector keeps skipping lines    | Another program is holding the serial port              |
| Values drift from a reference     | Cold-junction temperature, probe type, probe immersion  |

## Safety

The MAX31855 and this firmware are monitoring tools, not a safety function.
Use an independent hardware over-temperature cut-out, isolate or properly
ground probes that touch live furnace hardware, and keep mains wiring away
from the low-voltage side.
