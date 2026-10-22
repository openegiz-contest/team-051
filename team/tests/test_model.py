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

    def test_geometry_mass_conversion(self):
        m = Model()
        m.step(3000)
        a = m.audit()
        self.assertAlmostEqual(math.pi * 25 * 15 * a["height"] / 3, a["scan_volume"])
        self.assertAlmostEqual(a["scan_volume"] * 2.7, m.stock)

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


if __name__ == "__main__":
    unittest.main()
