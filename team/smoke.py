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
    state = lambda: http("http://127.0.0.1:8085/api/state")
    thing = lambda name: http(f"http://ditto-nginx/api/2/things/{NS}:{name}", "ditto", ditto_password)
    def conserved(a):
        # Legacy checkpoints can contain months of accumulated floating-point
        # sums. Relative 1e-10 is 15 g at 150000 t; the audit threshold is 1 t.
        tolerance = max(1e-6, abs(a['inventory']) * 1e-10)
        assert all(abs(a[key]) < tolerance for key in ('physical_residual', 'fe_residual')), (a, tolerance)
    wait_for("service + MQTT ready", lambda: http("http://127.0.0.1:8085/health")["ready"])
    wait_for("UI reads real Ditto twins", lambda: state()["source"] == "Eclipse Ditto")
    for thing_id, body in json.loads(Path("twins.json").read_text()).items():
        actual = thing(thing_id.split(":")[1])
        assert actual["attributes"].get("_parents") == body["attributes"].get("_parents")
    print("PASS 18 Ditto twins, independent instruments and hierarchy", flush=True)
    connection = http('http://ditto-nginx/api/2/connections/mosquitto-source-connection', 'devops', os.environ['DITTO_DEVOPS_PASSWORD'])
    assert connection['sources'][0]['qos'] == 1
    print('PASS actual Ditto MQTT source QoS 1', flush=True)
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
        matches = lambda offset: offset * value > 0 if value else offset == 0
        wait_for(f"signed UI telemetry {value}", lambda: matches(state()["audit"]["fraud_offset"]))
        wait_for(f"model -> Ditto anomaly {value}", lambda: thing("Mass_Balance")["features"]["anomaly"]["properties"]["value"] == int(bool(value)))
        wait_for(f"model -> Ditto direction {value}", lambda: matches(thing("Mass_Balance")["features"]["fraud_offset"]["properties"]["value"]))
        def influx_matches():
            flux = (f'from(bucket:"default") |> range(start:-2m) |> filter(fn:(r)=>r.thingId=="{NS}:Mass_Balance" and r._field=="value_fraud_offset_properties_value") |> group() |> last()')
            raw = http("http://influxdb:8086/api/v2/query?org=opentwins", data=flux.encode(), content_type="application/vnd.flux", token=token)
            csv_text = "\n".join(line for line in raw.splitlines() if line and not line.startswith("#"))
            rows = [r for r in csv.DictReader(io.StringIO(csv_text)) if r.get("_value")]
            return bool(rows) and matches(float(rows[-1]["_value"]))
        wait_for(f"Ditto -> Telegraf -> InfluxDB signed offset {value}", influx_matches)

    try:
        set_fraud(0)
        assert state()["audit"]["status"] == OK
        set_fraud(1)
        a = state()["audit"]
        assert a["status"] == CRITICAL and a["fraud_offset"] > 0
        conserved(a)
        set_fraud(-1)
        a = state()["audit"]
        assert a["status"] == CRITICAL and a["fraud_offset"] < 0
        conserved(a)
        set_fraud(0)
        assert state()["audit"]["status"] == OK
        assert len(http("http://127.0.0.1:8085/api/audit")) >= 4
        print("PASS anti-fraud, conserved mass/Fe, reset and persistent audit trail", flush=True)
        a = state()['audit']
        assert a['scale_timestamp'] < state()['virtual_time'] and a['lidar_timestamp'] < state()['virtual_time']
        assert all(key in a for key in ('measured_stock', 'ground_truth_mass', 'lidar_dry_stock', 'audit_residual', 'audit_reason'))
        assert thing('Mass_Balance')['attributes']['ground_truth_used_for_decisions'] is False
        assert thing('Stockpile_01')['attributes']['measured_by']
        print('PASS independent delayed measurements, typed contracts and semantic links', flush=True)

        def scenario(name, enabled):
            reply = http('http://127.0.0.1:8085/api/scenarios', data=json.dumps({'scenario': name, 'value': enabled}).encode())
            assert reply['accepted'] == enabled
            wait_for(f'HTTP -> MQTT -> Ditto scenario {name}={enabled}',
                     lambda: state()['audit']['scenario_' + name] == enabled)

        scenario('plant_erp', 1)
        wait_for('plant stop -> full buffer -> two loaded trucks -> excavator IDLE',
                 lambda: state()['audit']['buffer_fill_pct'] >= 99.999 and
                 state()['audit']['truck_queue_length'] == 2 and state()['audit']['excavator_interlock'] == 1, timeout=180)
        a = state()['audit']
        assert a['audit_reason'] == 'ERP_DURING_DOWNTIME' and a['anomaly'] == 1
        assert state()['twins']['Processing_Plant']['raw_inflow_rate']['properties']['value'] == 0
        conserved(a)
        scenario('plant_erp', 0)
        wait_for('ERP reset and mill resumes without discarding inventory', lambda: state()['audit']['status'] == OK)
        scenario('sensor_bias', 1)
        wait_for('scale bias classified as instrument fault', lambda: state()['audit']['audit_reason'] == 'SENSOR_MISMATCH')
        assert state()['audit']['recommendation'] == 'Проверить измерительный канал'
        scenario('sensor_bias', 0)
        wait_for('sensor calibration reset confirmed', lambda: state()['audit']['status'] == OK)
        scenario('rain', 1)
        wait_for('rain: stale ERP moisture classified without a theft claim', lambda: state()['audit']['audit_reason'] == 'MOISTURE_ERP_MISMATCH')
        a = state()['audit']
        assert a['moisture_pct'] == 7 and a['erp_moisture_pct'] == 3
        assert a['recommendation'] == 'Требуется пересчет влажности / ошибка ERP'
        assert abs(a['wet_scale_reading'] / a['measured_stock'] - 1.07) < 1e-9
        assert abs(a['moisture_erp_offset'] - a['measured_stock'] * (1.07 / 1.03 - 1)) < 1e-7
        conserved(a)
        assert thing('Moisture_Sensor_01')['features']['moisture_pct']['properties']['value'] == 7
        assert thing('ERP_Register')['features']['registered_moisture_pct']['properties']['value'] == 3
        scenario('rain', 0)
        wait_for('rain reset and fresh humidity measurements confirmed', lambda: state()['audit']['status'] == OK)
        assert len(http('http://127.0.0.1:8085/api/scenarios')['events']) >= 4
        print('PASS plant_erp, sensor_bias and rain, safe reset and persistent scenario audit trail', flush=True)
    finally:
        for name in ('plant_erp', 'sensor_bias', 'rain'):
            http('http://127.0.0.1:8085/api/scenarios', data=json.dumps({'scenario': name, 'value': 0}).encode())
        client.publish(FRAUD_TOPIC, "0", qos=1, retain=True).wait_for_publish(10)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
