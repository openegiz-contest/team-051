import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model
from sensors import reconcile


class IndependentSensorTests(unittest.TestCase):
    def test_decision_has_only_measurements_and_accounting_inputs(self):
        result = reconcile({'value': 3000, 'time': 20}, {'value': 3000 / 2.7, 'time': 20}, 3300, 3300, .5)
        self.assertEqual(result['anomaly'], 1)
        self.assertEqual(result['audit_reason'], 'ACCOUNTING_MISMATCH')
        result = reconcile({'value': 3060, 'time': 20}, {'value': 3000 / 2.7, 'time': 20}, 3000, 3000, .5)
        self.assertEqual(result['audit_reason'], 'SENSOR_MISMATCH')
        self.assertEqual(result['recommendation'], 'Проверить измерительный канал')

    def test_readings_are_delayed_and_reads_do_not_resample(self):
        m = Model()
        m.step(900)
        a = m.audit()
        self.assertLess(a['scale_timestamp'], m.time)
        self.assertLess(a['lidar_timestamp'], m.time)
        self.assertEqual(a, m.audit())
        self.assertGreater(abs(a['weight_scale_reading'] - m.stock), .01)
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        m.step(600)
        restored.step(600)
        self.assertEqual(m.dump(), restored.dump())

    def test_calibration_can_generate_a_false_alarm_without_changing_mass(self):
        normal = Model(false_alarm_probability=0)
        outlier = Model(false_alarm_probability=1)
        normal.step(40)
        outlier.step(40)
        self.assertEqual(normal.ground_truth_mass, outlier.ground_truth_mass)
        self.assertEqual(normal.audit()['anomaly'], 0)
        self.assertEqual(outlier.audit()['anomaly'], 1)
        self.assertGreater(outlier.sensors['coincidence_events'], 0)

    def test_no_reading_uses_the_book_ledger_as_a_sensor_input(self):
        first, second = Model(), Model()
        second.control(b'1')
        first.step(1800)
        second.step(1800)
        self.assertEqual(first.sensors, second.sensors)

    def test_old_checkpoint_commissions_sensor_state(self):
        data = Model().dump()
        for key in ('sensors', 'erp_history', 'false_alarm_probability'):
            data.pop(key)
        m = Model.restore(data)
        self.assertEqual(m.audit()['weight_scale_reading'], 3000)
