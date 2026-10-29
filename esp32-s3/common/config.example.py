# Copy this file to config.py on the ESP32-S3 and edit the values.
# config.py is ignored by git.

# --- Wi-Fi (2.4 GHz only: the ESP32-S3 has no 5 GHz radio) ---------------
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
WIFI_HOSTNAME = "furnace-esp32"   # shown by the router's client list
WIFI_TIMEOUT_S = 20               # give up waiting for an association after this
WIFI_RETRY_S = 15                 # re-association attempt interval at runtime

# --- API (the board talks to the PHP API directly) -----------------------
# No PC in the middle: the firmware POSTs to /api/ingest itself.
# Use the API host, not the dashboard host. "/api/ingest" is appended, so a
# URL that already ends in a path (a proxy) is used exactly as written.
API_URL = "https://apitemp.futurefounderco.com"
# Must equal FURNACE_INGEST_KEY on the server; empty when the API is open.
API_KEY = ""
API_TIMEOUT_S = 8

# One request carries BATCH_SIZE samples: 10 at 10 Hz means the server has
# each sample about a second after it was read. The socket is kept open between
# batches, so a smaller batch does not cost a TLS handshake every time.
# The API accepts at most 600 samples per request.
BATCH_SIZE = 10
# Send from a background thread, so the 100 ms sampling grid never waits for a
# request (or a TLS handshake, which costs 0.5-1 s on this board). The counter
# keeps sampling while a batch is in flight. Set False to send inline again.
BACKGROUND_UPLOAD = True
# Samples kept while the API is unreachable: 600 samples are one minute at
# 10 Hz. Beyond this the oldest samples are dropped, so a long outage cannot
# exhaust the board's RAM.
BUFFER_LIMIT = 600

# --- Time ----------------------------------------------------------------
# Real timestamps make the history correct even after a reboot, and they are
# what the dashboard's date pickers rely on. With NTP off (or unreachable) the
# board sends no time at all and the server stamps the batch on arrival, which
# is fine for live viewing but shifts after a backfill.
NTP_ENABLED = True
NTP_HOST = "pool.ntp.org"
NTP_TIMEOUT_S = 6

# --- Unattended operation (a USB charger, no computer) --------------------
# Seconds without a heartbeat before the hardware watchdog reboots the board.
# A board on a charger has no console, so a hung SPI read or a stuck network
# call has to recover by itself. Keep this above WIFI_TIMEOUT_S (a reconnect
# blocks the loop) and above NTP_TIMEOUT_S. 0 disables the watchdog.
WATCHDOG_S = 30

# --- Console -------------------------------------------------------------
# Also print the sample on USB serial (same CSV as module/), so the board can
# be watched in Thonny, Serial Studio or serial-web while it uploads. On a
# charger nobody reads it: False saves a little power and keeps the log clean.
# It goes to UART0 (the CH343 port), which never blocks when nothing listens;
# on a board whose console is the native USB-Serial-JTAG, set it to False
# before running without a PC.
SERIAL_ECHO = True

# --- Wi-Fi security note -------------------------------------------------
# TLS_VERIFY = True makes the firmware validate the server certificate. The
# MicroPython ssl module ships a small root store, so a certificate issued by
# a CA that is not in it fails the handshake. Keep False if the board cannot
# connect to https and prefer an ingest key plus a private network; set True
# when the server's CA is in the bundle (or served through a pinned proxy).
TLS_VERIFY = False
