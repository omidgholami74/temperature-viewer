"""Batched HTTP ingest client for MicroPython on the Pico W."""

import time

try:
    import ujson as json
except ImportError:  # pragma: no cover - CPython fallback
    import json

try:
    import urequests as requests
except ImportError:  # pragma: no cover - newer MicroPython naming
    import requests


class ApiClient:
    """POSTs sample batches to the PHP backend."""

    def __init__(self, base_url, api_key="", retries=2):
        self.url = base_url.rstrip("/") + "/api/ingest"
        self.api_key = api_key
        self.retries = retries

    def post_samples(self, samples):
        """Return True when the batch was stored (or accepted as duplicate)."""
        if not samples:
            return True

        payload = json.dumps({"samples": samples})
        headers = {"Content-Type": "application/json"}

        if self.api_key:
            headers["X-Api-Key"] = self.api_key

        for attempt in range(self.retries + 1):
            response = None

            try:
                response = requests.post(self.url, data=payload, headers=headers)
                status = response.status_code

                try:
                    body = response.text[:120]
                except Exception:
                    body = ""

                if status == 200:
                    return True

                print("API", status, body)

                # A rejected payload will not become valid by retrying.
                if 400 <= status < 500:
                    return False
            except OSError as error:
                print("API error:", error)
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

            time.sleep_ms(200 * (attempt + 1))

        return False
