"""Diagnostics that run before ``main.py`` on every boot and power-up.

MicroPython runs ``boot.py`` and then ``main.py``, so the firmware starts by
itself from a charger or from a PC. What it cannot do is explain a failure: if
a file is missing from the board root, ``main.py`` dies on its import and the
board sits at a REPL prompt with a traceback nobody is watching.

This file prints the two things that answer "why is it not running?" — how the
board booted and which required files are actually on it — and then lets
MicroPython run ``main.py`` as usual. It never imports the firmware itself, so
it cannot start the sampler twice.
"""

# The files main.py imports. boot.py and main.py are checked too, because a
# board that is missing either of them cannot start at all.
REQUIRED = ("boot.py", "config.py", "wifi.py", "ingest.py", "supervisor.py", "main.py")

_RESET_NAMES = (
    "PWRON_RESET",
    "HARD_RESET",
    "WDT_RESET",
    "DEEPSLEEP_RESET",
    "SOFT_RESET",
)


def missing(present):
    """Which required files are not on the board, in REQUIRED order."""
    have = set(present)
    return [name for name in REQUIRED if name not in have]


def reset_cause():
    """``'WDT_RESET'``, ``'PWRON_RESET'``, ... or None without a machine module."""
    try:
        import machine
    except ImportError:
        return None

    try:
        cause = machine.reset_cause()
    except Exception:
        return None

    for name in _RESET_NAMES:
        if getattr(machine, name, None) == cause:
            return name

    return "cause %r" % (cause,)


def listing():
    """The files at the board root, or [] when the filesystem cannot be read."""
    try:
        import os

        return os.listdir()
    except Exception:
        return []


def main():
    cause = reset_cause()

    if cause is not None:
        print("# boot: reset cause %s" % (cause,))

    present = listing()

    if not present:
        # A soft reset keeps the files, so an unreadable root is not fatal:
        # main.py still gets its chance on the next line of the boot sequence.
        return

    absent = missing(present)

    if absent:
        print("# boot: MISSING from the board root: %s" % (", ".join(absent),))
        print("# boot: main.py cannot start until those files are uploaded")
    else:
        print("# boot: starting main.py (%d files on the board)" % (len(present),))


main()
