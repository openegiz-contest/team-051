"""Idempotent installation; restarting never resets existing sensor features."""
import base64
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def setup():
    url = os.environ.get("DITTO_URL", "http://ditto-nginx:80").rstrip("/")
    auth = base64.b64encode(f"ditto:{os.environ['DITTO_PASSWORD']}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    twins = json.loads(Path(__file__).with_name("twins.json").read_text(encoding="utf-8"))
    for thing_id, body in twins.items():
        endpoint = f"{url}/api/2/things/{quote(thing_id, safe=':')}"
        for attempt in range(60):
            try:
                try:
                    with urlopen(Request(endpoint, headers=headers), timeout=15):
                        exists = True
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                    exists = False
                target, payload = (endpoint + "/attributes", body["attributes"]) if exists else (endpoint, body)
                with urlopen(Request(target, data=json.dumps(payload).encode(), headers=headers, method="PUT"), timeout=15):
                    print(f"ready {thing_id}", flush=True)
                break
            except (URLError, TimeoutError):
                if attempt == 59:
                    raise
                time.sleep(3)


if __name__ == "__main__":
    setup()
