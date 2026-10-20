# Copy this file to config.py on the Pico and edit the values.
# config.py is ignored by git so credentials never reach the repository.

# --- WiFi (Pico W only) --------------------------------------------------
WIFI_SSID = "your-wifi"
WIFI_PASSWORD = "your-password"

# --- PHP backend ---------------------------------------------------------
# Use the LAN address of the machine running Apache / php -S.
API_BASE = "http://192.168.1.50:8080"
# Must match FURNACE_INGEST_KEY on the server. Leave empty if it is disabled.
API_KEY = ""

# --- SPI wiring (MAX31855) ----------------------------------------------
# SCK and MISO are shared by all four amplifiers; each amplifier has its own CS.
SPI_ID = 0
SCK_PIN = 6
MISO_PIN = 4
CS_PINS = (5, 7, 9, 11)
SPI_BAUD_RATE = 1_000_000

# --- Sampling and upload -------------------------------------------------
SAMPLE_INTERVAL_MS = 100   # 10 samples per second, per channel
POST_INTERVAL_MS = 1000    # upload one batch per second
MAX_BUFFERED_SAMPLES = 600 # 60 s of offline buffering
