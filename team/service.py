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

from model import FRAUD_TOPIC, MAINTENANCE_TOPIC, MAINTAINABLE, SCENARIO_TOPIC, SCENARIOS, NS, Model

LOG = logging.getLogger("stepnoe")
WEB = Path(__file__).with_name("web")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, timestamp REAL, virtual_time REAL, source TEXT, command INTEGER, physical REAL, book REAL, status TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS maintenance_audit (id INTEGER PRIMARY KEY, timestamp REAL, virtual_time REAL, source TEXT, thing TEXT, enabled INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS scenario_audit (id INTEGER PRIMARY KEY, timestamp REAL, virtual_time REAL, source TEXT, scenario TEXT, enabled INTEGER, measured REAL, book REAL, reason TEXT)")
        self.db.commit()

    def load(self, mode, **parameters):
        row = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        model = Model.restore(json.loads(row[0])) if row else Model()
        model.fraud_mode = mode
        for name, value in parameters.items():
            setattr(model, name, value)
        model.__post_init__()
        bound = model.lidar_noise_pct / 100
        model.lidar_noise_fraction = max(-bound, min(bound, model.lidar_noise_fraction))
        return model

    def save(self, model, commands=(), maintenance_commands=(), scenario_commands=()):
        a = model.audit()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES(1, ?)", (json.dumps(model.dump()),))
            for source, value in commands:
                self.db.execute("INSERT INTO audit(timestamp,virtual_time,source,command,physical,book,status) VALUES(?,?,?,?,?,?,?)",
                    (time.time(), model.time, source, value, a["physical_stock"], a["book_stock"], a["status"]))
            for source, thing, enabled in maintenance_commands:
                self.db.execute("INSERT INTO maintenance_audit(timestamp,virtual_time,source,thing,enabled) VALUES(?,?,?,?,?)",
                                (time.time(), model.time, source, thing, enabled))
            for source, scenario, enabled in scenario_commands:
                self.db.execute('INSERT INTO scenario_audit(timestamp,virtual_time,source,scenario,enabled,measured,book,reason) VALUES(?,?,?,?,?,?,?,?)',
                                (time.time(), model.time, source, scenario, enabled, a['measured_stock'], a['book_stock'], a['audit_reason']))

    def events(self):
        keys = ["id", "timestamp", "virtual_time", "source", "command", "physical", "book", "status"]
        return [dict(zip(keys, row)) for row in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]

    def maintenance_events(self):
        keys = ["id", "timestamp", "virtual_time", "source", "thing", "enabled"]
        return [dict(zip(keys, row)) for row in self.db.execute("SELECT * FROM maintenance_audit ORDER BY id DESC LIMIT 100")]

    def scenario_events(self):
        keys = ['id', 'timestamp', 'virtual_time', 'source', 'scenario', 'enabled', 'measured', 'book', 'reason']
        return [dict(zip(keys, row)) for row in self.db.execute('SELECT * FROM scenario_audit ORDER BY id DESC LIMIT 100')]


class Runtime:
    def __init__(self, store, mode="fixed", platform=None, **parameters):
        self.lock = threading.RLock()
        self.store, self.model = store, store.load(mode, **parameters)
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
                    "fraud": self.model.fraud, "fraud_direction": self.model.fraud_direction, "fraud_mode": self.model.fraud_mode,
                    "mqtt_connected": self.connected, "telemetry_age": time.monotonic() - self.last_publish,
                    "audit": self.model.audit(), "twins": self.model.telemetry(), "history": list(self.history)}

    def healthy(self):
        return (self.connected and self.subscribed and time.monotonic() - self.last_publish < 30
                and time.monotonic() - self.last_tick < 30 and (not self.platform or self.platform.ready()))

    def queue_control(self, topic, payload, source="mqtt"):
        if topic == FRAUD_TOPIC and payload in (b"-1", b"0", b"1"):
            self.controls.put_nowait((payload, source))
            return
        if topic.startswith(MAINTENANCE_TOPIC):
            thing = topic.removeprefix(MAINTENANCE_TOPIC)
            if thing in MAINTAINABLE and payload in (b"0", b"1"):
                self.controls.put_nowait((payload, source, thing))
                return
        if topic.startswith(SCENARIO_TOPIC):
            scenario = topic.removeprefix(SCENARIO_TOPIC)
            if scenario in SCENARIOS and payload in (b'0', b'1'):
                self.controls.put_nowait((payload, source, scenario))
                return
        raise ValueError("invalid MQTT control")

    def advance(self, seconds):
        with self.lock:
            while True:
                try:
                    payload, source, *target = self.controls.get_nowait()
                except queue.Empty:
                    break
                if target:
                    if target[0] in SCENARIOS:
                        self.model.set_scenario(target[0], payload == b'1')
                        self.store.save(self.model, scenario_commands=[(source, target[0], int(payload))])
                    else:
                        self.model.set_maintenance(target[0], payload == b"1")
                        self.store.save(self.model, maintenance_commands=[(source, target[0], int(payload))])
                else:
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
                          "revision": revision, "fraud": bool(values.get("fraud_active", values["fraud_offset"] != 0)),
                          "fraud_direction": values.get("fraud_direction", -1 if values["fraud_offset"] < 0 else 1),
                          "fraud_mode": values.get("fraud_mode", os.getenv("FRAUD_MODE", "fixed")), "audit": values,
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

        def route_path(self):
            path = urlsplit(self.path).path
            # Nginx strips /stepnoe. Accept it directly for local previews too.
            return path.removeprefix("/stepnoe") if path.startswith("/stepnoe/") else path

        def respond(self, status, body, content_type="application/json"):
            if status == 204:
                raw = b""
            elif isinstance(body, bytes):
                raw = body
            else:
                raw = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.route_path().startswith("/api/"):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Grafana-Org-Id")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            if self.route_path() in ("/api/fraud", "/api/state", "/api/audit", "/api/maintenance", '/api/scenarios'):
                self.respond(204, None)
            else:
                self.respond(404, {"error": "not found"})

        def do_GET(self):
            path = self.route_path()
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
            elif path == "/api/maintenance":
                try:
                    state = runtime.snapshot()
                except RuntimeError:
                    self.respond(503, {"error": "waiting for platform telemetry in Ditto"})
                    return
                with runtime.lock:
                    events = runtime.store.maintenance_events()
                self.respond(200, {"objects": {thing: bool(state["twins"][thing].get("maintenance", {}).get("properties", {}).get("value", 0)) for thing in MAINTAINABLE}, "events": events})
            elif path == '/api/scenarios':
                try:
                    state = runtime.snapshot()
                except RuntimeError:
                    self.respond(503, {'error': 'waiting for platform telemetry in Ditto'})
                    return
                with runtime.lock:
                    events = runtime.store.scenario_events()
                self.respond(200, {'objects': {name: bool(state['audit'].get('scenario_' + name, 0)) for name in SCENARIOS}, 'events': events})
            else:
                asset = (WEB / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
                content_types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".txt": "text/plain; charset=utf-8"}
                if not asset.is_relative_to(WEB.resolve()) or not asset.is_file() or asset.suffix not in content_types:
                    self.respond(404, {"error": "not found"})
                    return
                self.respond(200, asset.read_bytes(), content_types[asset.suffix])

        def do_POST(self):
            path = self.route_path()
            if path not in ("/api/fraud", "/api/maintenance", '/api/scenarios'):
                self.respond(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                    raise ValueError("expected a small JSON object")
                body = json.loads(self.rfile.read(length))
                value = body.get("value")
                maintenance = path == "/api/maintenance"
                scenario = body.get('scenario') if path == '/api/scenarios' else None
                if path == '/api/scenarios' and scenario not in SCENARIOS:
                    raise ValueError('unsupported scenario')
                if type(value) is not int or value not in ((0, 1) if maintenance or scenario else (-1, 0, 1)):
                    raise ValueError("invalid command value")
                thing = body.get("thing")
                if maintenance and thing not in MAINTAINABLE:
                    raise ValueError("unsupported maintenance target")
                topic = SCENARIO_TOPIC + scenario if scenario else MAINTENANCE_TOPIC + thing if maintenance else FRAUD_TOPIC
            except (ValueError, AttributeError):
                error = 'use {"thing":"Processing_Plant|Truck_01|Truck_02","value":0|1}' if path == "/api/maintenance" else 'use {"value":-1}, {"value":0} or {"value":1}'
                self.respond(400, {"error": 'use {"scenario":"plant_erp|sensor_bias|rain","value":0|1}' if path == '/api/scenarios' else error})
                return
            if not runtime.healthy():
                self.respond(503, {"error": "MQTT/model not ready"})
                return
            try:
                info = runtime.client.publish(topic, str(value), qos=1, retain=True)
                info.wait_for_publish(timeout=5)
                if not info.is_published():
                    raise TimeoutError()
            except (RuntimeError, TimeoutError):
                self.respond(503, {"error": "MQTT command not acknowledged"})
                return
            self.respond(202, {"accepted": value, "topic": topic, **({"thing": thing} if maintenance else {}), **({'scenario': scenario} if scenario else {})})
    return Handler


def main():
    import paho.mqtt.client as mqtt
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tick = float(os.getenv("TICK_SECONDS", "1"))
    speed = float(os.getenv("SIM_SPEEDUP", "30"))
    mode = os.getenv("FRAUD_MODE", "fixed")
    parameters = {name: float(os.environ[key]) for name, key in
                  (("moisture_pct", "MOISTURE_PCT"), ("drift_rate_tph", "FRAUD_DRIFT_RATE_TPH"), ("lidar_noise_pct", "LIDAR_NOISE_PCT"),
                   ('max_stock_capacity', 'MAX_STOCK_CAPACITY'), ('max_buffer_capacity', 'MAX_BUFFER_CAPACITY'),
                   ('false_alarm_probability', 'SENSOR_FALSE_ALARM_PROBABILITY')) if os.getenv(key)}
    if os.getenv('MAX_TRUCK_QUEUE'):
        parameters['max_truck_queue'] = int(os.environ['MAX_TRUCK_QUEUE'])
    if not math.isfinite(tick) or not .1 <= tick <= 5 or not math.isfinite(speed) or not 0 < speed <= 3600 / tick or mode not in ("fixed", "percent", "drift"):
        raise ValueError("invalid TICK_SECONDS, SIM_SPEEDUP or FRAUD_MODE")
    store = Store(os.getenv("STATE_DB", "/data/stepnoe.db"))
    stop = threading.Event()
    platform = DittoReader(os.getenv("DITTO_URL", "http://ditto-nginx:80"), os.environ["DITTO_PASSWORD"])
    runtime = Runtime(store, mode, platform, **parameters)
    threading.Thread(target=platform.run, args=(stop,), daemon=True).start()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="stepnoe-model", protocol=mqtt.MQTTv311)
    runtime.client = client

    def on_connect(client, _userdata, _flags, reason, _properties):
        runtime.connected = not reason.is_failure
        runtime.subscribed = False
        if runtime.connected:
            client.subscribe([(FRAUD_TOPIC, 1), (MAINTENANCE_TOPIC + "+", 1), (SCENARIO_TOPIC + '+', 1)])
            LOG.info("MQTT connected; subscribing to control topic")

    def on_subscribe(_client, _userdata, _mid, reasons, _properties):
        runtime.subscribed = all(not code.is_failure for code in reasons)

    def on_disconnect(_client, _userdata, _flags, _reason, _properties):
        runtime.connected = runtime.subscribed = False

    def on_message(_client, _userdata, msg):
        try:
            runtime.queue_control(msg.topic, msg.payload, "mqtt-retained" if msg.retain else "mqtt")
        except ValueError:
            LOG.warning("Rejected invalid MQTT control")
        except queue.Full:
            LOG.error("Control queue full")

    client.on_connect, client.on_subscribe = on_connect, on_subscribe
    client.on_disconnect, client.on_message = on_disconnect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    client.connect_async(os.getenv("MQTT_HOST", "mosquitto"), int(os.getenv("MQTT_PORT", "1883")), keepalive=30)
    client.loop_start()
    server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("HTTP_PORT", "8085"))), make_handler(runtime))
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
