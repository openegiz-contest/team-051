import json
import io
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import FRAUD_TOPIC, MAINTENANCE_TOPIC, SCENARIO_TOPIC
from service import DittoReader, Runtime, Store, make_handler


class Ack:
    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return True


class CapturingMQTT:
    def __init__(self, runtime):
        self.runtime = runtime
        self.published = []

    def publish(self, topic, payload, qos, retain=False):
        self.published.append((topic, payload, qos, retain))
        if topic == FRAUD_TOPIC or topic.startswith(MAINTENANCE_TOPIC) or topic.startswith(SCENARIO_TOPIC):
            self.runtime.queue_control(topic, str(payload).encode(), "mqtt-test")
        return Ack()


class ServiceTests(unittest.TestCase):
    def test_cascade_scenario_api_mqtt_ditto_journal_and_cors(self):
        for name in ('plant_erp', 'sensor_bias', 'rain'):
            for value in (1, 0):
                code, reply = self.request('/stepnoe/api/scenarios', {'scenario': name, 'value': value})
                self.assertEqual(code, 202)
                self.assertEqual(reply['topic'], SCENARIO_TOPIC + name)
                self.runtime.advance(60)
                result = self.request('/api/scenarios')[1]
                self.assertEqual(result['objects'][name], bool(value))
                self.assertEqual(result['events'][0]['enabled'], value)
        self.assertEqual(len(self.store.scenario_events()), 6)
        self.assertEqual(self.store.events(), [])
        self.assertEqual(self.store.load('fixed').dump(), self.runtime.model.dump())
        with urlopen(Request(self.url + '/stepnoe/api/scenarios', method='OPTIONS')) as reply:
            self.assertEqual(reply.status, 204)
            self.assertEqual(reply.headers['Access-Control-Allow-Origin'], '*')
        for command in ({'scenario': 'bad', 'value': 1}, {'scenario': 'plant_erp', 'value': -1}, {'scenario': 'sensor_bias', 'value': True}):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/scenarios', command)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.db")
        self.store = Store(self.path)
        self.runtime = Runtime(self.store)
        self.runtime.client = CapturingMQTT(self.runtime)
        self.runtime.connected = self.runtime.subscribed = True
        self.runtime.last_publish = self.runtime.last_tick = time.monotonic()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.db.close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        req = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers or {"Content-Type": "application/json"})
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)

    def test_mqtt_envelopes_contain_all_features(self):
        self.runtime.publish(self.runtime.advance(60))
        for topic, raw, qos, retained in self.runtime.client.published:
            data = json.loads(raw)
            name = topic.split("/")[-1]
            self.assertEqual(data["topic"], f"org.stepnoe/{name}/things/twin/commands/modify")
            self.assertEqual(data["path"], "/features")
            self.assertEqual(data["value"], self.runtime.model.telemetry()[name])
            self.assertEqual(qos, 1)
            self.assertFalse(retained)

    def test_http_to_mqtt_to_audit_and_reset(self):
        code, result = self.request("/api/fraud", {"value": 1})
        self.assertEqual(code, 202)
        self.assertEqual(result["topic"], FRAUD_TOPIC)
        self.runtime.advance(30)
        self.assertEqual(self.request("/api/state")[1]["audit"]["anomaly"], 1)
        self.request("/api/fraud", {"value": 0})
        self.runtime.advance(30)
        self.assertEqual(self.request("/api/state")[1]["audit"]["anomaly"], 0)
        events = self.request("/api/audit")[1]
        self.assertEqual([e["command"] for e in events], [0, 1])
        restored = Store(self.path)
        self.assertEqual(restored.load("fixed").dump(), self.runtime.model.dump())
        restored.db.close()

    def test_prefixed_api_for_standalone_and_grafana_views(self):
        code, result = self.request("/stepnoe/api/fraud", {"value": 1})
        self.assertEqual(code, 202)
        self.assertEqual(result["topic"], FRAUD_TOPIC)
        self.runtime.advance(30)
        self.assertTrue(self.request("/stepnoe/api/state")[1]["fraud"])
        self.assertEqual(self.request("/stepnoe/api/audit")[1][0]["command"], 1)
        self.request("/stepnoe/api/fraud?source=grafana", {"value": 0})
        self.runtime.advance(30)
        self.assertFalse(self.request("/stepnoe/api/state")[1]["fraud"])

    def test_concealment_flows_through_http_mqtt_ditto_and_journal(self):
        code, result = self.request("/stepnoe/api/fraud", {"value": -1})
        self.assertEqual((code, result["accepted"]), (202, -1))
        self.runtime.advance(30)
        reader = DittoReader("http://ditto.test", "test-only")
        twins = [{"thingId": "org.stepnoe:" + name, "features": features}
                 for name, features in self.runtime.model.telemetry().items()]
        with patch("service.urlopen", return_value=io.BytesIO(json.dumps(twins).encode())):
            reader.refresh()
        self.runtime.platform = reader
        data = self.request("/stepnoe/api/state")[1]
        self.assertTrue(data["fraud"])
        self.assertEqual(data["fraud_direction"], -1)
        self.assertEqual(data["audit"]["fraud_offset"], -300)
        self.assertEqual(data["audit"]["anomaly"], 1)
        self.assertEqual(self.store.events()[0]["command"], -1)

    def test_mqtt_publish_failure_returns_json(self):
        with patch.object(self.runtime.client, "publish", side_effect=RuntimeError("disconnected")):
            with self.assertRaises(HTTPError) as err:
                self.request("/stepnoe/api/fraud", {"value": 1})
        self.assertEqual(err.exception.code, 503)
        self.assertEqual(err.exception.headers.get_content_type(), "application/json")
        self.assertEqual(err.exception.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(json.load(err.exception)["error"], "MQTT command not acknowledged")

    def test_separate_commands_in_same_tick_are_journaled(self):
        self.runtime.controls.put((b"1", "mqtt"))
        self.runtime.controls.put((b"0", "mqtt"))
        self.runtime.advance(30)
        events = self.store.events()
        self.assertEqual([e["book"] - e["physical"] for e in events], [0, 300])

    def test_http_input_validation(self):
        for data in ({"value": True}, {"value": "1"}, {"value": 2}, {}, []):
            with self.assertRaises(HTTPError) as err:
                self.request("/api/fraud", data)
            self.assertEqual(err.exception.code, 400)

    def test_cors_preflight_without_command_or_mqtt(self):
        self.runtime.connected = False
        for path in ("/api/fraud", "/stepnoe/api/fraud", "/stepnoe/api/state", "/stepnoe/api/audit", "/api/maintenance", "/stepnoe/api/maintenance"):
            request = Request(self.url + path, method="OPTIONS", headers={
                "Origin": "https://other.invalid", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-grafana-org-id"})
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.read(), b"")
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
                self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Content-Type, X-Grafana-Org-Id")
        self.assertEqual(self.runtime.client.published, [])

    def test_cross_origin_control_is_allowed(self):
        request = Request(self.url + "/stepnoe/api/fraud", data=b'{"value":1}', headers={
            "Content-Type": "application/json", "Origin": "https://other.invalid", "X-Grafana-Org-Id": "1"})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(json.load(response)["accepted"], 1)
        self.runtime.advance(30)
        self.assertTrue(self.runtime.model.fraud)

    def test_health_and_control_fail_when_mqtt_is_down(self):
        self.runtime.connected = False
        for path, data in (("/health", None), ("/api/fraud", {"value": 1})):
            with self.assertRaises(HTTPError) as err:
                self.request(path, data)
            self.assertEqual(err.exception.code, 503)

    def test_no_path_traversal(self):
        with self.assertRaises(HTTPError) as err:
            self.request("/../service.py")
        self.assertEqual(err.exception.code, 404)

    def test_web_modules_and_vendored_assets_are_served(self):
        for path in ("/stepnoe/scene.js", "/stepnoe/api.js", "/stepnoe/vendor/three.module.js", "/stepnoe/vendor/three.core.js"):
            with urlopen(self.url + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "text/javascript")
                self.assertGreater(len(response.read()), 100)
        with urlopen(self.url + "/stepnoe/package.json", timeout=3) as response:
            self.assertEqual(json.load(response)["type"], "module")
        with self.assertRaises(HTTPError) as err:
            self.request("/stepnoe/vendor/../../../service.py")
        self.assertEqual(err.exception.code, 404)

    def test_ui_reads_ditto_not_simulator_memory(self):
        reader = DittoReader("http://ditto.test", "test-only")
        twins = [{"thingId": "org.stepnoe:" + name, "features": features}
                 for name, features in self.runtime.model.telemetry().items()]
        with patch("service.urlopen", return_value=io.BytesIO(json.dumps(twins).encode())) as request:
            reader.refresh()
        self.assertIn("/api/2/things?ids=", request.call_args.args[0].full_url)
        self.runtime.platform = reader
        self.runtime.model.stock = 123456  # Would change a direct model view.
        data = self.request("/api/state")[1]
        self.assertEqual(data["source"], "Eclipse Ditto")
        self.assertEqual(data["audit"]["physical_stock"], 3000)

    def test_platform_age_tracks_telemetry_not_http_polling(self):
        reader = DittoReader("http://ditto.test", "test-only")
        twins = [{"thingId": "org.stepnoe:" + name, "features": features}
                 for name, features in self.runtime.model.telemetry().items()]
        raw = json.dumps(twins).encode()
        with patch("service.urlopen", return_value=io.BytesIO(raw)):
            reader.refresh()
        reader.last_change -= 60
        with patch("service.urlopen", return_value=io.BytesIO(raw)):
            reader.refresh()
        self.assertGreater(reader.snapshot()["telemetry_age"], 59)
        self.assertEqual(len(reader.snapshot()["history"]), 1)

    def test_http_maintenance_mqtt_journal_and_checkpoint(self):
        for thing in ("Processing_Plant", "Truck_01", "Truck_02"):
            code, reply = self.request("/stepnoe/api/maintenance", {"thing": thing, "value": 1})
            self.assertEqual(code, 202)
            self.assertEqual(reply["topic"], MAINTENANCE_TOPIC + thing)
        self.runtime.advance(600)
        state = self.request("/stepnoe/api/maintenance")[1]
        self.assertTrue(all(state["objects"].values()))
        self.assertEqual(len(state["events"]), 3)
        self.assertEqual(self.store.events(), [])  # Preserve the fraud journal contract.
        restored = self.store.load("fixed")
        self.assertTrue(restored.plant_maintenance)
        self.assertTrue(all(t.maintenance for t in restored.trucks))
        for thing in state["objects"]:
            self.request("/stepnoe/api/maintenance", {"thing": thing, "value": 0})
        self.runtime.advance(30)
        self.assertFalse(any(self.request("/stepnoe/api/maintenance")[1]["objects"].values()))

    def test_maintenance_commands_validate_target_payload_and_readiness(self):
        for data in ({"thing": "Wagon_01", "value": 1}, {"thing": "Truck_01", "value": -1}, {"thing": "Truck_01", "value": True}, {"value": 1}):
            with self.assertRaises(HTTPError) as err:
                self.request("/api/maintenance", data)
            self.assertEqual(err.exception.code, 400)
            err.exception.close()
        for topic, payload in ((MAINTENANCE_TOPIC + "Truck_01", b"-1"), (MAINTENANCE_TOPIC + "Wagon_01", b"1"), (FRAUD_TOPIC, b"1\n")):
            with self.assertRaises(ValueError):
                self.runtime.queue_control(topic, payload)
        self.runtime.connected = False
        with self.assertRaises(HTTPError) as err:
            self.request("/api/maintenance", {"thing": "Truck_01", "value": 1})
        self.assertEqual(err.exception.code, 503)
        err.exception.close()

    def test_ditto_reader_reports_zero_offset_active_drift_and_direction(self):
        self.runtime.model.fraud_mode = "drift"
        self.runtime.model.control(b"-1")
        reader = DittoReader("http://ditto.test", "test-only")
        twins = [{"thingId": "org.stepnoe:" + name, "features": features} for name, features in self.runtime.model.telemetry().items()]
        with patch("service.urlopen", return_value=io.BytesIO(json.dumps(twins).encode())):
            reader.refresh()
        state = reader.snapshot()
        self.assertTrue(state["fraud"])
        self.assertEqual(state["fraud_direction"], -1)
        self.assertEqual(state["fraud_mode"], "drift")
        self.assertEqual(state["audit"]["fraud_offset"], 0)
        self.assertEqual(state["audit"]["anomaly"], 0)

    def test_drift_rate_is_restored_and_http_control_keeps_its_contract(self):
        self.runtime.model.fraud_mode = "drift"
        self.runtime.model.drift_rate_tph = 10
        self.request("/stepnoe/api/fraud", {"value": -1})
        self.runtime.advance(3600)
        self.assertAlmostEqual(self.runtime.model.audit()["fraud_offset"], -10)
        self.assertEqual(self.store.load("drift").dump(), self.runtime.model.dump())
        self.request("/stepnoe/api/fraud", {"value": 0})
        self.runtime.advance(30)
        self.assertEqual(self.runtime.model.audit()["fraud_offset"], 0)


if __name__ == "__main__":
    unittest.main()
