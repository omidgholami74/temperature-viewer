# Copy this file to config.py on the Pico and edit the values.
# config.py is ignored by git.
#
# This firmware is for a plain Pico (no WiFi). There is no network, no
# urequests and no API key here: the PC-side collector sends the data.

# --- SPI wiring (MAX31855) ----------------------------------------------
# SCK and MISO are shared by all four amplifiers; each amplifier has its own CS.
SPI_ID = 0
SCK_PIN = 6
MISO_PIN = 4
CS_PINS = (5, 7, 9, 11)
SPI_BAUD_RATE = 1_000_000

# --- Sampling ------------------------------------------------------------
SAMPLE_INTERVAL_MS = 100   # 10 samples per second, per channel

# --- Output --------------------------------------------------------------
# One JSON sample per line on USB serial. collector/collector.py reads it.
SERIAL_ECHO = True

# Also keep a copy on the Pico's flash, so a serial dropout does not lose data.
# Set to False if the PC is always connected (less flash wear).
LOG_TO_FLASH = True
LOG_FILE = "samples.jsonl"
# 1 MB is roughly 25 minutes at 10 Hz. When the log reaches this size it is
# renamed to LOG_FILE + ".old" and a fresh file is started.
MAX_LOG_BYTES = 1_048_576
