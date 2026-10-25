import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model


class InterlockTests(unittest.TestCase):
    def conserved(self, m):
        for key in ('physical_residual', 'stock_residual', 'fe_residual'):
            self.assertAlmostEqual(m.audit()[key], 0, places=6)
        self.assertLessEqual(m.stock, m.max_stock_capacity + 1e-7)
        self.assertLessEqual(m.buffer, m.max_buffer_capacity + 1e-7)

    def test_full_buffer_retains_truck_loads_and_stops_excavator_then_recovers(self):
        m = Model()
        m.set_maintenance('Processing_Plant', True)
        m.step(3600)
        self.assertEqual(m.buffer, 360)
        self.assertEqual(len(m.truck_queue), 2)
        self.assertTrue(m.excavator_blocked)
        self.assertTrue(all(t.weight == 90 and t.state == 'unloading' for t in m.trucks))
        before = m.mined
        m.step(600)
        self.assertEqual(m.mined, before)
        self.conserved(m)
        m.set_maintenance('Processing_Plant', False)
        m.step(1800)
        self.assertGreater(m.feed, 0)
        self.assertLess(len(m.truck_queue), 2)
        self.conserved(m)

    def test_full_stock_limits_processing_without_discarding_material(self):
        m = Model(max_stock_capacity=3000)
        m.rail_state = 'returning'
        m.rail_elapsed = -1e6  # Isolate stock interlock from scheduled rail withdrawals.
        m.step(3600)
        self.assertEqual(m.stock, 3000)
        self.assertEqual(m.concentrate, 0)
        self.assertEqual(m.buffer, 360)
        self.assertEqual(len(m.truck_queue), 2)
        self.conserved(m)

    def test_legacy_overcapacity_checkpoint_is_migrated_without_clamping(self):
        data = Model().dump()
        data['stock'] = 18000
        data['initial_stock'] = 18000
        data['buffer'] = 500
        for key in ('max_stock_capacity', 'max_buffer_capacity'):
            data.pop(key)
        m = Model.restore(json.loads(json.dumps(data)))
        self.assertEqual((m.stock, m.buffer), (18000, 500))
        self.assertEqual((m.max_stock_capacity, m.max_buffer_capacity), (18000, 500))

    def test_fractional_capacity_keeps_partly_unloaded_mass_in_the_truck(self):
        m = Model(max_buffer_capacity=123.45)
        m.set_maintenance('Processing_Plant', True)
        m.step(3600)
        self.assertAlmostEqual(m.buffer, 123.45)
        self.assertEqual(len(m.truck_queue), 2)
        self.assertTrue(any(0 < t.weight < 90 for t in m.trucks))
        self.conserved(m)
