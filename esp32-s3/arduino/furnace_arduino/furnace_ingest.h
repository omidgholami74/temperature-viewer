// Batch queue and HTTP(S) uploader for POST /api/ingest.
//
// The board is the collector here: samples are buffered, serialised with
// furnace_payload.h and POSTed over Wi-Fi, so no PC sits next to the furnace.
#pragma once

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>              // defines WiFiClient (NetworkClient in core 3.x)
#include <WiFiClientSecure.h>

#include <cstring>

#include "furnace_payload.h"

namespace furnace {

constexpr size_t kMaxSamplesPerRequest = 600;  // the API answers 413 above this

/**
 * Fixed-size ring buffer of samples plus the HTTP client that drains it.
 *
 * A failed upload keeps the batch for the next flush; a refused payload (400,
 * 413, 422) is dropped instead of blocking the queue forever, and beyond
 * `Capacity` the oldest samples are dropped so RAM cannot fill up.
 */
template <size_t Capacity, size_t BatchSize, size_t PayloadCapacity>
class Ingest {
 public:
  static_assert(Capacity >= 1, "BUFFER_LIMIT must be at least 1");
  static_assert(BatchSize >= 1 && BatchSize <= kMaxSamplesPerRequest,
                "BATCH_SIZE must be 1..600: the API rejects a larger batch");
  static constexpr size_t kFixedBatch = BatchSize < kMaxSamplesPerRequest ? BatchSize : kMaxSamplesPerRequest;
  static_assert(PayloadCapacity > kFixedBatch * kMaxSampleJson,
                "payload buffer is too small for one batch");

  void begin(const char* url, const char* apiKey, bool verifyTls, const char* rootCa, uint32_t timeoutMs) {
    url_ = url;
    apiKey_ = apiKey;
    verifyTls_ = verifyTls;
    rootCa_ = rootCa;
    timeoutMs_ = timeoutMs;
    warnedInsecure_ = false;
  }

  /** Queue one frame; flushes automatically when the batch is full. */
  void add(const Sample& sample) {
    if (count_ >= Capacity) {
      head_ = (head_ + 1) % Capacity;
      --count_;
      ++dropped_;
      Serial.printf("API: buffer full, dropped the %lu oldest sample(s)\n",
                    static_cast<unsigned long>(dropped_));
    }

    buffer_[(head_ + count_) % Capacity] = sample;
    ++count_;

    if (count_ >= BatchSize) {
      flush();
    }
  }

  /** Send everything queued, one request per batch. Returns true when empty. */
  bool flush() {
    while (count_ > 0) {
      const size_t chunk = count_ < kFixedBatch ? count_ : kFixedBatch;

      if (!send(chunk)) {
        break;
      }

      head_ = (head_ + chunk) % Capacity;
      count_ -= chunk;
    }

    return count_ == 0;
  }

  void printStats() const {
    Serial.printf("# sent=%lu pending=%lu dropped=%lu failures=%lu\n",
                  static_cast<unsigned long>(sent_),
                  static_cast<unsigned long>(count_),
                  static_cast<unsigned long>(dropped_),
                  static_cast<unsigned long>(failures_));
  }

  size_t pending() const { return count_; }

 private:
  /** True when the samples are done with (stored or refused for good). */
  bool send(size_t chunk) {
    size_t length = 0;

    for (size_t index = 0; index < chunk; ++index) {
      const Sample& sample = buffer_[(head_ + index) % Capacity];

      // buildIngestPayload writes the whole array at once, so copy the chunk
      // into a small staging array first (chunk <= BatchSize).
      staging_[index] = sample;
    }

    length = buildIngestPayload(payload_, PayloadCapacity, staging_, chunk);

    if (length == 0) {
      Serial.println(F("API: payload buffer too small; dropping the batch"));
      dropped_ += chunk;
      return true;
    }

    const int status = post(payload_, length);

    if (status == 200) {
      sent_ += chunk;
      failures_ = 0;
      return true;
    }

    ++failures_;
    Serial.printf("API %d\n", status);

    if (status == 400 || status == 413 || status == 422) {
      dropped_ += chunk;
      return true;
    }

    if (status == 401 || status == 403) {
      Serial.println(F("API: check API_KEY against FURNACE_INGEST_KEY on the server"));
    } else if (status == 404 || status == 405) {
      Serial.printf("API: check API_URL (%s) - the endpoint is missing\n", url_);
    }

    return false;
  }

  /** POST one body; returns the HTTP status, or a negative value on failure. */
  int post(const char* body, size_t length) {
    HTTPClient http;
    http.setTimeout(timeoutMs_);
    http.setConnectTimeout(timeoutMs_);

    bool started = false;

    if (std::strncmp(url_, "https://", 8) == 0) {
      if (verifyTls_ && rootCa_ != nullptr && rootCa_[0] != '\0') {
        secure_.setCACert(rootCa_);
      } else {
        secure_.setInsecure();

        if (!warnedInsecure_) {
          warnedInsecure_ = true;
          Serial.println(F("API: TLS certificate not verified (API_TLS_VERIFY false)"));
        }
      }

      started = http.begin(secure_, url_);
    } else {
      started = http.begin(plain_, url_);
    }

    if (!started) {
      Serial.println(F("API: HTTPClient could not start"));
      return -1;
    }

    http.addHeader(F("Content-Type"), F("application/json"));
    http.addHeader(F("Connection"), F("close"));

    if (apiKey_ != nullptr && apiKey_[0] != '\0') {
      http.addHeader(F("X-Api-Key"), apiKey_);
    }

    const int status = http.POST(reinterpret_cast<uint8_t*>(const_cast<char*>(body)), length);
    http.end();

    if (status < 0) {
      Serial.printf("API: request failed (%d %s)\n", status, HTTPClient::errorToString(status).c_str());
    }

    return status;
  }

  Sample buffer_[Capacity];
  Sample staging_[kFixedBatch];
  char payload_[PayloadCapacity];

  const char* url_ = nullptr;
  const char* apiKey_ = nullptr;
  const char* rootCa_ = nullptr;
  uint32_t timeoutMs_ = 8000;
  bool verifyTls_ = false;
  bool warnedInsecure_ = false;

  size_t head_ = 0;
  size_t count_ = 0;
  unsigned long sent_ = 0;
  unsigned long dropped_ = 0;
  unsigned long failures_ = 0;

  WiFiClient plain_;
  WiFiClientSecure secure_;
};

}  // namespace furnace
