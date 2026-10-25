// Host checks for the pure parts of the Arduino firmware: sensor decoding, the
// RTD curve and the JSON body. No board and no ESP32 core needed.
//
//   bash esp32-s3/arduino/tests/run.sh

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "furnace_math.h"
#include "furnace_payload.h"

static int failures = 0;

#define CHECK(condition, ...)                          \
  do {                                                 \
    if (!(condition)) {                                \
      std::printf("FAIL %s:%d ", __FILE__, __LINE__);  \
      std::printf(__VA_ARGS__);                        \
      std::printf("\n");                               \
      ++failures;                                      \
    }                                                  \
  } while (0)

/** A MAX31855 word with the given thermocouple temperature and fault flags. */
static void makeMax31855(float celsius, uint32_t faultFlags, uint8_t raw[4]) {
  const int32_t steps = static_cast<int32_t>(std::lround(celsius / 0.25));
  uint32_t word = (static_cast<uint32_t>(steps) & 0x3FFFu) << 18;
  word |= faultFlags;

  raw[0] = static_cast<uint8_t>(word >> 24);
  raw[1] = static_cast<uint8_t>(word >> 16);
  raw[2] = static_cast<uint8_t>(word >> 8);
  raw[3] = static_cast<uint8_t>(word);
}

static void testMax31855() {
  uint8_t raw[4];
  float celsius = 0;
  uint8_t fault = 0;

  makeMax31855(781.25f, 0, raw);
  CHECK(furnace::decodeMax31855(raw, &celsius, &fault), "781.25 should decode");
  CHECK(std::fabs(celsius - 781.25f) < 0.001f, "expected 781.25, got %.2f", celsius);
  CHECK(fault == 0, "expected no fault, got %u", fault);

  makeMax31855(-100.25f, 0, raw);
  CHECK(furnace::decodeMax31855(raw, &celsius, &fault), "-100.25 should decode");
  CHECK(std::fabs(celsius + 100.25f) < 0.001f, "expected -100.25, got %.2f", celsius);

  // The four fault flags map onto the API bits unchanged.
  makeMax31855(500.0f, 0x1, raw);
  CHECK(!furnace::decodeMax31855(raw, &celsius, &fault) && fault == 1,
        "open circuit should report fault 1, got %u", fault);
  makeMax31855(500.0f, 0x2, raw);
  CHECK(!furnace::decodeMax31855(raw, &celsius, &fault) && fault == 2,
        "short to GND should report fault 2, got %u", fault);
  makeMax31855(500.0f, 0x4, raw);
  CHECK(!furnace::decodeMax31855(raw, &celsius, &fault) && fault == 4,
        "short to VCC should report fault 4, got %u", fault);
  makeMax31855(500.0f, 0x10000, raw);
  CHECK(!furnace::decodeMax31855(raw, &celsius, &fault) && fault == 8,
        "general fault should report fault 8, got %u", fault);

  // The datasheet frame for 25 °C: 100 steps of 0.25 °C in bits 31..18.
  const uint8_t datasheet[4] = {0x01, 0x90, 0x00, 0x00};
  CHECK(furnace::decodeMax31855(datasheet, &celsius, &fault), "datasheet frame");
  CHECK(std::fabs(celsius - 25.0f) < 0.001f, "expected 25.00, got %.2f", celsius);
}

static void testFormatCenti() {
  char text[32];

  furnace::formatCenti(text, sizeof text, 781.25f);
  CHECK(std::strcmp(text, "781.25") == 0, "781.25 -> %s", text);

  furnace::formatCenti(text, sizeof text, -1000.0f);
  CHECK(std::strcmp(text, "-1000.00") == 0, "-1000 -> %s", text);

  furnace::formatCenti(text, sizeof text, 0.0f);
  CHECK(std::strcmp(text, "0.00") == 0, "0 -> %s", text);

  furnace::formatCenti(text, sizeof text, 1234.5f);
  CHECK(std::strcmp(text, "1234.50") == 0, "1234.5 -> %s", text);

  furnace::formatCenti(text, sizeof text, -0.05f);
  CHECK(std::strcmp(text, "-0.05") == 0, "-0.05 -> %s", text);

  // A buffer that cannot hold the digits must be refused, not truncated.
  char tight[4] = {'x', 'x', 'x', 'x'};
  CHECK(furnace::formatCenti(tight, sizeof tight, 12.0f) == 0, "tight buffer accepted");
  CHECK(std::strcmp(text, "-0.05") == 0, "previous output was overwritten");
}

static void testPayload() {
  furnace::Sample samples[2];

  samples[0].uptimeMs = 100;
  samples[0].hasAt = true;
  samples[0].atMs = 1792523131000LL;
  samples[0].celsius[0] = 781.25f;
  samples[0].celsius[1] = NAN;
  samples[0].celsius[2] = NAN;
  samples[0].celsius[3] = 545.75f;
  samples[0].fault[1] = 1;

  samples[1].uptimeMs = 200;
  samples[1].hasAt = false;
  samples[1].celsius[0] = -1.5f;
  samples[1].celsius[1] = 0.0f;
  samples[1].celsius[2] = 20.25f;
  samples[1].celsius[3] = 545.75f;

  char buffer[512];
  const size_t length = furnace::buildIngestPayload(buffer, sizeof buffer, samples, 2);

  const char* expected =
      "{\"samples\":[{\"u\":100,\"at\":1792523131000,"
      "\"t\":[781.25,null,null,545.75],\"f\":[0,1,0,0]},"
      "{\"u\":200,\"t\":[-1.50,0.00,20.25,545.75],\"f\":[0,0,0,0]}]}";

  CHECK(length == std::strlen(expected), "length %zu vs %zu", length, std::strlen(expected));
  CHECK(std::strcmp(buffer, expected) == 0, "payload: %s", buffer);

  // Too small: refuse instead of sending a truncated body.
  char small[64];
  CHECK(furnace::buildIngestPayload(small, sizeof small, samples, 2) == 0, "small buffer accepted");

  // A full batch of the worst case must still fit the sketch's buffer.
  furnace::Sample worst[20];
  for (int index = 0; index < 20; ++index) {
    worst[index].uptimeMs = 4294967295u;
    worst[index].hasAt = true;
    worst[index].atMs = 9223372036854775LL;
    for (int channel = 0; channel < 4; ++channel) {
      worst[index].celsius[channel] = -3276.75f;
    }
  }

  const size_t capacity = 20 * furnace::kMaxSampleJson + 64;
  char big[20 * furnace::kMaxSampleJson + 64];
  const size_t bigLength = furnace::buildIngestPayload(big, capacity, worst, 20);
  CHECK(bigLength > 0, "worst-case batch of 20 did not fit %zu bytes", capacity);
  CHECK(bigLength < capacity, "worst-case batch overflowed");
}

static void testRtd() {
  const double tolerance = 0.05;

  CHECK(std::fabs(furnace::rtdResistanceToCelsius(100.0) - 0.0) < tolerance,
        "100 ohm should be 0 C, got %.3f", furnace::rtdResistanceToCelsius(100.0));
  CHECK(std::fabs(furnace::rtdResistanceToCelsius(138.5055) - 100.0) < tolerance,
        "138.5055 ohm should be 100 C, got %.3f", furnace::rtdResistanceToCelsius(138.5055));
  CHECK(std::fabs(furnace::rtdResistanceToCelsius(175.856) - 200.0) < 0.1,
        "175.856 ohm should be 200 C, got %.3f", furnace::rtdResistanceToCelsius(175.856));
  CHECK(std::fabs(furnace::rtdResistanceToCelsius(60.256) + 100.0) < 0.2,
        "60.256 ohm should be -100 C, got %.3f", furnace::rtdResistanceToCelsius(60.256));
  CHECK(std::fabs(furnace::rtdResistanceToCelsius(18.52) + 200.0) < 0.4,
        "18.52 ohm should be -200 C, got %.3f", furnace::rtdResistanceToCelsius(18.52));

  // PT1000 is the same curve with a 1000 ohm base.
  CHECK(std::fabs(furnace::rtdResistanceToCelsius(1000.0, 1000.0)) < tolerance,
        "1000 ohm PT1000 should be 0 C");

  CHECK(std::isnan(furnace::rtdResistanceToCelsius(0.0)), "0 ohm must be NaN");
  CHECK(std::isnan(furnace::rtdResistanceToCelsius(-5.0)), "negative ohm must be NaN");

  // 100 ohm on a 430 ohm reference is code 7620.
  CHECK(std::fabs(furnace::max31865Resistance(7620, 430.0) - 99.985) < 0.01,
        "code 7620 should be 99.99 ohm, got %.3f", furnace::max31865Resistance(7620, 430.0));

  CHECK(furnace::max31865FaultBits(0x00) == 0, "no flag must mean no fault");
  CHECK(furnace::max31865FaultBits(0x84) == 1, "open element");
  CHECK(furnace::max31865FaultBits(0x82) == 2, "lead short");
  CHECK(furnace::max31865FaultBits(0x81) == 4, "over/under voltage");
  CHECK(furnace::max31865FaultBits(0x90) == 4, "reference high");
  CHECK(furnace::max31865FaultBits(0xC0) == 8, "threshold trip");
  CHECK(furnace::max31865FaultBits(0x80) == 8, "latched fault with no bit set");
}

int main() {
  testMax31855();
  testFormatCenti();
  testPayload();
  testRtd();

  if (failures == 0) {
    std::printf("All Arduino firmware host tests passed.\n");
    return 0;
  }

  std::printf("%d check(s) failed.\n", failures);
  return 1;
}
