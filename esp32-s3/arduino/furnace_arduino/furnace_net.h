// Wi-Fi association and clock sync for the Arduino/C++ firmware.
#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

namespace furnace {

// 2025-01-01: anything below this means the clock was never set.
constexpr time_t kClockFloor = 1735689600;

inline bool clockIsSet() {
  return time(nullptr) > kClockFloor;
}

inline void logWifiState() {
  Serial.printf("Wi-Fi: connected, ip=%s gateway=%s\n",
                WiFi.localIP().toString().c_str(),
                WiFi.gatewayIP().toString().c_str());
}

/** Join the network once at boot. Returns false when it did not associate. */
inline bool connectWifi(const char* ssid, const char* password, const char* hostname, uint32_t timeoutMs) {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(hostname);
  WiFi.begin(ssid, password);

  const uint32_t start = millis();

  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > timeoutMs) {
      Serial.printf("Wi-Fi: no association after %lu ms; sampling continues, uploads wait\n",
                    static_cast<unsigned long>(timeoutMs));
      return false;
    }

    delay(200);
  }

  // Modem sleep adds up to ~200 ms of latency, which is a whole batch at 10 Hz.
  WiFi.setSleep(false);
  logWifiState();
  return true;
}

/**
 * Start a re-association without waiting for it.
 *
 * Waiting here would stop the 10 Hz sampling loop for the whole timeout, so
 * the loop only kicks the attempt off and checks `WiFi.status()` next pass.
 */
inline void startWifiReconnect(const char* ssid, const char* password) {
  WiFi.disconnect();
  WiFi.begin(ssid, password);
}

/** Set the RTC from NTP. Returns true when the clock is trustworthy. */
inline bool syncClock(const char* server, uint32_t timeoutMs) {
  if (clockIsSet()) {
    return true;
  }

  configTime(0, 0, server);

  const uint32_t start = millis();

  while (!clockIsSet()) {
    if (millis() - start > timeoutMs) {
      Serial.println(F("NTP: sync failed"));
      return false;
    }

    delay(100);
  }

  time_t now = time(nullptr);
  struct tm parts;
  gmtime_r(&now, &parts);

  Serial.printf("NTP: clock set to %04d-%02d-%02d %02d:%02d:%02d UTC\n",
                parts.tm_year + 1900, parts.tm_mon + 1, parts.tm_mday,
                parts.tm_hour, parts.tm_min, parts.tm_sec);

  return true;
}

}  // namespace furnace
