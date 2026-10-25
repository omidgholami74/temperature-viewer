// Four MAX31865 RTD front ends (PT100/PT1000) on one SPI bus.
//
// Unlike the MAX31855 the MAX31865 is a register device, so it needs MOSI.
// SPI mode 1 (CPOL = 0, CPHA = 1) is what the datasheet and the reference
// library use; the clock idles low and data is sampled on the falling edge.
#pragma once

#include <Arduino.h>
#include <SPI.h>

#include "furnace_math.h"

#ifndef MAX31865_SCK_PIN
#define MAX31865_SCK_PIN 14
#endif

#ifndef MAX31865_MISO_PIN
#define MAX31865_MISO_PIN 12
#endif

#ifndef MAX31865_MOSI_PIN
#define MAX31865_MOSI_PIN 11
#endif

#ifndef MAX31865_CS_PINS
#define MAX31865_CS_PINS {15, 16, 17, 18}
#endif

#ifndef MAX31865_RREF
#define MAX31865_RREF 430.0
#endif

#ifndef MAX31865_RNOMINAL
#define MAX31865_RNOMINAL 100.0
#endif

#ifndef MAX31865_WIRE_MODE
#define MAX31865_WIRE_MODE 3
#endif

#ifndef MAX31865_FILTER_HZ
#define MAX31865_FILTER_HZ 50
#endif

class Max31865Array {
 public:
  static constexpr uint8_t kChannels = 4;

  // Registers and bits
  static constexpr uint8_t kConfigRegister = 0x00;
  static constexpr uint8_t kRtdRegister = 0x01;
  static constexpr uint8_t kBiasOn = 0x80;
  static constexpr uint8_t kAutoConvert = 0x40;
  static constexpr uint8_t kThreeWire = 0x10;
  static constexpr uint8_t kClearFault = 0x02;
  static constexpr uint8_t kFilter50Hz = 0x01;

  void begin() {
    const uint8_t* pins = csPins();

    for (uint8_t channel = 0; channel < kChannels; ++channel) {
      pinMode(pins[channel], OUTPUT);
      digitalWrite(pins[channel], HIGH);
    }

    SPI.begin(MAX31865_SCK_PIN, MAX31865_MISO_PIN, MAX31865_MOSI_PIN, -1);
    SPI.setFrequency(1000000);
    SPI.setDataMode(SPI_MODE1);

    for (uint8_t channel = 0; channel < kChannels; ++channel) {
      writeRegister(channel, kConfigRegister, configValue());
    }

    // The first conversion after the bias is enabled needs ~65 ms.
    delay(100);
  }

  /** Read one channel. Returns false and sets `fault` on a sensor fault. */
  bool read(uint8_t channel, float* celsius, uint8_t* fault) {
    if (channel >= kChannels) {
      *celsius = NAN;
      *fault = furnace::kFaultRead;
      return false;
    }

    uint8_t block[8];

    if (!readRegisters(channel, kRtdRegister, block, sizeof block)) {
      *celsius = NAN;
      *fault = furnace::kFaultRead;
      return false;
    }

    const uint16_t code = static_cast<uint16_t>(
        (static_cast<uint16_t>(block[0]) << 8 | static_cast<uint16_t>(block[1])) >> 1);
    const uint8_t bits = furnace::max31865FaultBits(block[2]);

    if (bits) {
      clearFaults(channel);
      *celsius = NAN;
      *fault = bits;
      return false;
    }

    const double ohms = furnace::max31865Resistance(code, static_cast<double>(MAX31865_RREF));

    *celsius = furnace::rtdResistanceToCelsius(ohms, static_cast<double>(MAX31865_RNOMINAL));
    *fault = 0;

    return true;
  }

 private:
  static const uint8_t* csPins() {
    static const uint8_t pins[kChannels] = MAX31865_CS_PINS;
    return pins;
  }

  static uint8_t configValue() {
    uint8_t value = kBiasOn | kAutoConvert;

    if (MAX31865_WIRE_MODE == 3) {
      value |= kThreeWire;
    }

    if (MAX31865_FILTER_HZ == 50) {
      value |= kFilter50Hz;
    }

    return value;
  }

  void writeRegister(uint8_t channel, uint8_t reg, uint8_t value) {
    const uint8_t* pins = csPins();

    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));
    digitalWrite(pins[channel], LOW);
    SPI.transfer(static_cast<uint8_t>(reg | 0x80));
    SPI.transfer(value);
    digitalWrite(pins[channel], HIGH);
    SPI.endTransaction();
  }

  bool readRegisters(uint8_t channel, uint8_t reg, uint8_t* out, size_t length) {
    const uint8_t* pins = csPins();

    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));
    digitalWrite(pins[channel], LOW);
    SPI.transfer(static_cast<uint8_t>(reg & 0x7F));

    for (size_t index = 0; index < length; ++index) {
      out[index] = SPI.transfer(0x00);
    }

    digitalWrite(pins[channel], HIGH);
    SPI.endTransaction();

    return true;
  }

  /** After a fault the latch has to be cleared or every later read fails. */
  void clearFaults(uint8_t channel) {
    writeRegister(channel, kConfigRegister, static_cast<uint8_t>(configValue() | kClearFault));
    delay(2);
    writeRegister(channel, kConfigRegister, configValue());
  }
};
