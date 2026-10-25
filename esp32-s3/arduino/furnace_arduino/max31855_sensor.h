// Four MAX31855 thermocouple amplifiers on one SPI bus.
//
// The MAX31855 is read-only: it has no MOSI/SDI pin, so GPIO11 stays free.
#pragma once

#include <Arduino.h>
#include <SPI.h>

#include "furnace_math.h"

#ifndef MAX31855_SCK_PIN
#define MAX31855_SCK_PIN 14
#endif

#ifndef MAX31855_MISO_PIN
#define MAX31855_MISO_PIN 12
#endif

#ifndef MAX31855_CS_PINS
#define MAX31855_CS_PINS {15, 16, 17, 18}
#endif

class Max31855Array {
 public:
  static constexpr uint8_t kChannels = 4;

  void begin() {
    const uint8_t* pins = csPins();

    for (uint8_t channel = 0; channel < kChannels; ++channel) {
      pinMode(pins[channel], OUTPUT);
      digitalWrite(pins[channel], HIGH);  // idle high: one amplifier at a time
    }

    // -1 leaves MOSI and SS unclaimed: this sensor never writes to the bus.
    SPI.begin(MAX31855_SCK_PIN, MAX31855_MISO_PIN, -1, -1);
    SPI.setFrequency(1000000);
    SPI.setDataMode(SPI_MODE0);
  }

  /** Read one channel. Returns false and sets `fault` on a sensor fault. */
  bool read(uint8_t channel, float* celsius, uint8_t* fault) {
    if (channel >= kChannels) {
      *celsius = NAN;
      *fault = furnace::kFaultRead;
      return false;
    }

    const uint8_t* pins = csPins();
    uint8_t raw[4];

    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins[channel], LOW);
    delayMicroseconds(2);  // tCSS: CS may not fall before the clock settles

    for (uint8_t index = 0; index < 4; ++index) {
      raw[index] = SPI.transfer(0x00);
    }

    digitalWrite(pins[channel], HIGH);
    SPI.endTransaction();

    return furnace::decodeMax31855(raw, celsius, fault);
  }

 private:
  static const uint8_t* csPins() {
    static const uint8_t pins[kChannels] = MAX31855_CS_PINS;
    return pins;
  }
};
