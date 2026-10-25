# MAX31865 variant — 4x PT100/PT1000 RTD on an ESP32-S3

Same firmware shape as the thermocouple variant, but for **resistance
thermometers**: platinum RTDs (PT100 or PT1000) read through MAX31865 boards.
That means a real SPI write line (the MAX31865 is a register device), a
2/3/4-wire lead compensation setting, and a reference resistor that must match
the probe.

```
ESP32-S3 ──SPI──┬── MAX31865 #1 (CS 15) ── PT100 probe 1
                ├── MAX31865 #2 (CS 16) ── PT100 probe 2
                ├── MAX31865 #3 (CS 17) ── PT100 probe 3
                └── MAX31865 #4 (CS 18) ── PT100 probe 4
        └── Wi-Fi ── POST /api/ingest ── dashboard
```

## Pin numbers

| ESP32-S3 pin | Signal          | MAX31865 board (all four) |
| ------------ | --------------- | ------------------------- |
| 3V3          | 3V3             | `VIN`                     |
| GND          | GND             | `GND`                     |
| GPIO14       | SCK (clock)     | `CLK`                     |
| **GPIO11**   | **MOSI** (SDI)  | `SDI`                     |
| GPIO12       | MISO (SDO)      | `SDO`                     |
| GPIO15       | CS              | `CS` of furnace 1         |
| GPIO16       | CS              | `CS` of furnace 2         |
| GPIO17       | CS              | `CS` of furnace 3         |
| GPIO18       | CS              | `CS` of furnace 4         |
| —            | IRQ (optional)  | `DRDY` — leave unconnected |

The defaults are the constants at the top of [`main.py`](main.py):

```python
SCK_PIN = 14
MOSI_PIN = 11
MISO_PIN = 12
CS_PINS = [15, 16, 17, 18]
```

GPIO11/12/13/14 are the ESP32-S3's default SPI2 (FSPI) pins, which is why this
variant uses them; servo or LED pins on a dev board may clash — move the
constants if yours does.

## Probe wiring on the MAX31865 board

The board has screw terminals `RTD+`, `RTD-`, `FORCE+`, `FORCE-` (some boards:
`RTDIN+`, `RTDIN-`, `FORCE+`, `FORCE-`) and 3/4-wire solder jumpers.

| Probe             | Terminals                                                     | `WIRE_MODE` | Board jumper |
| ----------------- | ------------------------------------------------------------- | ----------- | ------------ |
| 2-wire            | `RTD+`/`FORCE+` bridged, `RTD-`/`FORCE-` bridged; leads there  | `2`         | 2/3-wire (default) |
| 3-wire            | `RTD+`, `RTD-`, third lead to `FORCE-` (and `FORCE+`→`RTD+`)   | `3`         | 2/3-wire |
| 4-wire            | `RTD+`, `RTD-`, `FORCE+`, `FORCE-` as marked                 | `4`         | cut/solder the 4-wire jumper |

2-wire probes add the lead resistance to the reading (≈0.4 °C per 0.1 Ω with a
PT100), so a long 2-wire run reads hot. Use 3-wire for furnace probes, 4-wire
when accuracy matters most.

## Reference resistor and probe type

`R_REF` must match the resistor fitted on the board, and `R_NOMINAL` the probe:

| Probe  | `R_REF` | `R_NOMINAL` |
| ------ | ------- | ----------- |
| PT100  | `430.0` | `100.0`     |
| PT1000 | `4300.0`| `1000.0`    |

Most cheap boards ship the 430 Ω PT100 resistor. If the value does not match
the probe, `read()` returns a plausible looking but wrong temperature — the
ratio, not the absolute value, is what the driver computes.

`FILTER_HZ = 50` keeps the notch filter matched to Iran's 50 Hz mains, which
rejects most of the pickup on long probe leads. Use `60` only on a 60 Hz grid.

## Install

```bash
cp ../common/config.example.py /tmp/config.py      # edit Wi-Fi + API settings
mpremote connect COM6 \
  cp /tmp/config.py :config.py + \
  cp ../common/wifi.py :wifi.py + \
  cp ../common/ingest.py :ingest.py + \
  cp max31865.py :max31865.py + \
  cp main.py :main.py
mpremote connect COM6 repl
```

## Driver

[`max31865.py`](max31865.py) is self-contained (no `mip install`):

- `MAX31865(spi, cs, r_ref, r_nominal, wire_mode, filter_hz)` — one channel.
- `begin()` — bias on, continuous conversion, first reading after 100 ms.
- `read()` → `(celsius_or_None, fault_mask)`; `read_resistance()` → `(ohms, mask)`.
- `resistance_to_celsius(ohms, r_nominal)` — IEC 60751 Callendar–Van Dusen
  inverse: closed form above 0 °C, Newton iteration with the `C` term below it,
  so a cold furnace still reads correctly down to −200 °C.
- Fault handling: when the fault bit latches, the driver clears it (config bit
  `0x02`) so the next frame is not a permanent error, and reports the API mask:

  | MAX31865 fault bit | API bit | Meaning                     |
  | ------------------ | ------- | --------------------------- |
  | `0x04`             | 1       | open element / broken lead  |
  | `0x02`, `0x01`     | 2, 4    | lead short, over/under voltage |
  | `0x10`, `0x20/0x40`| 4, 8    | reference / threshold trip  |

## Accuracy notes

- A PT100 is about ten times less sensitive than a K-type thermocouple but far
  more linear and stable; ±0.5 °C is realistic with a 0.1 % reference resistor.
- Self-heating: the bias current is ~0.4 mA through a PT100 (≈0.15 mW). In still
  air that is negligible; in a probe pocket it is well below the sensor's limit.
- The board's `R_REF` tolerance is usually 0.1 %; for a real calibration offset,
  put the probe in ice water (0 °C) and boiling water (~100 °C at your altitude)
  and note the error. Correct it in the dashboard or subtract it from `read()`.

## Troubleshooting

| Symptom | Cause |
| ------- | ----- |
| Every channel ≈ −242 °C | Open input: no probe, broken lead, or `WIRE_MODE` not matching the jumper. |
| Reading is exactly `0.00` on all four | No SPI clock/CS traffic — check GPIO14/GPIO11/GPIO12 and the 3V3 rail. |
| Reading is ~10x too low (≈25 °C instead of 250) | PT100 probe with `R_REF = 4300`, or the reverse: check the resistor against the probe table. |
| Reading jumps by a few tenths between frames | 2-wire probe with long leads; switch to 3-wire, or enable `FILTER_HZ = 50`. |
| One channel always faults | That board's `R_REF` or solder jumper; swap the board to confirm. |
| `f` = 8 on the dashboard | Threshold trip (`0x20`/`0x40`) — usually a probe shorted against the sheath. |
