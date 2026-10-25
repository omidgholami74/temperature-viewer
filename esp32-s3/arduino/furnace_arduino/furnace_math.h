// Sensor decoding and RTD maths, free of Arduino headers so the same file can
// be compiled and tested on a PC (see ../tests).
#pragma once

#include <cmath>
#include <cstdint>

namespace furnace {

// Fault bits the API stores per channel.
constexpr uint8_t kFaultOpen = 1;        // open thermocouple / broken RTD lead
constexpr uint8_t kFaultShortGround = 2; // short to GND / lead short
constexpr uint8_t kFaultShortVcc = 4;    // short to VCC / over-voltage
constexpr uint8_t kFaultRead = 8;        // read error / threshold trip

/**
 * Decode one MAX31855 frame (four bytes, MSB first).
 *
 * Bit layout: D31 sign, D30..D18 signed 14-bit thermocouple temperature in
 * 0.25 °C steps, D16 fault, D2..D0 short/open flags.
 *
 * @return true when the channel produced a usable temperature.
 */
inline bool decodeMax31855(const uint8_t raw[4], float* celsius, uint8_t* fault) {
  const uint32_t word = (static_cast<uint32_t>(raw[0]) << 24) |
                        (static_cast<uint32_t>(raw[1]) << 16) |
                        (static_cast<uint32_t>(raw[2]) << 8) |
                        static_cast<uint32_t>(raw[3]);

  uint8_t bits = 0;

  if (word & 0x00000001u) bits |= kFaultOpen;
  if (word & 0x00000002u) bits |= kFaultShortGround;
  if (word & 0x00000004u) bits |= kFaultShortVcc;
  if (word & 0x00010000u) bits |= kFaultRead;

  if (bits) {
    *celsius = NAN;
    *fault = bits;
    return false;
  }

  int32_t rawTemperature = static_cast<int32_t>((word >> 18) & 0x3FFFu);

  if (rawTemperature & 0x2000) {
    rawTemperature -= 0x4000;
  }

  *celsius = static_cast<float>(rawTemperature) * 0.25f;
  *fault = 0;
  return true;
}

/** RTD code from the MAX31865 (15 bits) to ohms. */
inline double max31865Resistance(uint16_t rtdCode, double referenceResistor) {
  return static_cast<double>(rtdCode) * referenceResistor / 32768.0;
}

/**
 * MAX31865 fault register byte to the API fault mask.
 *
 * 1 open element or broken lead, 2 lead/reference short, 4 over/under voltage,
 * 8 RTD threshold trip. 0 means "no fault".
 */
inline uint8_t max31865FaultBits(uint8_t code) {
  if (!(code & 0x80)) {
    return 0;
  }

  uint8_t bits = 0;

  if (code & 0x04) bits |= kFaultOpen;        // RTDIN- below 0.85 x VBIAS
  if (code & 0x02) bits |= kFaultShortGround; // FORCE- open
  if (code & 0x01) bits |= kFaultShortVcc;    // over/under voltage
  if (code & 0x10) bits |= kFaultShortVcc;    // REFIN- above 0.85 x VBIAS
  if (code & 0x60) bits |= kFaultRead;        // RTD high/low threshold

  return bits ? bits : kFaultRead;
}

/**
 * Resistance in ohms to degrees Celsius for a platinum RTD (IEC 60751).
 *
 * Above 0 °C the Callendar-Van Dusen curve is quadratic and the root is
 * closed-form; below 0 °C it gains the C term, which has no useful closed
 * form, so a few Newton steps find the root instead.
 */
inline float rtdResistanceToCelsius(double resistance, double nominal = 100.0) {
  if (!(resistance > 0.0) || !(nominal > 0.0)) {
    return NAN;
  }

  const double a = 3.9083e-3;
  const double b = -5.775e-7;
  const double c = -4.183e-12;

  const double ratio = resistance / nominal;
  const double z2 = a * a - 4.0 * b;
  const double z3 = 4.0 * b / nominal;
  const double z4 = 2.0 * b;
  const double discriminant = z2 + z3 * resistance;

  if (discriminant >= 0.0 && z4 != 0.0) {
    const double t = (-a + std::sqrt(discriminant)) / z4;

    if (t >= 0.0) {
      return static_cast<float>(t);
    }
  }

  double t = (ratio - 1.0) / a;

  for (int i = 0; i < 12; ++i) {
    const double t2 = t * t;
    const double t3 = t2 * t;
    const double value = 1.0 + a * t + b * t2 + c * (t - 100.0) * t3 - ratio;
    const double slope = a + 2.0 * b * t + c * (3.0 * t2 * (t - 100.0) + t3);

    if (slope == 0.0) {
      break;
    }

    const double step = value / slope;
    t -= step;

    if (std::fabs(step) < 1e-9) {
      break;
    }
  }

  return static_cast<float>(t);
}

}  // namespace furnace
