from machine import Pin, SPI
import time
import struct

# =====================================
# Raspberry Pi Pico + MAX31855
# Furnace 1
# =====================================

SCK_PIN = 6
MISO_PIN = 4
CS_PIN = 5

# SPI configuration
spi = SPI(
    0,
    baudrate=1000000,
    polarity=0,
    phase=0,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(SCK_PIN),
    mosi=None,
    miso=Pin(MISO_PIN)
)

cs = Pin(CS_PIN, Pin.OUT, value=1)


def read_max31855():
    """
    Read temperature and fault status from MAX31855.
    Returns (temperature_c, fault_message).
    """

    cs.value(0)
    time.sleep_us(2)

    data = spi.read(4)

    cs.value(1)

    # Convert 4 bytes to a 32-bit integer
    raw = struct.unpack(">I", data)[0]

    # Check thermocouple fault bits
    fault = raw & 0x7

    if fault:
        messages = []

        if fault & 0x01:
            messages.append("Open thermocouple")

        if fault & 0x02:
            messages.append("Short to GND")

        if fault & 0x04:
            messages.append("Short to VCC")

        return None, ", ".join(messages)

    # Extract signed 14-bit thermocouple temperature
    temp_raw = (raw >> 18) & 0x3FFF

    if temp_raw & 0x2000:
        temp_raw -= 0x4000

    temperature = temp_raw * 0.25

    return temperature, None


print("Furnace 1 - MAX31855")
print("Starting temperature readings...")

while True:
    try:
        temperature, error = read_max31855()

        if error:
            print("ERROR:", error)

        elif temperature is not None:
            # Send temperature as CSV
            print("{:.2f}".format(temperature))

        time.sleep(1)

    except Exception as e:
        cs.value(1)
        print("READ_ERROR:", str(e))
        time.sleep_ms(200)
