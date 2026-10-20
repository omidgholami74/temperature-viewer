# Pico firmware — 4x MAX31855 at 10 Hz

Firmware, a USB bridge and a simulator for the furnace monitor. All four
thermocouples share one SPI bus and each MAX31855 has its own chip-select pin.

## Files

| File               | Runs on | Purpose                                              |
| ------------------ | ------- | ---------------------------------------------------- |
| `max31855.py`      | Pico    | SPI driver, signed 14-bit read, fault decoding       |
| `sampler.py`       | Pico    | Drift-free 100 ms sampling scheduler                 |
| `hardware.py`      | Pico    | Shared SPI bus setup                                 |
| `api_client.py`    | Pico W  | Batched HTTP upload with retry                       |
| `main.py`          | Pico W  | WiFi firmware: sample, buffer, upload                |
| `main_usb.py`      | Pico    | USB firmware: one JSON sample per serial line        |
| `bridge.py`        | PC      | Reads the USB stream and posts it to the PHP API     |
| `simulator.py`     | PC      | Sends fake furnace data to the API (no hardware)     |
| `config.example.py`| —       | Copy to `config.py` and edit; `config.py` is ignored |

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

1. Flash MicroPython for **Pico W** (WiFi mode) or Pico (USB mode) with Thonny.
2. Copy `config.example.py` to `config.py` and edit WiFi, `API_BASE`, pins and
   `API_KEY`.
3. Copy `config.py`, `hardware.py`, `max31855.py`, `sampler.py` and either
   `main.py` (WiFi) or `main_usb.py` (USB) to the Pico.
4. Run the file in Thonny. To start automatically, save it as `main.py`.

The Pico W only joins **2.4 GHz** networks. `API_BASE` must be the LAN address
of the PHP server, for example `http://192.168.1.50:8080` — not `localhost`.

### USB mode

If the Pico is not a Pico W, run `main_usb.py` on it and `bridge.py` on a PC
that can reach the backend:

```bash
python -m pip install -r module/requirements.txt
python module/bridge.py --url http://127.0.0.1:8080 --port COM5 --baud 115200
```

The bridge auto-detects common Pico serial ports when `--port` is omitted.

## Configuration

| Setting               | Default | Meaning                                   |
| --------------------- | ------- | ----------------------------------------- |
| `WIFI_SSID`           | —       | 2.4 GHz network name (Pico W)             |
| `WIFI_PASSWORD`       | —       | Network password                          |
| `API_BASE`            | —       | Backend base URL, no trailing slash       |
| `API_KEY`             | `""`    | Must equal `FURNACE_INGEST_KEY` when set   |
| `SPI_ID`              | `0`     | RP2040 SPI peripheral                     |
| `SCK_PIN`             | `6`     | Shared clock                              |
| `MISO_PIN`            | `4`     | Shared data input                         |
| `CS_PINS`             | `(5,7,9,11)` | One chip select per furnace          |
| `SPI_BAUD_RATE`       | `1_000_000` | 1 MHz is reliable on short wires      |
| `SAMPLE_INTERVAL_MS`  | `100`   | 10 samples per second                     |
| `POST_INTERVAL_MS`    | `1000`  | Upload one batch per second               |
| `MAX_BUFFERED_SAMPLES`| `600`   | About 60 s of offline buffering           |

## Data protocol

Each sample is a JSON object:

```json
{ "u": 12300, "t": [781.25, null, 940.0, 545.75], "f": [0, 1, 0, 0] }
```

- `u` — milliseconds since the firmware started. Only differences within a
  batch are used, so a reboot never produces wrong timestamps.
- `t` — channel temperatures; `null` means the sensor reported a fault.
- `f` — fault bit masks:

| Bit | Meaning           |
| --- | ----------------- |
| 1   | Open thermocouple |
| 2   | Short to GND      |
| 4   | Short to VCC      |
| 8   | SPI read error    |

Batches are posted to `POST /api/ingest`, ten samples at a time by default.
Failed uploads stay in a RAM buffer (last 600 samples) and are retried on the
next cycle; a lost response can therefore store a duplicate frame
(at-least-once delivery).

### Timing notes

`Sampler` advances its next slot from the previous slot, not from `sleep()`, so
read jitter does not accumulate. An HTTP request blocks the single-core Pico
for its duration; on a LAN a one-second batch typically costs 50–150 ms, which
fits inside the 200 ms catch-up window. If uploads regularly take longer, some
sampling slots are skipped (the gap is visible in the chart) — raise
`POST_INTERVAL_MS`, shorten the network path or accept a lower effective rate.

## Simulator

```bash
python3 module/simulator.py --url http://127.0.0.1:8080
python3 module/simulator.py --url http://127.0.0.1:8080 --key change-me --post-every 2
```

It generates four furnace curves at 10 Hz, posts one batch per second and
prints each accepted batch. A short open-thermocouple event is injected on
channel 4 every five minutes so the fault styling can be checked.

## Troubleshooting

| Symptom                        | Check                                                     |
| ------------------------------ | --------------------------------------------------------- |
| `Read error` (bit 8) on a channel | CS pin, MISO continuity, sensor VIN, common ground     |
| All channels read the same value | Two sensors share a CS pin                             |
| `API 401`                      | `API_KEY` does not match `FURNACE_INGEST_KEY`             |
| `API unreachable`              | `API_BASE` uses the PC's LAN IP, firewall allows port 8080 |
| WiFi timeout                   | 2.4 GHz network, correct password, signal strength        |
| Values drift from a reference  | Cold-junction temperature, probe type, probe immersion    |

## Safety

The MAX31855 and this firmware are monitoring tools, not a safety function.
Use an independent hardware over-temperature cut-out, isolate or properly
ground probes that touch live furnace hardware, and keep mains wiring away
from the low-voltage side.
