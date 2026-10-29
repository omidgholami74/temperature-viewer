"""Wi-Fi association and clock sync for the ESP32-S3 firmware.

The board connects once at boot and reconnects on its own afterwards, because
a furnace room router reboots more often than the furnace does.
"""

import time

import network

# 2025-01-01 in seconds: anything below this means the clock was never set.
_CLOCK_FLOOR = 1735689600


def connect(config, log=print, watch=None):
    """Join the network and return the station interface, connected or not."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    try:
        wlan.config(hostname=getattr(config, "WIFI_HOSTNAME", "furnace-esp32"))
    except (ValueError, OSError):
        pass  # older firmware builds have no hostname option

    if wlan.isconnected():
        _report(wlan, log)
        return wlan

    log("Wi-Fi: joining %r" % (config.WIFI_SSID,))
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)

    timeout_ms = int(float(getattr(config, "WIFI_TIMEOUT_S", 20)) * 1000)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

    while not wlan.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            log("Wi-Fi: no association after %d s; sampling continues, uploads wait"
                % (timeout_ms // 1000,))
            return wlan
        _feed(watch)
        time.sleep_ms(200)

    _report(wlan, log)
    return wlan


def _feed(watch):
    """Keep the watchdog happy while an association is being waited out."""
    if watch is not None:
        watch.feed()


def _report(wlan, log):
    try:
        ip, netmask, gateway, dns = wlan.ifconfig()
    except ValueError:
        return
    log("Wi-Fi: connected, ip=%s gateway=%s" % (ip, gateway))


def reconnect(wlan, config, log=print, watch=None):
    """Try to bring a dropped link back; returns True when connected."""
    if wlan.isconnected():
        return True

    try:
        wlan.disconnect()
    except (OSError, RuntimeError):
        pass

    try:
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    except (OSError, RuntimeError) as error:
        log("Wi-Fi: reconnect failed: %s" % (error,))
        return False

    timeout_ms = int(float(getattr(config, "WIFI_TIMEOUT_S", 20)) * 1000)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

    while not wlan.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            log("Wi-Fi: still offline")
            return False
        _feed(watch)
        time.sleep_ms(200)

    _report(wlan, log)
    return True


def clock_is_set():
    """True when the RTC holds a plausible date instead of the 2000 epoch."""
    return time.time() > _CLOCK_FLOOR


def sync_time(config, log=print):
    """Set the RTC from NTP. Returns True when the clock is trustworthy."""
    if clock_is_set():
        return True

    try:
        import ntptime
    except ImportError:
        log("NTP: ntptime is missing from this firmware build")
        return False

    ntptime.host = getattr(config, "NTP_HOST", "pool.ntp.org")

    try:
        ntptime.timeout = float(getattr(config, "NTP_TIMEOUT_S", 6))
    except AttributeError:
        pass

    try:
        ntptime.settime()
    except Exception as error:  # OSError, but axtls may raise others
        log("NTP: sync failed: %s" % (error,))
        return False

    if not clock_is_set():
        return False

    year, month, day, hour, minute, second, _, _ = time.localtime()
    log("NTP: clock set to %04d-%02d-%02d %02d:%02d:%02d UTC"
        % (year, month, day, hour, minute, second))
    return True
