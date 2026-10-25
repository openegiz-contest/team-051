import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import CRITICAL, OK, Model, in_corridor, PIT, PLANT


class MassBalanceTests(unittest.TestCase):
    def assert_conserved(self, m):
        a = m.audit()
        for name in ("physical_residual", "stock_residual", "fe_residual"):
            self.assertAlmostEqual(a[name], 0, places=6, msg=name)
        self.assertAlmostEqual(m.feed, m.concentrate + m.tails, places=6)
        self.assertGreaterEqual(m.stock, 0)
        self.assertGreaterEqual(m.buffer, -1e-8)

    def test_opening_stock_is_not_an_inflow_error(self):
        m = Model()
        self.assertEqual(m.audit()["status"], OK)
        self.assertEqual(m.audit()["physical_stock"], 3000)
        self.assert_conserved(m)

    def test_full_cycles_include_wagon_inventory_and_departures(self):
        m = Model()
        states = set()
        for _ in range(24 * 60):
            m.step(60)
            states.add(m.rail_state)
            self.assert_conserved(m)
        self.assertEqual(states, {"loading", "outbound", "returning"})
        self.assertGreater(m.shipped, 0)
        self.assertTrue(all(t.trips > 10 for t in m.trucks))
        self.assertGreater(m.tails_fe, 0)
        self.assertEqual(m.audit()["status"], OK)

    def test_fraud_changes_only_books_and_reset_preserves_inventory(self):
        m = Model()
        m.step(1000)
        original = m.dump()
        m.control(b"1")
        self.assertAlmostEqual(m.audit()["fraud_offset"], 300)
        self.assertEqual(m.audit()["status"], CRITICAL)
        self.assertAlmostEqual(m.audit()["book_residual"], -300)
        m.control(b"1")
        self.assertAlmostEqual(m.audit()["fraud_offset"], 300)  # QoS1 duplicates do not accumulate.
        self.assert_conserved(m)
        m.control(b"0")
        self.assertEqual(m.audit()["status"], OK)
        for key, value in original.items():
            if key != "revision":
                self.assertEqual(m.dump()[key], value, key)

    def test_percent_mode_is_exactly_ten_percent(self):
        m = Model(fraud_mode="percent")
        m.step(800)
        m.control(b"1")
        self.assertAlmostEqual(m.audit()["discrepancy_pct"], 10)
        self.assert_conserved(m)

    def test_concealment_is_signed_idempotent_and_preserves_physics(self):
        m = Model()
        m.step(1000)
        physical = m.audit()["physical_stock"]
        for command, expected in ((b"-1", -300), (b"-1", -300), (b"1", 300), (b"0", 0)):
            m.control(command)
            audit = m.audit()
            self.assertAlmostEqual(audit["fraud_offset"], expected)
            self.assertAlmostEqual(audit["book_stock"] - physical, expected)
            self.assertAlmostEqual(audit["physical_stock"], physical)
            self.assertEqual(audit["status"], CRITICAL if expected else OK)
            self.assert_conserved(m)
        m.fraud_mode = "percent"
        m.control(b"-1")
        self.assertAlmostEqual(m.audit()["discrepancy_pct"], -10)
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        self.assertEqual(restored.audit(), m.audit())

    def test_old_checkpoint_without_direction_restores(self):
        original = Model().dump()
        del original["fraud_direction"]
        self.assertEqual(Model.restore(original).fraud_direction, 1)

    def test_geometry_mass_conversion(self):
        m = Model()
        m.step(3000)
        a = m.audit()
        self.assertAlmostEqual(math.pi * 25 * 15 * a["height"] / 3, a["reference_volume"])
        self.assertAlmostEqual(a["reference_volume"] * 2.7, m.stock)
        self.assertAlmostEqual(a["scan_volume"], a["reference_volume"] * (1 + m.lidar_noise_fraction))

    def test_checkpoint_replay_is_deterministic(self):
        m = Model()
        m.step(1730)
        m.control(b"1")
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        for _ in range(30):
            m.step(120)
            restored.step(120)
        self.assertEqual(restored.dump(), m.dump())
        self.assertEqual(restored.telemetry(), m.telemetry())

    def test_bad_commands_and_steps(self):
        m = Model()
        for payload in (b"true", b"2", b"1\n", b"{}"):
            with self.assertRaises(ValueError):
                m.control(payload)
        for step in (0, -1, math.nan, math.inf, 3601):
            with self.assertRaises(ValueError):
                m.step(step)

    def test_actual_geofence_distance(self):
        self.assertTrue(in_corridor(*PIT, PIT, PLANT))
        self.assertTrue(in_corridor(*PLANT, PIT, PLANT))
        self.assertFalse(in_corridor(PIT[0] + .1, PIT[1], PIT, PLANT))

    def test_physical_imbalance_is_detected_without_fraud(self):
        m = Model()
        m.stock += 10
        self.assertFalse(m.fraud)
        self.assertEqual(m.audit()["status"], CRITICAL)

    def test_twins_are_namespaced_and_parent_first(self):
        root = Path(__file__).resolve().parents[1]
        twins = json.loads((root / "twins.json").read_text(encoding="utf-8"))
        self.assertEqual(len(twins), 14)
        seen = set()
        for name, body in twins.items():
            parent = body["attributes"].get("_parents")
            if parent:
                self.assertIn(parent, seen)
                self.assertEqual(body["attributes"]["parents"], [parent])
            seen.add(name)
            short = name.split(":")[1]
            self.assertEqual(set(body["features"]), set(Model().telemetry()[short]))

    def test_drift_integration_threshold_sign_duplicates_and_reset(self):
        m = Model(fraud_mode="drift")
        m.control(b"1")
        self.assertEqual(m.audit()["fraud_offset"], 0)
        self.assertEqual(m.audit()["fraud_active"], 1)
        m.step(600)
        self.assertAlmostEqual(m.audit()["fraud_offset"], 5 / 6)
        self.assertEqual(m.audit()["status"], OK)
        m.control(b"1")
        self.assertAlmostEqual(m.audit()["fraud_offset"], 5 / 6)
        m.step(200)
        self.assertEqual(m.audit()["status"], CRITICAL)
        m.control(b"-1")
        m.step(2800)
        self.assertAlmostEqual(m.audit()["fraud_offset"], -5)
        self.assert_conserved(m)
        m.control(b"0")
        m.step(30)
        self.assertEqual(m.audit()["fraud_offset"], 0)
        self.assertEqual(m.audit()["status"], OK)
        m.control(b"1")
        m.step(3600)
        self.assertAlmostEqual(m.audit()["fraud_offset"], 5)

    def test_lidar_is_bounded_nonzero_repeatable_and_never_makes_false_fraud(self):
        for opening in (1., 3000., 1e6):
            m = Model(stock=opening, initial_stock=opening)
            samples = []
            for _ in range(500):
                m.step(1)
                a = m.audit()
                samples.append(a["lidar_noise_pct"])
                self.assertLessEqual(abs(a["lidar_noise_pct"]), .5)
                self.assertLessEqual(abs(a["lidar_residual"]), a["lidar_uncertainty_t"] + 1e-7)
                self.assertEqual(a["status"], OK)
                self.assert_conserved(m)
                self.assertEqual(a, m.audit())  # Reads do not resample a sensor.
            self.assertLess(min(samples), 0)
            self.assertGreater(max(samples), 0)
            self.assertLess(abs(sum(samples) / len(samples)), .03)
        noiseless = Model(lidar_noise_pct=0)
        noiseless.step(100)
        self.assertEqual(noiseless.audit()["scan_volume"], noiseless.audit()["reference_volume"])

    def test_dry_basis_moisture_preserves_mass_and_iron(self):
        self.assertEqual(Model().moisture_pct, 3)
        dry, wet = Model(moisture_pct=0), Model(moisture_pct=12)
        for _ in range(30):
            dry.step(120)
            wet.step(120)
            self.assert_conserved(wet)
        for name in ("stock", "mined", "feed", "concentrate", "tails", "shipped", "mined_fe", "shipped_fe"):
            self.assertEqual(getattr(wet, name), getattr(dry, name))
        a = wet.audit()
        self.assertAlmostEqual(a["wet_stock"] / 1.12, a["dry_stock"])
        self.assertAlmostEqual(a["dry_stock"], wet.stock)
        telemetry = wet.telemetry()
        self.assertEqual(telemetry["Stockpile_01"]["moisture"]["properties"]["value"], 12)
        self.assertEqual(telemetry["Processing_Plant"]["moisture"]["properties"]["value"], 12)
        self.assertAlmostEqual(telemetry["Processing_Plant"]["wet_inflow"]["properties"]["value"], wet.feed * 1.12)

    def test_gross_lidar_failure_is_detected_outside_calibrated_uncertainty(self):
        m = Model()
        m.lidar_noise_fraction = .02  # Sensor fault, outside the Gaussian model.
        a = m.audit()
        self.assertAlmostEqual(a["lidar_residual"], 60)
        self.assertEqual(a["lidar_uncertainty_t"], 15)
        self.assertAlmostEqual(a["lidar_validated_residual"], 45)
        self.assertEqual(a["status"], CRITICAL)
        self.assert_conserved(m)

    def test_factory_maintenance_accumulates_buffer_then_resumes_without_losing_mass(self):
        m = Model()
        m.step(900)
        before = m.feed, m.concentrate, m.tails, m.buffer
        m.set_maintenance("Processing_Plant", True)
        m.step(900)
        self.assertEqual((m.feed, m.concentrate, m.tails), before[:3])
        self.assertGreater(m.buffer, before[3])
        self.assertEqual(m.raw_inflow_rate, 0)
        self.assertEqual(m.telemetry()["Processing_Plant"]["state"]["properties"]["value"], "maintenance")
        self.assertEqual(m.telemetry()["Processing_Plant"]["raw_inflow"]["properties"]["value"], before[0])
        self.assert_conserved(m)
        m.set_maintenance("Processing_Plant", False)
        m.step(60)
        self.assertGreater(m.feed, before[0])
        self.assertGreater(m.raw_inflow_rate, 0)
        self.assert_conserved(m)

    def test_truck_maintenance_freezes_and_resumes_every_inflight_state(self):
        for state in ("queued", "loading", "hauling", "unloading", "returning"):
            m = Model()
            truck = m.trucks[1 if state == "queued" else 0]
            while truck.state != state:
                m.step(1)
                self.assertLess(m.time, 2000)
            m.set_maintenance(truck.name, True)
            before = dict(vars(truck))
            point = m.telemetry()[truck.name]
            m.step(180)
            after = m.telemetry()[truck.name]
            self.assertEqual(vars(truck), before)
            self.assertEqual(after["state"]["properties"]["value"], "MAINTENANCE")
            self.assertEqual(after["speed"]["properties"]["value"], 0)
            self.assertEqual(after["lat"], point["lat"])
            self.assertEqual(after["lon"], point["lon"])
            self.assert_conserved(m)
            m.set_maintenance(truck.name, False)
            m.step(1)
            self.assertEqual(truck.elapsed, before["elapsed"] + 1)
            self.assert_conserved(m)

    def test_new_factors_checkpoint_and_old_checkpoint_defaults(self):
        m = Model(fraud_mode="drift", moisture_pct=7)
        m.control(b"-1")
        m.step(1100)
        m.set_maintenance("Truck_01", True)
        m.set_maintenance("Processing_Plant", True)
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        for _ in range(10):
            m.step(90)
            restored.step(90)
        self.assertEqual(restored.dump(), m.dump())
        self.assertEqual(restored.telemetry(), m.telemetry())
        old = Model().dump()
        for name in ("moisture_pct", "lidar_noise_pct", "lidar_rng_state", "lidar_noise_fraction", "drift_rate_tph", "drift_tonnes", "drift_compensation", "plant_maintenance", "raw_inflow_rate"):
            old.pop(name)
        for truck in old["trucks"]:
            truck.pop("maintenance")
        restored = Model.restore(old)
        self.assertEqual(restored.moisture_pct, 3)
        self.assertFalse(restored.plant_maintenance)
        self.assertEqual(restored.drift_tonnes, 0)

    def test_new_factor_validation(self):
        for kwargs in ({"fraud_mode": "unknown"}, {"moisture_pct": -1}, {"moisture_pct": math.nan}, {"drift_rate_tph": -1}, {"lidar_noise_pct": .6}):
            with self.assertRaises(ValueError):
                Model(**kwargs)
        for target, enabled in (("Wagon_01", True), ("Truck_01", 1)):
            with self.assertRaises(ValueError):
                Model().set_maintenance(target, enabled)


if __name__ == "__main__":
    unittest.main()
