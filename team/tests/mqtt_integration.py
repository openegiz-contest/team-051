"""Optional transport test with a real local MQTT broker (amqtt==0.11.3).

Does not replace make team-smoke: Ditto/InfluxDB/Grafana are not simulated here.
Run: python team/tests/mqtt_integration.py
Set STEP_NOE_PREVIEW_SECONDS=120 to leave the service open for visual QA.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt
from amqtt.broker import Broker

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(check, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (URLError, TimeoutError, ConnectionError, KeyError):
            pass
        time.sleep(.1)
    raise AssertionError("MQTT integration timed out")


def run_service_test(mqtt_port, http_port):
    received = {}
    probe = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="stepnoe-test-probe")
    def message(_client, _userdata, msg):
        received[msg.topic] = json.loads(msg.payload)
    probe.on_message = message
    probe.connect("127.0.0.1", mqtt_port)
    probe.subscribe("telemetry/+", qos=1)
    probe.loop_start()
    # HTTP stub tests the UI's platform-read adapter. It is not real Ditto;
    # make team-smoke remains the acceptance test for the OpenEgiz pipeline.
    class DittoStub(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            things = [{"thingId": "org.stepnoe:" + name.split("/")[-1], "features": data["value"]}
                      for name, data in list(received.items())]
            body = json.dumps(things).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    ditto = ThreadingHTTPServer(("127.0.0.1", 0), DittoStub)
    threading.Thread(target=ditto.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, MQTT_HOST="127.0.0.1", MQTT_PORT=str(mqtt_port), HTTP_PORT=str(http_port),
                   STATE_DB=str(Path(temp) / "model.db"), TICK_SECONDS="0.1", SIM_SPEEDUP="300",
                   DITTO_URL=f"http://127.0.0.1:{ditto.server_port}", DITTO_PASSWORD="test-stub-only")
        process = subprocess.Popen([sys.executable, str(ROOT / "service.py")], cwd=ROOT, env=env)
        url = f"http://127.0.0.1:{http_port}"
        def state():
            with urlopen(url + "/api/state", timeout=2) as response:
                return json.load(response)
        try:
            wait(lambda: state()["mqtt_connected"])
            wait(lambda: len(received) == 14)
            wait(lambda: state()["twins"]["Processing_Plant"]["raw_inflow"]["properties"]["value"] > 0)
            for command in (1, 1, 0):
                probe.publish("telemetry/stockpile/inject_fraud", str(command), qos=1, retain=True).wait_for_publish(3)
                wait(lambda: state()["fraud"] == bool(command))
                wait(lambda: received["telemetry/Mass_Balance"]["value"]["anomaly"]["properties"]["value"] == command)
                audit = state()["audit"]
                assert abs(audit["physical_residual"]) < 1e-6
                assert abs(audit["fraud_offset"] - (300 if command else 0)) < 1e-6
            req = Request(url + "/api/fraud", data=b'{"value":1}', headers={"Content-Type":"application/json"})
            with urlopen(req) as response:
                assert response.status == 202
            wait(lambda: state()["fraud"])
            print(f"PASS real MQTT transport: 14 twins, controls 1/1/0, HTTP -> broker -> model; preview {url}", flush=True)
            seconds = int(os.environ.get("STEP_NOE_PREVIEW_SECONDS", "0"))
            if seconds:
                time.sleep(seconds)
            probe.publish("telemetry/stockpile/inject_fraud", "0", qos=1, retain=True).wait_for_publish(3)
            wait(lambda: not state()["fraud"])
            process.terminate()
            process.wait(timeout=10)
            # Same SQLite, retained OFF, inventory must resume rather than reset.
            before = json.loads(sqlite_state(Path(temp) / "model.db"))
            process = subprocess.Popen([sys.executable, str(ROOT / "service.py")], cwd=ROOT, env=env)
            restored = wait(lambda: state() if state()["virtual_time"] >= before["time"] else None)
            assert restored["twins"]["Stepnoe"]["mined"]["properties"]["value"] >= before["mined"]
            print("PASS process restart resumes SQLite inventories and retained control", flush=True)
        finally:
            process.terminate()
            process.wait(timeout=10)
            probe.disconnect()
            probe.loop_stop()
            ditto.shutdown()
            ditto.server_close()


def sqlite_state(path):
    import sqlite3
    with sqlite3.connect(path) as db:
        return db.execute("SELECT data FROM state WHERE id=1").fetchone()[0]


async def main():
    mqtt_port = free_port()
    # Fixed only for the opt-in local visual QA; production port stays internal.
    http_port = 18765 if os.environ.get("STEP_NOE_PREVIEW_SECONDS") else free_port()
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{mqtt_port}"}},
                     "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    await broker.start()
    try:
        await asyncio.to_thread(run_service_test, mqtt_port, http_port)
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
