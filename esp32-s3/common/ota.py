"""Remote firmware updates over HTTP(S) for the ESP32-S3.

The board downloads a bundle published with ``esp32-s3/tools/publish.py``,
checks every file against the manifest, stages it in ``/ota/stage`` and asks
``main.py`` to reboot. ``boot.py`` applies the staged files before ``main.py``
runs, keeping a copy of the replaced files in ``/ota/backup``.

If the new firmware does not confirm itself within three boots, the next boot
restores the backup automatically. ``main.py`` confirms after a minute of
uptime, so a bundle that crashes on import is rolled back without a laptop.

Only the firmware ``.py`` files travel in a bundle. ``config.py`` is never
published, so Wi-Fi credentials and API keys survive an update.
"""

import json
import os
import time

try:
    import hashlib
except ImportError:  # a build without hashlib cannot verify a bundle
    hashlib = None

try:
    import machine
except ImportError:  # the host tests run without a board
    machine = None

try:
    import socket
except ImportError:
    socket = None

try:
    import ssl
except ImportError:  # a build without TLS still updates over http://
    ssl = None

try:
    import _thread
except ImportError:  # no thread support: updates only happen at boot
    _thread = None

import ingest  # split_url, parse_head: one HTTP implementation in the firmware

# A firmware file is a few KB; anything larger is not a bundle written by
# publish.py and must not be written to the board.
MAX_FILE_BYTES = 65536
MAX_BUNDLE_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 640 * 1024
# Boots allowed before an unconfirmed update is rolled back.
BOOT_LIMIT = 3
# A bundle file name is a plain name in the board root, never a path.
_NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
_MAX_NAME = 64


def _sleep_ms(milliseconds):
    """MicroPython has sleep_ms, CPython (the host tests) only has sleep."""
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def _join(root, *parts):
    """Absolute board path, or a path under ``root`` in the host tests."""
    base = root.rstrip("/") if root else ""
    return base + "/" + "/".join(parts)


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read(path):
    handle = open(path, "rb")

    try:
        return handle.read()
    finally:
        handle.close()


def _write(path, data):
    handle = open(path, "wb")

    try:
        handle.write(data)
    finally:
        handle.close()


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _mkdir(path):
    try:
        os.mkdir(path)
    except OSError:
        pass  # already there


def _mkdirs(root):
    _mkdir(_join(root, "ota"))
    _mkdir(_join(root, "ota", "stage"))
    _mkdir(_join(root, "ota", "backup"))


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def _clear(path):
    for name in _listdir(path):
        _remove(path + "/" + name)


def _free_bytes(root):
    """Free bytes in the filesystem holding ``root``, or None if unknown."""
    try:
        stats = os.statvfs(_join(root) or "/")

        return int(stats[0]) * int(stats[3])
    except Exception:
        return None


def sha256_hex(data):
    """Lower-case SHA-256 of ``data``, or None when hashlib is missing."""
    if hashlib is None:
        return None

    digest = hashlib.sha256(data).hexdigest()

    if isinstance(digest, bytes):
        digest = digest.decode()

    return digest.lower()


def valid_name(name):
    """True for a bundle file name that can only land in the board root."""
    if not isinstance(name, str) or name == "" or len(name) > _MAX_NAME:
        return False

    if name.startswith(".") or ".." in name or "/" in name or "\\" in name:
        return False

    for char in name:
        if char not in _NAME_CHARS:
            return False

    return True


def valid_channel(channel):
    """A bundle name as accepted by the API (max31855, max31865, ...)."""
    if not isinstance(channel, str) or not 1 <= len(channel) <= 32:
        return False

    for char in channel:
        if char not in "abcdefghijklmnopqrstuvwxyz0123456789-":
            return False

    return not channel.startswith("-")


def parse_manifest(payload):
    """Validate the server manifest and return ``{version, files}``.

    Raises ``ValueError`` for anything that is not a complete bundle: a
    manifest with a truncated file list would otherwise half-update a board.
    """
    if not isinstance(payload, dict):
        raise ValueError("manifest must be an object")

    version = payload.get("version")

    if not isinstance(version, str) or version == "" or len(version) > 64:
        raise ValueError("manifest has no valid version")

    raw_files = payload.get("files")

    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("manifest lists no files")

    files = []

    for entry in raw_files:
        if not isinstance(entry, dict):
            raise ValueError("manifest file entry must be an object")

        name = entry.get("name")
        digest = entry.get("sha256")
        size = entry.get("size")

        if not valid_name(name):
            raise ValueError("manifest has an unsafe file name: %r" % (name,))

        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("manifest has no sha256 for %s" % (name,))

        for char in digest:
            if char not in "0123456789abcdefABCDEF":
                raise ValueError("manifest sha256 for %s is not hex" % (name,))

        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_FILE_BYTES:
            raise ValueError("manifest size for %s is out of range" % (name,))

        files.append({"name": name, "sha256": digest.lower(), "size": int(size)})

    return {"version": version, "files": files}


def _load_json(path):
    return json.loads(_read(path).decode("utf-8"))


def apply_pending(root="", log=print):
    """Move the staged bundle into the board root. Returns the new version.

    Every file is checked against the manifest again (the download was checked
    too, but the staging area can be corrupted by a power cut) and compiled
    before it replaces anything, so a syntax error never reaches the root.
    """
    pending_path = _join(root, "ota", "pending.json")

    if not _exists(pending_path):
        return None

    pending = _load_json(pending_path)
    files = pending.get("files") or []
    stage = _join(root, "ota", "stage")
    backup = _join(root, "ota", "backup")
    _mkdir(backup)
    replaced = []

    for entry in files:
        name = entry.get("name")

        if not valid_name(name):
            raise ValueError("pending bundle has an unsafe name: %r" % (name,))

        data = _read(stage + "/" + name)
        expected = (entry.get("sha256") or "").lower()

        if sha256_hex(data) != expected:
            raise ValueError("checksum mismatch for %s" % (name,))

        if name.endswith(".py"):
            compile(data.decode("utf-8"), name, "exec")

        target = _join(root, name)

        if _exists(target):
            _write(backup + "/" + name, _read(target))
            replaced.append(name)

        _write(target, data)

    _write(
        _join(root, "ota", "backup.json"),
        json.dumps({"files": replaced}).encode("utf-8"),
    )
    _write(_join(root, "ota", "version"), pending["version"].encode("utf-8"))
    _remove(pending_path)
    _clear(stage)

    log("# OTA: applied %s (%d files)" % (pending["version"], len(files)))

    return pending["version"]


def _boot_count(root):
    try:
        return int(_read(_join(root, "ota", "boots")).decode("utf-8").strip())
    except (OSError, ValueError):
        return 0


def restore(root="", log=print):
    """Put the backed-up files back after an update that never confirmed."""
    backup = _join(root, "ota", "backup")

    for name in _listdir(backup):
        _write(_join(root, name), _read(backup + "/" + name))

    _clear(backup)
    _remove(_join(root, "ota", "backup.json"))
    _remove(_join(root, "ota", "boots"))
    log("# OTA: rolled back to the previous firmware")


def confirm(root="", log=print):
    """Keep an applied update: the new firmware has stayed up long enough."""
    if not _exists(_join(root, "ota", "backup.json")):
        return False

    _clear(_join(root, "ota", "backup"))
    _remove(_join(root, "ota", "backup.json"))
    _remove(_join(root, "ota", "boots"))
    log("# OTA: update confirmed")

    return True


def reboot():
    """Restart into the applied firmware (a no-op error in the host tests)."""
    if machine is not None and hasattr(machine, "reset"):
        machine.reset()

    raise RuntimeError("machine.reset is not available")


def boot(root="", log=print):
    """Run from ``boot.py``: apply a staged bundle, or roll back a boot loop."""
    if _exists(_join(root, "ota", "backup.json")):
        count = _boot_count(root) + 1
        _write(_join(root, "ota", "boots"), str(count).encode("utf-8"))

        if count > BOOT_LIMIT:
            log("# OTA: %d boots without a confirmation, rolling back" % (count,))
            restore(root, log)
            reboot()

    pending_path = _join(root, "ota", "pending.json")

    if not _exists(pending_path):
        return False

    try:
        version = apply_pending(root, log)
    except Exception as error:
        log("# OTA: %s — keeping the current firmware" % (error,))
        _remove(pending_path)
        _clear(_join(root, "ota", "stage"))

        return False

    if version is None:
        return False

    reboot()

    return True


def _dechunk(raw):
    """Decode a ``Transfer-Encoding: chunked`` body held in memory."""
    body = b""

    while True:
        line, _, raw = raw.partition(b"\r\n")
        size = int(line.split(b";")[0].strip() or b"0", 16)

        if size == 0:
            return body

        body += raw[:size]
        raw = raw[size + 2:]


class Updater:
    """Check the API for a newer bundle and stage it in ``/ota``."""

    def __init__(self, config, root="", log=print):
        self.log = log
        self.root = root
        self.enabled = bool(getattr(config, "OTA_ENABLED", True))
        self.channel = str(getattr(config, "OTA_CHANNEL", "max31855"))
        self.key = getattr(config, "OTA_API_KEY", "") or getattr(config, "API_KEY", "") or ""
        self.timeout = float(getattr(config, "OTA_TIMEOUT_S", 10))
        self.verify = bool(getattr(config, "TLS_VERIFY", False))

        if not valid_channel(self.channel):
            raise ValueError("OTA_CHANNEL must be a bundle name such as max31855")

        base = getattr(config, "OTA_URL", "") or config.API_URL
        host, port, path, use_tls = ingest.split_url(base)
        self.host = host
        self.port = port
        self.use_tls = use_tls
        # API_URL may be the host or the complete /api/ingest URL.
        self.prefix = ingest.api_base_path(path)

        self.ready = ""          # version staged and waiting for a reboot
        self.last_error = ""
        self.last_version = ""

    # -- urls -------------------------------------------------------------

    def _path(self, kind, name=None):
        if kind == "manifest":
            return "%s/api/firmware/manifest?channel=%s" % (self.prefix, self.channel)

        return "%s/api/firmware/file?channel=%s&name=%s" % (self.prefix, self.channel, name)

    def manifest_url(self):
        return self._path("manifest")

    # -- http -------------------------------------------------------------

    def _get(self, path):
        """GET ``path`` over a fresh connection: (status, body bytes)."""
        if socket is None:
            raise OSError("this firmware build has no socket module")

        address = socket.getaddrinfo(self.host, self.port)[0][-1]
        sock = socket.socket()
        sock.settimeout(self.timeout)

        try:
            sock.connect(address)

            if self.use_tls:
                if ssl is None:
                    raise OSError("this firmware build has no ssl module")

                if self.verify:
                    sock = ssl.wrap_socket(
                        sock,
                        server_hostname=self.host,
                        cert_reqs=ssl.CERT_REQUIRED,
                    )
                else:
                    sock = ssl.wrap_socket(sock, server_hostname=self.host)

            lines = [
                "GET %s HTTP/1.1" % (path,),
                "Host: %s" % (self.host,),
                "Connection: close",
                "User-Agent: esp32-s3-furnace-ota/1.0",
            ]

            if self.key:
                lines.append("X-Api-Key: %s" % (self.key,))

            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))

            raw = b""

            while True:
                chunk = sock.recv(1024)

                if not chunk:
                    break

                raw += chunk

                if len(raw) > MAX_RESPONSE_BYTES:
                    raise OSError("response is too large")
        finally:
            try:
                sock.close()
            except Exception:
                pass

        head, _, body = raw.partition(b"\r\n\r\n")
        status, fields = ingest.parse_head(head)

        if "chunked" in fields.get("transfer-encoding", "").lower():
            body = _dechunk(body)

        return status, body

    # -- update check -----------------------------------------------------

    def _local_version(self):
        try:
            return _read(_join(self.root, "ota", "version")).decode("utf-8").strip()
        except OSError:
            return ""

    def _check(self):
        """Download and verify a bundle. True when a reboot is needed."""
        status, body = self._get(self._path("manifest"))

        if status == 404:
            self.last_error = "no bundle published"

            if self.last_error != self.last_version:
                self.log("# OTA: no bundle published for %r yet" % (self.channel,))

            self.last_version = self.last_error
            return False

        if status in (401, 403):
            self.last_error = "HTTP %d" % (status,)
            self.log("# OTA: the API rejected the key; check OTA_API_KEY / API_KEY")
            return False

        if status != 200:
            raise OSError("manifest request answered HTTP %d" % (status,))

        manifest = parse_manifest(json.loads(body.decode("utf-8")))
        version = manifest["version"]

        if version == self._local_version():
            self.last_version = version
            return False

        stage = _join(self.root, "ota", "stage")
        _mkdirs(self.root)
        _clear(stage)

        needed = 0
        staged = []

        for entry in manifest["files"]:
            name = entry["name"]
            local_path = _join(self.root, name)
            local_hash = sha256_hex(_read(local_path)) if _exists(local_path) else None

            if local_hash == entry["sha256"]:
                continue  # this file is already the published version

            needed += entry["size"]
            staged.append(entry)

        free = _free_bytes(self.root)

        if free is not None and needed > free - 65536:
            raise OSError("not enough free space: %d bytes needed" % (needed,))

        bundle_total = 0

        for entry in staged:
            status, blob = self._get(self._path("file", entry["name"]))

            if status == 404:
                raise OSError("bundle is missing %s on the server" % (entry["name"],))

            if status != 200:
                raise OSError("download of %s answered HTTP %d" % (entry["name"], status))

            if len(blob) != entry["size"]:
                raise OSError("%s is %d bytes, manifest says %d" % (
                    entry["name"], len(blob), entry["size"]))

            if sha256_hex(blob) != entry["sha256"]:
                raise OSError("checksum mismatch for %s" % (entry["name"],))

            if entry["name"].endswith(".py"):
                compile(blob.decode("utf-8"), entry["name"], "exec")

            _write(stage + "/" + entry["name"], blob)
            bundle_total += len(blob)

            if bundle_total > MAX_BUNDLE_BYTES:
                raise OSError("bundle is larger than %d bytes" % (MAX_BUNDLE_BYTES,))

        if not staged:
            # Every file already matches: remember the version, no reboot.
            _write(_join(self.root, "ota", "version"), version.encode("utf-8"))
            self.last_version = version
            return False

        _write(
            _join(self.root, "ota", "pending.json"),
            json.dumps({"version": version, "files": staged}).encode("utf-8"),
        )

        self.ready = version
        self.last_version = version

        return True

    def check(self):
        """One check, blocking. Kept public so a console can force an update."""
        if not self.enabled:
            return False

        try:
            return self._check()
        except Exception as error:
            self.last_error = str(error)
            raise

    def local_version(self):
        """Version currently on the board ('' when it was never updated)."""
        return self._local_version()

    def stats(self):
        return "channel=%s version=%s%s" % (
            self.channel,
            self._local_version() or "unknown",
            " ready=" + self.ready if self.ready else "",
        )
