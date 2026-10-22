"""Real-stack acceptance check. Run with make team-smoke after make up."""
import base64
import csv
import io
import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt
from model import CRITICAL, FRAUD_TOPIC, NS, OK


def http(url, user=None, password=None, data=None, content_type="application/json", token=None):
    headers = {"Content-Type": content_type}
    if user:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    if token:
        headers["Authorization"] = "Token " + token
    with urlopen(Request(url, headers=headers, data=data), timeout=15) as response:
        raw = response.read().decode()
        return json.loads(raw) if content_type == "application/json" else raw


def wait_for(label, check, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if check():
                print("PASS", label, flush=True)
                return
        except (URLError, TimeoutError, KeyError, ValueError):
            pass
        time.sleep(2)
    raise AssertionError(f"Timed out: {label}")


def main():
    ditto_password, grafana_password, token = [os.environ[k] for k in ("DITTO_PASSWORD", "GRAFANA_ADMIN_PASSWORD", "INFLUXDB_TOKEN")]
    state = lambda: http("http://127.0.0.1:8000/api/state")
    thing = lambda name: http(f"http://ditto-nginx/api/2/things/{NS}:{name}", "ditto", ditto_password)
    wait_for("service + MQTT ready", lambda: http("http://127.0.0.1:8000/health")["ready"])
    wait_for("UI reads real Ditto twins", lambda: state()["source"] == "Eclipse Ditto")
    for thing_id, body in json.loads(Path("twins.json").read_text()).items():
        actual = thing(thing_id.split(":")[1])
        assert actual["attributes"].get("_parents") == body["attributes"].get("_parents")
    print("PASS 14 Ditto twins and hierarchy", flush=True)
    for uid in ("stepnoe-overview", "stepnoe-audit", "stepnoe-logistics"):
        http(f"http://grafana:3000/api/dashboards/uid/{uid}", "admin", grafana_password)
    print("PASS three provisioned Grafana dashboards", flush=True)
    wait_for("haul and processing cycles", lambda: state()["twins"]["Processing_Plant"]["raw_inflow"]["properties"]["value"] > 0)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"stepnoe-smoke-{os.getpid()}")
    client.connect("mosquitto", 1883)
    client.loop_start()

    def set_fraud(value):
        client.publish(FRAUD_TOPIC, str(value), qos=1, retain=True).wait_for_publish(10)
        wait_for(f"MQTT control {value} -> model", lambda: state()["fraud"] == bool(value))
        wait_for(f"model -> Ditto anomaly {value}", lambda: thing("Mass_Balance")["features"]["anomaly"]["properties"]["value"] == value)
        def influx_matches():
            flux = (f'from(bucket:"default") |> range(start:-2m) |> filter(fn:(r)=>r.thingId=="{NS}:Mass_Balance" and r._field=="value_anomaly_properties_value") |> group() |> last()')
            raw = http("http://influxdb:8086/api/v2/query?org=opentwins", data=flux.encode(), content_type="application/vnd.flux", token=token)
            csv_text = "\n".join(line for line in raw.splitlines() if line and not line.startswith("#"))
            rows = [r for r in csv.DictReader(io.StringIO(csv_text)) if r.get("_value")]
            return bool(rows) and float(rows[-1]["_value"]) == value
        wait_for(f"Ditto -> Telegraf -> InfluxDB anomaly {value}", influx_matches)

    try:
        set_fraud(0)
        assert state()["audit"]["status"] == OK
        set_fraud(1)
        a = state()["audit"]
        assert a["status"] == CRITICAL and a["fraud_offset"] > 0
        assert abs(a["physical_residual"]) < 1e-6 and abs(a["fe_residual"]) < 1e-6
        set_fraud(0)
        assert state()["audit"]["status"] == OK
        assert len(http("http://127.0.0.1:8000/api/audit")) >= 3
        print("PASS anti-fraud, conserved mass/Fe, reset and persistent audit trail", flush=True)
    finally:
        client.publish(FRAUD_TOPIC, "0", qos=1, retain=True).wait_for_publish(10)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
