import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model, OK, CRITICAL


class CascadeScenarioTests(unittest.TestCase):
    def test_plant_erp_complete_cascade_and_non_destructive_reset(self):
        m = Model()
        m.set_scenario('plant_erp', True)
        m.step(3600)
        a = m.audit()
        self.assertAlmostEqual(m.erp_phantom_stock, 400.8)
        self.assertEqual(m.feed, 0)
        self.assertEqual(m.buffer, m.max_buffer_capacity)
        self.assertEqual(len(m.truck_queue), 2)
        self.assertTrue(m.excavator_blocked)
        self.assertEqual(a['audit_reason'], 'ERP_DURING_DOWNTIME')
        self.assertEqual(a['status'], CRITICAL)
        before = m.stock, m.buffer, m.mined
        m.set_scenario('plant_erp', False)
        self.assertEqual((m.stock, m.buffer, m.mined), before)
        self.assertEqual(m.erp_phantom_stock, 0)
        m.step(600)
        self.assertGreater(m.feed, 0)
        self.assertEqual(m.audit()['status'], OK)
        for key in ('physical_residual', 'fe_residual'):
            self.assertAlmostEqual(m.audit()[key], 0, places=6)

    def test_bias_only_changes_instrument_reading_and_diagnoses_channel(self):
        normal, biased = Model(), Model()
        biased.set_scenario('sensor_bias', True)
        normal.step(1200)
        biased.step(1200)
        for key in ('stock', 'mined', 'buffer', 'feed', 'concentrate', 'shipped'):
            self.assertEqual(getattr(normal, key), getattr(biased, key))
        self.assertEqual(normal.audit()['lidar_dry_stock'], biased.audit()['lidar_dry_stock'])
        self.assertEqual(biased.audit()['audit_reason'], 'SENSOR_MISMATCH')
        self.assertEqual(biased.audit()['recommendation'], 'Проверить измерительный канал')
        biased.set_scenario('sensor_bias', False)
        biased.step(30)
        self.assertEqual(biased.audit()['status'], OK)

    def test_duplicate_commands_checkpoint_and_manual_maintenance_are_independent(self):
        m = Model()
        m.set_maintenance('Processing_Plant', True)
        m.set_scenario('plant_erp', True)
        m.step(900)
        amount = m.erp_phantom_stock
        m.set_scenario('plant_erp', True)
        self.assertEqual(m.erp_phantom_stock, amount)
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        m.step(60)
        restored.step(60)
        self.assertEqual(restored.dump(), m.dump())
        m.set_scenario('plant_erp', False)
        self.assertTrue(m.plant_stopped)
        with self.assertRaises(ValueError):
            m.set_scenario('unknown', True)
