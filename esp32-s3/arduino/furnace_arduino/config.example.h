// Copy this file to config.h in the same folder and edit it.
// config.h is ignored by git, so your Wi-Fi password and ingest key stay local.
#pragma once

// --- Which sensor board is wired up --------------------------------------
// Uncomment exactly one. Commented out means MAX31855 + K-type thermocouples.
// #define FURNACE_USE_MAX31865   // MAX31865 + PT100/PT1000 RTD

// --- Wi-Fi (2.4 GHz only: the ESP32-S3 has no 5 GHz radio) ---------------
#define WIFI_SSID "your-network"
#define WIFI_PASSWORD "your-password"
#define WIFI_HOSTNAME "furnace-esp32"
#define WIFI_TIMEOUT_MS 20000   // give up waiting for an association after this
#define WIFI_RETRY_MS 15000     // re-association attempt interval at runtime

// --- API (the board talks to the PHP API directly) -----------------------
// Full endpoint, not just the host: HTTPClient needs the whole URL. Use the API
// host, never the dashboard.
#define API_URL "https://apitemp.futurefounderco.com/api/ingest"
// Must equal FURNACE_INGEST_KEY on the server; empty when the API is open.
#define API_KEY ""
#define API_TIMEOUT_MS 5000

// TLS. false = encrypted but the certificate is not checked (the ESP32 root
// store is small and a missing CA would silently block the upload). true needs
// API_ROOT_CA below, for example the ISRG Root X1 PEM of Let's Encrypt.
// Uploads keep the connection open between batches either way, so the TLS
// handshake is paid once instead of on every request.
#define API_TLS_VERIFY false
#define API_ROOT_CA ""

// --- Batching ------------------------------------------------------------
// One request carries BATCH_SIZE samples: 10 at 10 Hz means the server sees
// each sample about one second after it was read, and ten requests a second
// fewer than a batch of 1 would need. Bigger = fewer requests but later data.
// The API accepts at most 600 samples per request.
#define BATCH_SIZE 10
// Samples kept while the API is unreachable: 600 are one minute at 10 Hz.
// Each queued sample costs ~40 bytes of RAM.
#define BUFFER_LIMIT 600

// --- Time ----------------------------------------------------------------
// Absolute timestamps keep the history correct after a reboot. Off (or when
// NTP is unreachable) the board sends no time and the server stamps the batch
// on arrival, which is fine for live viewing only.
#define NTP_ENABLED true
#define NTP_SERVER "pool.ntp.org"
#define NTP_TIMEOUT_MS 8000

// --- Console -------------------------------------------------------------
// Also print the sample on USB serial, same CSV as module/ and the
// MicroPython firmware, so Thonny, Serial Studio and serial-web keep working.
#define SERIAL_ECHO true
#define SERIAL_BAUD 115200

// --- Pin overrides (optional) -------------------------------------------
// The defaults live in max31855_sensor.h / max31865_sensor.h:
//   MAX31855: SCK 14, MISO 12,        CS 15 16 17 18   (no MOSI)
//   MAX31865: SCK 14, MISO 12, MOSI 11, CS 15 16 17 18
// Uncomment and change here when your board uses other pins.
// #define MAX31855_SCK_PIN 14
// #define MAX31855_MISO_PIN 12
// #define MAX31855_CS_PINS {15, 16, 17, 18}
// #define MAX31865_SCK_PIN 14
// #define MAX31865_MISO_PIN 12
// #define MAX31865_MOSI_PIN 11
// #define MAX31865_CS_PINS {15, 16, 17, 18}

// --- RTD settings (MAX31865 only) ---------------------------------------
// R_REF is the reference resistor on the breakout: 430 ohm for a PT100,
// 4300 ohm for a PT1000. Set WIRE_MODE to match how the probe is wired.
#define MAX31865_RREF 430.0
#define MAX31865_RNOMINAL 100.0
#define MAX31865_WIRE_MODE 3   // 2, 3 or 4
#define MAX31865_FILTER_HZ 50  // Iran's mains frequency: keep the 50 Hz notch
