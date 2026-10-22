"""MQTT -> physical model -> Ditto. Local HTTP API powers embedded Grafana views."""
import base64
import json
import logging
import math
import os
import queue
import signal
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from model import FRAUD_TOPIC, NS, Model

LOG = logging.getLogger("stepnoe")
WEB = Path(__file__).with_name("web")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, timestamp REAL, virtual_time REAL, source TEXT, command INTEGER, physical REAL, book REAL, status TEXT)")
        self.db.commit()

    def load(self, mode):
        row = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        model = Model.restore(json.loads(row[0])) if row else Model()
        model.fraud_mode = mode
        return model

    def save(self, model, commands=()):
        a = model.audit()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1, ?)", (json.dumps(model.dump()),))
            for source, value in commands:
                self.db.execute("INSERT INTO audit(timestamp,virtual_time,source,command,physical,book,status) VALUES(?,?,?,?,?,?,?)",
                    (time.time(), model.time, source, value, a["physical_stock"], a["book_stock"], a["status"]))

    def events(self):
        keys = ["id", "timestamp", "virtual_time", "source", "command", "physical", "book", "status"]
        return [dict(zip(keys, row)) for row in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]


class Runtime:
    def __init__(self, store, mode="fixed", platform=None):
        self.lock = threading.RLock()
        self.store, self.model = store, store.load(mode)
        self.controls = queue.Queue(maxsize=100)
        self.history = deque(maxlen=900)
        self.connected = False
        self.subscribed = False
        self.last_publish = 0.
        self.last_tick = 0.
        self.client = None
        self.platform = platform

    def snapshot(self):
        if self.platform:
            result = self.platform.snapshot()
            result["mqtt_connected"] = self.connected
            return result
        with self.lock:
            return {"timestamp": time.time(), "virtual_time": self.model.time, "revision": self.model.revision,
                    "fraud": self.model.fraud, "fraud_mode": self.model.fraud_mode,
                    "mqtt_connected": self.connected, "telemetry_age": time.monotonic() - self.last_publish,
                    "audit": self.model.audit(), "twins": self.model.telemetry(), "history": list(self.history)}

    def healthy(self):
        return (self.connected and self.subscribed and time.monotonic() - self.last_publish < 30
                and time.monotonic() - self.last_tick < 30 and (not self.platform or self.platform.ready()))

    def advance(self, seconds):
        with self.lock:
            while True:
                try:
                    payload, source = self.controls.get_nowait()
                except queue.Empty:
                    break
                self.model.control(payload)
                # Store each transition separately, including 1 -> 0 in one tick.
                self.store.save(self.model, [(source, int(payload))])
            self.model.step(seconds)
            self.store.save(self.model)
            a = self.model.audit()
            self.history.append({"time": self.model.time, **a})
            self.last_tick = time.monotonic()
            return self.model.telemetry()

    def publish(self, twins):
        if not self.connected:
            return
        pending = []
        for name, features in twins.items():
            message = {"topic": f"{NS}/{name}/things/twin/commands/modify", "path": "/features",
                       "headers": {"response-required": False}, "value": features}
            pending.append(self.client.publish(f"telemetry/{name}", json.dumps(message), qos=1))
        for info in pending:
            info.wait_for_publish(timeout=5)
            if not info.is_published():
                raise TimeoutError("MQTT publish not acknowledged")
        self.last_publish = time.monotonic()


class DittoReader:
    """The user interface reads platform twins, never the simulator's memory.

    A single Ditto bulk GET supplies all twins. Cache is updated independently
    so HTTP clients do not multiply platform requests or block the model.
    """
    def __init__(self, url, password):
        twins = json.loads(Path(__file__).with_name("twins.json").read_text(encoding="utf-8"))
        self.expected = {thing_id.split(":", 1)[1] for thing_id in twins}
        self.url = url.rstrip("/") + "/api/2/things?" + urlencode({"ids": ",".join(twins)})
        self.auth = "Basic " + base64.b64encode(f"ditto:{password}".encode()).decode()
        self.lock = threading.Lock()
        self.state = None
        self.last_change = 0.
        self.history = deque(maxlen=900)

    def refresh(self):
        request = Request(self.url, headers={"Authorization": self.auth})
        with urlopen(request, timeout=15) as response:
            things = json.load(response)
        twins = {item["thingId"].split(":", 1)[1]: item["features"] for item in things}
        if not self.expected.issubset(twins):
            raise ValueError("incomplete platform snapshot")
        values = {name: f["properties"]["value"] for name, f in twins["Mass_Balance"].items()}
        revision = values.pop("revision")
        virtual_time = values.pop("simulation_time")
        with self.lock:
            if not self.state or self.state["revision"] != revision:
                self.last_change = time.monotonic()
                self.history.append({"time": virtual_time, **values})
            self.state = {"source": "Eclipse Ditto", "timestamp": time.time(), "virtual_time": virtual_time,
                          "revision": revision, "fraud": values["fraud_offset"] > 0,
                          "fraud_mode": os.getenv("FRAUD_MODE", "fixed"), "audit": values,
                          "twins": twins}

    def snapshot(self):
        with self.lock:
            if self.state is None:
                raise RuntimeError("waiting for platform telemetry in Ditto")
            return {**self.state, "telemetry_age": time.monotonic() - self.last_change, "history": list(self.history)}

    def ready(self):
        with self.lock:
            return self.state is not None and time.monotonic() - self.last_change < 30

    def run(self, stop):
        while not stop.is_set():
            try:
                self.refresh()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                LOG.warning("Ditto UI read pending: %s", type(exc).__name__)
            stop.wait(2)


def make_handler(runtime):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, body, content_type="application/json"):
            raw = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.respond(200 if runtime.healthy() else 503, {"ready": runtime.healthy()})
            elif path == "/api/state":
                try:
                    self.respond(200, runtime.snapshot())
                except RuntimeError:
                    self.respond(503, {"error": "waiting for platform telemetry in Ditto"})
            elif path == "/api/audit":
                with runtime.lock:
                    self.respond(200, runtime.store.events())
            else:
                names = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                name = names.get(path)
                if not name:
                    self.respond(404, {"error": "not found"})
                    return
                content_type = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}[name.rsplit(".", 1)[1]]
                self.respond(200, (WEB / name).read_bytes(), content_type)

        def do_POST(self):
            if self.path != "/api/fraud":
                self.respond(404, {"error": "not found"})
                return
            # API is local to the stand. Reject cross-site browser writes.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.respond(403, {"error": "cross-origin control refused"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                    raise ValueError("expected a small JSON object")
                value = json.loads(self.rfile.read(length)).get("value")
                if type(value) is not int or value not in (0, 1):
                    raise ValueError("value must be integer 0 or 1")
            except (ValueError, AttributeError):
                self.respond(400, {"error": "use {\"value\":0} or {\"value\":1}"})
                return
            if not runtime.healthy():
                self.respond(503, {"error": "MQTT/model not ready"})
                return
            info = runtime.client.publish(FRAUD_TOPIC, str(value), qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=5)
                if not info.is_published():
                    raise TimeoutError()
            except (RuntimeError, TimeoutError):
                self.respond(503, {"error": "MQTT command not acknowledged"})
                return
            self.respond(202, {"accepted": value, "topic": FRAUD_TOPIC})
    return Handler


def main():
    import paho.mqtt.client as mqtt
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tick = float(os.getenv("TICK_SECONDS", "1"))
    speed = float(os.getenv("SIM_SPEEDUP", "30"))
    mode = os.getenv("FRAUD_MODE", "fixed")
    if not math.isfinite(tick) or not .1 <= tick <= 5 or not math.isfinite(speed) or not 0 < speed <= 3600 / tick or mode not in ("fixed", "percent"):
        raise ValueError("invalid TICK_SECONDS, SIM_SPEEDUP or FRAUD_MODE")
    store = Store(os.getenv("STATE_DB", "/data/stepnoe.db"))
    stop = threading.Event()
    platform = DittoReader(os.getenv("DITTO_URL", "http://ditto-nginx:80"), os.environ["DITTO_PASSWORD"])
    runtime = Runtime(store, mode, platform)
    threading.Thread(target=platform.run, args=(stop,), daemon=True).start()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="stepnoe-model", protocol=mqtt.MQTTv311)
    runtime.client = client

    def on_connect(client, _userdata, _flags, reason, _properties):
        runtime.connected = not reason.is_failure
        runtime.subscribed = False
        if runtime.connected:
            client.subscribe(FRAUD_TOPIC, qos=1)
            LOG.info("MQTT connected; subscribing to control topic")

    def on_subscribe(_client, _userdata, _mid, reasons, _properties):
        runtime.subscribed = all(not code.is_failure for code in reasons)

    def on_disconnect(_client, _userdata, _flags, _reason, _properties):
        runtime.connected = runtime.subscribed = False

    def on_message(_client, _userdata, msg):
        if msg.topic != FRAUD_TOPIC or msg.payload not in (b"0", b"1"):
            LOG.warning("Rejected invalid MQTT control")
            return
        try:
            runtime.controls.put_nowait((msg.payload, "mqtt-retained" if msg.retain else "mqtt"))
        except queue.Full:
            LOG.error("Control queue full")

    client.on_connect, client.on_subscribe = on_connect, on_subscribe
    client.on_disconnect, client.on_message = on_disconnect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    client.connect_async(os.getenv("MQTT_HOST", "mosquitto"), int(os.getenv("MQTT_PORT", "1883")), keepalive=30)
    client.loop_start()
    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("HTTP_PORT", "8000"))), make_handler(runtime))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    LOG.info("Stepnoe started: synthetic data, speedup=%s, fraud_mode=%s", speed, mode)
    try:
        while not stop.is_set():
            # Pause virtual time during outages; resume with saved inventories.
            if runtime.connected and runtime.subscribed:
                twins = runtime.advance(tick * speed)
                try:
                    runtime.publish(twins)
                except (TimeoutError, RuntimeError) as exc:
                    LOG.warning("Telemetry retry on next tick: %s", exc)
            stop.wait(tick)
    finally:
        server.shutdown()
        client.disconnect()
        client.loop_stop()
        store.db.close()


if __name__ == "__main__":
    main()
