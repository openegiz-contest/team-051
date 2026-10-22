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
from model import FRAUD_TOPIC
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
        if topic == FRAUD_TOPIC:
            self.runtime.controls.put((str(payload).encode(), "mqtt-test"))
        return Ack()


class ServiceTests(unittest.TestCase):
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

    def test_separate_commands_in_same_tick_are_journaled(self):
        self.runtime.controls.put((b"1", "mqtt"))
        self.runtime.controls.put((b"0", "mqtt"))
        self.runtime.advance(30)
        events = self.store.events()
        self.assertEqual([e["book"] - e["physical"] for e in events], [0, 300])

    def test_http_input_validation_and_cross_origin(self):
        for data in ({"value": True}, {"value": "1"}, {"value": 2}, {}, []):
            with self.assertRaises(HTTPError) as err:
                self.request("/api/fraud", data)
            self.assertEqual(err.exception.code, 400)
        with self.assertRaises(HTTPError) as err:
            self.request("/api/fraud", {"value": 1}, {"Content-Type": "application/json", "Origin": "https://other.invalid"})
        self.assertEqual(err.exception.code, 403)

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


if __name__ == "__main__":
    unittest.main()
