# Noise and unstable thermocouple readings

Noise is usually a wiring or power problem, not a filter problem. Fix the
signal path first; software filtering hides a bad measurement and can make the
furnace look slower than it is.

## 1. Tell noise from a real fault

Read the fault bits in the connection log or the dashboard tooltip:

| Symptom | Meaning |
| --- | --- |
| `Open thermocouple` | Broken probe, loose screw terminal or open extension wire |
| `Short to GND` / `Short to VCC` | Damaged probe, moisture or a pinched lead |
| `Read error` | SPI wiring, power or a missing MAX31855 |
| A single wild value, then normal | Electrical spike or a floating junction |
| A slow saw-tooth / 50 Hz pattern | Mains pickup or a ground loop |

A grounded, mains-powered furnace can inject current through the thermocouple
sheath. If the reading changes when the heater switches, treat it as a grounding
problem before increasing the filter.

## 2. Wiring checklist

- Use **twisted, shielded K-type extension wire**; do not run it parallel to
  mains, SSR or contactor wiring. Cross noisy cables at 90° if crossing is
  unavoidable.
- Ground the shield at **one end only**, normally the MAX31855 enclosure, not at
  both ends.
- Keep the thermocouple junction isolated from the furnace body unless the
  probe is explicitly grounded. A floating junction picks up mains noise; a
  second ground creates a loop.
- Put a **100 nF ceramic** across MAX31855 VCC/GND close to the chip, plus a
  **10 µF** bulk capacitor. Feed the board from a clean 5 V supply, not the
  same rail as a relay or SSR.
- Add a small RC filter at the thermocouple input: **1 kΩ in series with T+ and
  T−, 0.1 µF across the MAX31855 input**. Keep the filter close to the chip.
- Use short SPI wires, a common ground with the ESP32-S3, and a ferrite bead on
  the probe cable if switching noise remains.
- For low-temperature work, a MAX31865 PT100/PT1000 4-wire probe is much less
  sensitive than a K-type on a MAX31855.

## 3. Firmware settings

The board takes a median of several reads and then an EMA. The admin panel
exposes both, plus the frame interval and read gap.

| Preset | Frame | Reads | Gap | EMA | Use |
| --- | ---: | ---: | ---: | ---: | --- |
| Fast | 50 ms | 3 | 3000 µs | 0.60 | Fast response, more visible noise |
| Balanced | 100 ms | 5 | 6000 µs | 0.35 | Default |
| Quiet | 250 ms | 9 | 12000 µs | 0.20 | Noisy wiring; slower response |

Rules of thumb:

1. Increase **reads** first (5 → 7 → 9), then the **gap**, then the **frame
   interval**.
2. Lower **EMA alpha** (0.35 → 0.2 → 0.1) to smooth more, but a furnace step
   will then take longer to appear.
3. Do not use a filter to hide an open thermocouple. A fault must stay a fault.
4. Keep the raw samples. The public dashboard's one-frame drop filter only
   changes what is displayed; CSV, database and analysis still contain the
   original readings.
5. If the noise is exactly one sample and large, the **admin → Public dashboard
   → Single-frame drop filter** is appropriate. Set the threshold (60% is a
   reasonable start) and verify that a real cool-down still appears on the next
   sample.

## 4. Verify the fix

1. Disconnect the heater and watch a cold furnace for 10 minutes; the reading
   should be stable to a few tenths of a degree.
2. Switch the heater/SSR on and off several times and watch for jumps at the
   switching edge.
3. Compare against a reference thermometer at a soak temperature and set the
   channel offset in **Furnaces → Calibration helper**.
4. Export a short CSV and check that spikes are present in the raw data. If
   they are, the display filter is masking a wiring problem, not fixing it.

Never rely on software filtering for over-temperature protection. The monitor
is not a safety-rated controller.
