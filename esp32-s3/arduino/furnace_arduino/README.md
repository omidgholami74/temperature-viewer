# `furnace_arduino` — the sketch folder

Open **this folder** (or the `.ino` inside it) in the Arduino IDE. The folder
name must stay `furnace_arduino` because the Arduino builder requires it to
match the sketch file name.

Before the first build, copy `config.example.h` to `config.h` in this same
folder and fill in the Wi-Fi and API settings. Without `config.h` the sketch
stops with `#error "Copy config.example.h to config.h …"` instead of building a
board that silently cannot connect.

```bash
cp config.example.h config.h
```

| File                  | What it does                                                      |
| --------------------- | ----------------------------------------------------------------- |
| `furnace_arduino.ino` | `setup()`/`loop()`: 10 Hz sampling, CSV echo, Wi-Fi, batch upload |
| `config.h`            | Your credentials and settings (git-ignored, not in the repository) |
| `config.example.h`    | Template with every option and its default                        |
| `furnace_math.h`      | MAX31855/MAX31865 decoding and the RTD (Callendar–Van Dusen) curve |
| `furnace_payload.h`   | Builds the `{"samples":[…]}` body without a JSON library          |
| `furnace_net.h`       | Wi-Fi association, background reconnect, NTP clock                |
| `furnace_ingest.h`    | Ring buffer plus HTTP(S) POST to `/api/ingest`                    |
| `max31855_sensor.h`   | Thermocouple driver: SCK 14, MISO 12, CS 15/16/17/18              |
| `max31865_sensor.h`   | RTD driver: SCK 14, MISO 12, **MOSI 11**, CS 15/16/17/18          |

Pick the sensor in `config.h`:

```c
// #define FURNACE_USE_MAX31865   // MAX31865 + PT100/PT1000 RTD
```

Comment it out for MAX31855 + K-type thermocouples. Pins, RTD settings and the
API behaviour are documented one level up in
[`../README.md`](../README.md).
