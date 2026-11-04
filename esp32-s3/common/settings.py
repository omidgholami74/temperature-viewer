"""Live settings shared by the sampling loop and the heartbeat thread.

The admin panel sends a ``settings`` command (or the heartbeat returns the saved
snapshot). ``DeviceLink`` applies it here, writes ``/settings.json`` and the
sampling loop picks it up on the next frame:

* ``sample_ms``      -- frame interval; 400 ms is the default.
* ``noise_samples``  -- median window per MAX31855 channel.
* ``noise_gap_us``   -- gap between those reads.
* ``ema_alpha``      -- smoothing factor, 1.0 disables it.
* ``heartbeat_s``    -- how often the board reports in.
* ``channels``       -- per furnace: enabled and fixed calibration offset.

Defaults are supplied by ``main.py`` and mirror ``config.py``/the constants at
the top of the file, so a board that was never configured from the panel behaves
exactly as before. A corrupt settings file is ignored rather than crashing the
board; the next valid save replaces it.
"""

import json
import os

DEFAULTS = {
    "sample_ms": 400,
    "noise_samples": 5,
    "noise_gap_us": 6000,
    "ema_alpha": 0.35,
    "heartbeat_s": 15,
}

MAX_CHANNELS = 4
MAX_OFFSET_RANGES = 12
FILE_NAME = "settings.json"


class SettingsError(ValueError):
    """A settings payload that must not be applied."""


def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError("%s must be a number" % (name,))

    if integer:
        if isinstance(value, float) and value != int(value):
            raise SettingsError("%s must be a whole number" % (name,))
        value = int(value)
    else:
        value = float(value)

    if value < low or value > high:
        raise SettingsError("%s must be between %s and %s" % (name, low, high))

    return value


class Settings:
    """Validated, persisted settings with a snapshot safe to read per frame."""

    def __init__(self, root="", defaults=None, channels=None, log=print):
        self.root = root or ""
        self.path = (self.root.rstrip("/") + "/" + FILE_NAME) if self.root else FILE_NAME
        self.log = log
        self._values = dict(DEFAULTS)

        if defaults:
            for key in DEFAULTS:
                if key in defaults:
                    self._values[key] = defaults[key]

        if channels is None:
            channels = [
                {"channel": number, "enabled": True, "offset": 0.0}
                for number in range(1, MAX_CHANNELS + 1)
            ]

        try:
            self._channels = self._parse_channels(channels)
        except SettingsError as error:
            self.log("# settings: invalid defaults: %s" % (error,))
            self._channels = [
                {"channel": number, "enabled": True, "offset": 0.0}
                for number in range(1, MAX_CHANNELS + 1)
            ]

        self.load()

    # -- validation -------------------------------------------------------

    def _parse_channels(self, raw):
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= MAX_CHANNELS:
            raise SettingsError("channels must list 1 to %d entries" % (MAX_CHANNELS,))

        parsed = []
        seen = {}

        for entry in raw:
            if not isinstance(entry, dict):
                raise SettingsError("each channel must be an object")

            number = entry.get("channel")

            if isinstance(number, bool) or not isinstance(number, int):
                raise SettingsError("channel must be an integer from 1 to %d" % (MAX_CHANNELS,))

            if number < 1 or number > MAX_CHANNELS or number in seen:
                raise SettingsError("channel must be 1 to %d and appear once" % (MAX_CHANNELS,))

            enabled = entry.get("enabled", True)

            if not isinstance(enabled, bool):
                raise SettingsError("channel %d enabled must be true or false" % (number,))

            offset = _number(entry.get("offset", 0.0), "channel %d offset" % (number,), -200.0, 200.0)
            ranges = self._parse_ranges(entry.get("offset_ranges", []), number)
            seen[number] = True
            parsed.append({
                "channel": number,
                "enabled": enabled,
                "offset": offset,
                "offset_ranges": ranges,
            })

        parsed.sort(key=lambda item: item["channel"])
        return parsed

    def _parse_ranges(self, raw, channel):
        """Validate temperature-band overrides for one channel.

        A band is half-open: ``min <= temperature < max``. Bands are sorted and
        must not overlap, so a reading can never receive two corrections.
        """
        if raw is None:
            return []

        if not isinstance(raw, (list, tuple)):
            raise SettingsError("channel %d offset_ranges must be a list" % (channel,))

        if len(raw) > MAX_OFFSET_RANGES:
            raise SettingsError("channel %d has too many offset ranges" % (channel,))

        parsed = []

        for entry in raw:
            if not isinstance(entry, dict):
                raise SettingsError("channel %d offset range must be an object" % (channel,))

            low = _number(
                entry.get("min"),
                "channel %d offset range min" % (channel,),
                -270.0,
                1800.0,
            )
            high = _number(
                entry.get("max"),
                "channel %d offset range max" % (channel,),
                -270.0,
                1800.0,
            )

            if low >= high:
                raise SettingsError("channel %d offset range min must be below max" % (channel,))

            offset = _number(
                entry.get("offset", 0.0),
                "channel %d offset range offset" % (channel,),
                -200.0,
                200.0,
            )
            parsed.append({"min": low, "max": high, "offset": offset})

        parsed.sort(key=lambda item: item["min"])

        for index in range(1, len(parsed)):
            if parsed[index]["min"] < parsed[index - 1]["max"]:
                raise SettingsError("channel %d offset ranges must not overlap" % (channel,))

        return parsed

    def _normalize(self, data):
        if not isinstance(data, dict):
            raise SettingsError("settings must be an object")

        values = dict(self._values)

        if "sample_ms" in data:
            values["sample_ms"] = _number(data["sample_ms"], "sample_ms", 50, 60000, True)

        if "noise_samples" in data:
            values["noise_samples"] = _number(data["noise_samples"], "noise_samples", 1, 9, True)

        if "noise_gap_us" in data:
            values["noise_gap_us"] = _number(data["noise_gap_us"], "noise_gap_us", 0, 50000, True)

        if "ema_alpha" in data:
            values["ema_alpha"] = _number(data["ema_alpha"], "ema_alpha", 0.05, 1.0)

        if "heartbeat_s" in data:
            values["heartbeat_s"] = _number(data["heartbeat_s"], "heartbeat_s", 5, 3600, True)

        channels = list(self._channels)

        if "channels" in data:
            channels = self._parse_channels(data["channels"])

        return values, channels

    # -- persistence ------------------------------------------------------

    def load(self):
        """Read the last saved snapshot; a missing or broken file keeps defaults."""

        try:
            with open(self.path, "r") as handle:
                stored = json.loads(handle.read())
        except OSError:
            return
        except Exception as error:
            self.log("# settings: ignoring %s: %s" % (self.path, error))
            return

        try:
            self._values, self._channels = self._normalize(stored)
        except SettingsError as error:
            self.log("# settings: ignoring %s: %s" % (self.path, error))

    def _write(self, values, channels):
        payload = dict(values)
        payload["channels"] = channels
        temporary = self.path + ".tmp"

        with open(temporary, "w") as handle:
            handle.write(json.dumps(payload))

        try:
            os.rename(temporary, self.path)
        except OSError:
            # Some littlefs builds refuse to replace an existing file.
            try:
                os.remove(self.path)
            except OSError:
                pass

            os.rename(temporary, self.path)

    def apply(self, data):
        """Validate and persist a payload; returns True when something changed.

        Nothing is assigned when validation or the write fails, so a bad command
        cannot leave the board half-configured.
        """

        values, channels = self._normalize(data)

        if values == self._values and channels == self._channels:
            return False

        self._write(values, channels)
        self._values = values
        self._channels = channels
        return True

    # -- readers ----------------------------------------------------------

    def snapshot(self):
        values = dict(self._values)
        values["channels"] = [dict(entry) for entry in self._channels]
        return values

    def channel(self, number):
        for entry in self._channels:
            if entry["channel"] == number:
                return entry

        return {"channel": number, "enabled": False, "offset": 0.0, "offset_ranges": []}


def offset_for(channel, temperature):
    """Calibration offset for a channel at one raw reading.

    A band replaces the scalar fallback; outside every band the channel's
    normal offset applies. Faulted readings are filtered out by the sampler
    before this function is called, so a bad frame is never made plausible.
    """
    try:
        value = float(temperature)
    except (TypeError, ValueError):
        return 0.0

    for band in channel.get("offset_ranges", []):
        if band["min"] <= value < band["max"]:
            return band["offset"]

    return channel.get("offset", 0.0)
