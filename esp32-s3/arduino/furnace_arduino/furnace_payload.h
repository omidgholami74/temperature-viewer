// Builds the POST /api/ingest body that collector/collector.py would send.
//
// No Arduino and no external JSON library: the body is written into a caller
// supplied buffer with snprintf and a hand-rolled two-decimal float formatter,
// so it builds the same on an ESP32 (no `%f` support needed) and on a PC.
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace furnace {

/** One frame: uptime, optional absolute time, four temperatures and faults. */
struct Sample {
  uint32_t uptimeMs = 0;
  int64_t atMs = 0;       // Unix time in ms, only when `hasAt`
  bool hasAt = false;
  float celsius[4] = {NAN, NAN, NAN, NAN};
  uint8_t fault[4] = {0, 0, 0, 0};
};

/**
 * Write a float with exactly two decimals, e.g. "781.25" or "-1000.00".
 *
 * Percent-f formatting is avoided on purpose: it is the one printf feature
 * some embedded builds leave out, and a silent "0.00" in a furnace log is
 * worse than a few lines of integer maths.
 *
 * @return characters written (without the NUL), or 0 when it does not fit.
 */
inline size_t formatCenti(char* out, size_t capacity, float value) {
  if (out == nullptr || capacity < 8 || !std::isfinite(value)) {
    return 0;
  }

  const bool negative = value < 0.0f;
  long long centi = static_cast<long long>(std::llround(static_cast<double>(std::fabs(value)) * 100.0));
  long long whole = centi / 100;
  const long long fraction = centi % 100;

  char digits[24];
  int count = 0;

  do {
    digits[count++] = static_cast<char>('0' + (whole % 10));
    whole /= 10;
  } while (whole > 0 && count < 20);

  size_t length = (negative ? 1u : 0u) + static_cast<size_t>(count) + 3u;

  if (length + 1 > capacity) {
    return 0;
  }

  size_t pos = 0;

  if (negative) {
    out[pos++] = '-';
  }

  while (count > 0) {
    out[pos++] = digits[--count];
  }

  out[pos++] = '.';
  out[pos++] = static_cast<char>('0' + fraction / 10);
  out[pos++] = static_cast<char>('0' + fraction % 10);
  out[pos] = '\0';

  return pos;
}

/**
 * Serialise a batch as `{"samples":[{...},...]}`.
 *
 * A faulted channel is written as `null` (the API's "no reading") instead of a
 * made-up number, and `at` is omitted when the clock was never set, so the
 * server anchors the batch on arrival.
 *
 * @return bytes written (without the NUL), or 0 when the buffer is too small.
 */
inline size_t buildIngestPayload(char* out, size_t capacity, const Sample* samples, size_t count) {
  if (out == nullptr || capacity < 32 || samples == nullptr || count == 0) {
    return 0;
  }

  size_t pos = 0;
  char number[32];

  auto put = [&](const char* text) -> bool {
    const size_t length = std::strlen(text);

    if (pos + length + 1 > capacity) {
      return false;
    }

    std::memcpy(out + pos, text, length);
    pos += length;
    return true;
  };

  if (!put("{\"samples\":[")) {
    return 0;
  }

  for (size_t index = 0; index < count; ++index) {
    const Sample& sample = samples[index];

    if (index > 0 && !put(",")) {
      return 0;
    }

    std::snprintf(number, sizeof number, "{\"u\":%lu", static_cast<unsigned long>(sample.uptimeMs));

    if (!put(number)) {
      return 0;
    }

    if (sample.hasAt) {
      std::snprintf(number, sizeof number, ",\"at\":%lld", static_cast<long long>(sample.atMs));

      if (!put(number)) {
        return 0;
      }
    }

    if (!put(",\"t\":[")) {
      return 0;
    }

    for (int channel = 0; channel < 4; ++channel) {
      if (channel > 0 && !put(",")) {
        return 0;
      }

      const bool usable = sample.fault[channel] == 0 && std::isfinite(sample.celsius[channel]);

      if (!usable) {
        if (!put("null")) {
          return 0;
        }

        continue;
      }

      const size_t length = formatCenti(number, sizeof number, sample.celsius[channel]);

      if (length == 0 || !put(number)) {
        return 0;
      }
    }

    if (!put("],\"f\":[")) {
      return 0;
    }

    for (int channel = 0; channel < 4; ++channel) {
      std::snprintf(number, sizeof number, "%s%u", channel > 0 ? "," : "", static_cast<unsigned>(sample.fault[channel]));

      if (!put(number)) {
        return 0;
      }
    }

    if (!put("]}")) {
      return 0;
    }
  }

  if (!put("]}")) {
    return 0;
  }

  out[pos] = '\0';  // callers may print the body, and HTTPClient takes a pointer
  return pos;
}

/** Bytes a single sample can take in the worst case, for buffer sizing. */
constexpr size_t kMaxSampleJson = 112;

}  // namespace furnace
