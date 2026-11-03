"""Publish an ESP32-S3 firmware bundle for OTA updates.

    python3 esp32-s3/tools/publish.py --variant max31855
    python3 esp32-s3/tools/publish.py --variant max31865 --version 2026-11-03

The bundle is written to ``backend/var/firmware/<channel>/``: a manifest and
the plain ``.py`` files the board should have at its root. The API serves it on
``GET /api/firmware/manifest?channel=...`` and ``GET /api/firmware/file?...``,
both behind ``FURNACE_INGEST_KEY``.

``config.py`` is deliberately never part of a bundle: it holds the Wi-Fi
password and the API key, and an update must not overwrite them.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(FIRMWARE_ROOT)
DEFAULT_OUT = os.path.join(REPO_ROOT, "backend", "var", "firmware")

# Files every bundle carries, as (source relative to esp32-s3/, name on the board).
COMMON_FILES = [
    ("boot.py", "boot.py"),
    (os.path.join("common", "wifi.py"), "wifi.py"),
    (os.path.join("common", "ingest.py"), "ingest.py"),
    (os.path.join("common", "supervisor.py"), "supervisor.py"),
    (os.path.join("common", "ota.py"), "ota.py"),
]

VARIANTS = {
    "max31855": [("max31855/main.py", "main.py")],
    "max31865": [
        ("max31865/main.py", "main.py"),
        ("max31865/max31865.py", "max31865.py"),
    ],
}


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "wb") as handle:
        handle.write(data)


def bundle_files(variant):
    """[(source path, board name)] for one variant, config.py excluded."""
    if variant not in VARIANTS:
        raise SystemExit("unknown variant %r (choose %s)" % (variant, ", ".join(VARIANTS)))

    files = []
    seen = {}

    for relative, name in COMMON_FILES + VARIANTS[variant]:
        source = os.path.join(FIRMWARE_ROOT, relative)

        if not os.path.isfile(source):
            raise SystemExit("missing firmware file: %s" % (source,))

        if name in seen:
            raise SystemExit("two source files would be published as %s" % (name,))

        if name == "config.py":
            raise SystemExit("refusing to publish config.py: it holds credentials")

        seen[name] = source
        files.append((source, name))

    return files


def publish(variant, out_dir=DEFAULT_OUT, version=None, notes=""):
    """Write a bundle and return its manifest as a dict."""
    sources = bundle_files(variant)
    version = version or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = os.path.join(out_dir, variant)
    entries = []

    # Start clean: a file removed from the bundle must not linger on the server.
    if os.path.isdir(target):
        shutil.rmtree(target)

    os.makedirs(target)

    for source, name in sources:
        data = _read(source)

        if name.endswith(".py"):
            try:
                compile(data, name, "exec")
            except SyntaxError as error:
                raise SystemExit("%s does not compile: %s" % (source, error))

        _write(os.path.join(target, name), data)
        entries.append({"name": name, "size": len(data), "sha256": _sha256(data)})

    entries.sort(key=lambda entry: entry["name"])

    manifest = {
        "channel": variant,
        "version": str(version),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": entries,
    }

    if notes:
        manifest["notes"] = notes

    _write(
        os.path.join(target, "manifest.json"),
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )

    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", default="max31855", choices=sorted(VARIANTS))
    parser.add_argument("--version", default=None, help="defaults to the UTC timestamp")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output root (default: backend/var/firmware)")
    parser.add_argument("--notes", default="", help="optional note shown in the manifest")
    args = parser.parse_args(argv)

    manifest = publish(args.variant, args.out, args.version, args.notes)
    target = os.path.join(args.out, args.variant)
    total = sum(entry["size"] for entry in manifest["files"])

    print("published %s %s" % (args.variant, manifest["version"]))
    print("  %d files, %d bytes -> %s" % (len(manifest["files"]), total, target))

    for entry in manifest["files"]:
        print("  %-14s %6d  %s" % (entry["name"], entry["size"], entry["sha256"][:16]))

    print("")
    print("Next:")
    print("  1. copy %s to the API host (%s)" % (target, "FURNACE_FIRMWARE_DIR"))
    print("  2. restart PHP if the directory is new")
    print("  3. the board checks after boot and every OTA_CHECK_S seconds")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
