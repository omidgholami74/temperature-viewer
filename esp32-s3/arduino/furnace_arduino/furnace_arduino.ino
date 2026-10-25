// Furnace monitor — ESP32 / ESP32-S3, four channels, straight to the API.
//
// The C++ twin of ../../max31855/main.py and ../../max31865/main.py:
// samples four sensors at 10 Hz, prints the same `ms,f1,f2,f3,f4` line on USB
// serial, and POSTs the batches to /api/ingest itself. No PC, no collector.
//
// Wiring and pins are in ../README.md; pick the sensor board with
// `#define FURNACE_USE_MAX31865` in config.h.
//
// Copy config.example.h to config.h first: the sketch refuses to build without
// it, so a Wi-Fi password never ends up in the repository.

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and edit it (Wi-Fi, API_URL, API_KEY)."
#endif

#include <Arduino.h>
#include <SPI.h>

#include "furnace_ingest.h"
#include "furnace_math.h"
#include "furnace_net.h"

#ifdef FURNACE_USE_MAX31865
#include "max31865_sensor.h"
using SensorArray = Max31865Array;
#else
#include "max31855_sensor.h"
using SensorArray = Max31855Array;
#endif

namespace {

constexpr uint32_t kSampleIntervalMs = 100;  // 10 Hz, same as the Pico firmware
constexpr float kFaultSentinel = -1000.0f;   // CSV value for a failed channel
constexpr uint32_t kReportIntervalMs = 60000;
constexpr size_t kPayloadCapacity = BATCH_SIZE * furnace::kMaxSampleJson + 64;

SensorArray sensors;
furnace::Ingest<BUFFER_LIMIT, BATCH_SIZE, kPayloadCapacity> ingest;

uint32_t startMillis = 0;
int64_t epochBaseMs = 0;   // wall clock at boot, minus startMillis
bool clockOk = false;
uint32_t nextTick = 0;
uint32_t lastNetCheck = 0;
uint32_t lastReport = 0;

/** The CSV shows a fault as -1000, like module/ and the MicroPython firmware. */
float csvValue(const furnace::Sample& sample, uint8_t channel) {
  return sample.fault[channel] != 0 ? kFaultSentinel : sample.celsius[channel];
}

void readAllChannels(furnace::Sample* sample) {
  for (uint8_t channel = 0; channel < SensorArray::kChannels; ++channel) {
    sensors.read(channel, &sample->celsius[channel], &sample->fault[channel]);
  }
}

}  // namespace

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);

  Serial.println();
  Serial.println(F("Furnace monitor: 4 channels, direct to /api/ingest"));

  sensors.begin();

  const bool online = furnace::connectWifi(WIFI_SSID, WIFI_PASSWORD, WIFI_HOSTNAME, WIFI_TIMEOUT_MS);

#if NTP_ENABLED
  if (online) {
    clockOk = furnace::syncClock(NTP_SERVER, NTP_TIMEOUT_MS);
  }
#endif

  ingest.begin(API_URL, API_KEY, API_TLS_VERIFY, API_ROOT_CA, API_TIMEOUT_MS);

  startMillis = millis();
  epochBaseMs = clockOk ? static_cast<int64_t>(time(nullptr)) * 1000 - static_cast<int64_t>(startMillis) : 0;

  if (!clockOk) {
    Serial.println(F("# no clock: samples are stamped by the server on arrival"));
  }

#if SERIAL_ECHO
  Serial.println(F("ms,furnace1,furnace2,furnace3,furnace4"));
#endif

  nextTick = millis();
  lastNetCheck = millis();
  lastReport = millis();
}

void loop() {
  furnace::Sample sample;
  sample.uptimeMs = millis() - startMillis;
  sample.hasAt = clockOk;
  sample.atMs = clockOk ? epochBaseMs + static_cast<int64_t>(millis() - startMillis) : 0;

  readAllChannels(&sample);

#if SERIAL_ECHO
  Serial.printf("%lu,%.2f,%.2f,%.2f,%.2f\n",
                static_cast<unsigned long>(sample.uptimeMs),
                csvValue(sample, 0), csvValue(sample, 1),
                csvValue(sample, 2), csvValue(sample, 3));
#endif

  // Sends by itself once a full batch is queued; a failed upload keeps the
  // batch and is retried at the next batch.
  ingest.add(sample);

  const uint32_t now = millis();

  // A dropped link is retried in the background: sampling never stops.
  if (WiFi.status() != WL_CONNECTED && now - lastNetCheck >= WIFI_RETRY_MS) {
    lastNetCheck = now;
    Serial.println(F("Wi-Fi: reconnecting"));
    furnace::startWifiReconnect(WIFI_SSID, WIFI_PASSWORD);
  } else if (WiFi.status() == WL_CONNECTED && !clockOk) {
#if NTP_ENABLED
    if (now - lastNetCheck >= WIFI_RETRY_MS) {
      lastNetCheck = now;

      if (furnace::syncClock(NTP_SERVER, NTP_TIMEOUT_MS)) {
        clockOk = true;
        epochBaseMs = static_cast<int64_t>(time(nullptr)) * 1000 - static_cast<int64_t>(millis() - startMillis);
      }
    }
#endif
  }

  if (now - lastReport >= kReportIntervalMs) {
    lastReport = now;
    ingest.printStats();
  }

  // Keep the 10 Hz grid. A slow upload stretches the interval instead of
  // piling up a backlog of missed slots.
  nextTick += kSampleIntervalMs;
  const int32_t wait = static_cast<int32_t>(nextTick - millis());

  if (wait > 0) {
    delay(static_cast<uint32_t>(wait));
  } else {
    nextTick = millis();
  }
}
