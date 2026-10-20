"""SPI bus shared by the four MAX31855 channels."""

from machine import SPI

import config


def build_spi():
    """Create the shared SPI bus (MOSI is not used by the MAX31855)."""
    return SPI(
        config.SPI_ID,
        baudrate=config.SPI_BAUD_RATE,
        polarity=0,
        phase=0,
        bits=8,
        firstbit=SPI.MSB,
        sck=config.SCK_PIN,
        miso=config.MISO_PIN,
    )
