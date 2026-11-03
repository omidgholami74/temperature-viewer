"""Wi-Fi association and clock sync for the ESP32-S3 firmware.

The board connects once at boot and reconnects on its own afterwards, because
a furnace room router reboots more often than the furnace does.
"""

import json
import time

import network

# 2025-01-01 in seconds: anything below this means the clock was never set.
_CLOCK_FLOOR = 1735689600

# Written by a ``wifi`` command from the admin panel. It overrides the
# credentials in config.py without touching that file, so a bad password never
# leaves the board unreachable through a lost config.py.
OVERRIDE = "wifi.json"


def _override_path(root=""):
    base = root.rstrip("/") if root else ""

    return base + "/" + OVERRIDE if base else "/" + OVERRIDE


def credentials(config, root=""):
    """The SSID/password to use: the saved override first, then config.py."""
    try:
        handle = open(_override_path(root), "r")

        try:
            data = json.load(handle)
        finally:
            handle.close()

        ssid = str(data.get("ssid") or "")

        if ssid:
            return ssid, str(data.get("password") or "")
    except Exception:
        pass

    return config.WIFI_SSID, config.WIFI_PASSWORD


def save_override(ssid, password, root=""):
    """Persist new credentials; they are used from the next boot on."""
    handle = open(_override_path(root), "w")

    try:
        json.dump({"ssid": str(ssid), "password": str(password)}, handle)
    finally:
        handle.close()


def connect(config, log=print, watch=None):
    """Join the network and return the station interface, connected or not."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    try:
        wlan.config(hostname=getattr(config, "WIFI_HOSTNAME", "furnace-esp32"))
    except (ValueError, OSError):
        pass  # older firmware builds have no hostname option

    # Modem sleep can park the radio between packets; a furnace monitor is
    # mains-powered, so prioritise timely uploads over the last few mA.
    try:
        wlan.config(pm=getattr(network.WLAN, "PM_NONE", 0))
    except Exception:
        pass

    if wlan.isconnected():
        _report(wlan, log)
        return wlan

    ssid, password = credentials(config)
    log("Wi-Fi: joining %r" % (ssid,))
    wlan.connect(ssid, password)

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


def reconnect(wlan, config, log=print, watch=None, force=False):
    """Try to bring a dropped link back; returns True when connected.

    ``force`` also re-associates a link that still reports connected but has
    stopped delivering traffic. That is the only recovery from a half-open
    DHCP/AP session without rebooting the whole board.
    """
    if wlan.isconnected() and not force:
        return True

    try:
        wlan.disconnect()
    except (OSError, RuntimeError):
        pass

    try:
        wlan.config(pm=getattr(network.WLAN, "PM_NONE", 0))
    except Exception:
        pass

    try:
        ssid, password = credentials(config)
        wlan.connect(ssid, password)
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
