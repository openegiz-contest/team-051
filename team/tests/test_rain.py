import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model, OK
from sensors import reconcile


class RainScenarioTests(unittest.TestCase):
    def stationary(self):
        m = Model(false_alarm_probability=0)
        m.rail_state, m.rail_elapsed = 'returning', -1e6
        for name in ('Truck_01', 'Truck_02'):
            m.set_maintenance(name, True)
        return m

    def test_rain_adds_water_not_dry_mass_or_iron_and_erp_uses_old_factor(self):
        m = self.stationary()
        before = m.stock, m.mined, m.feed, m.shipped
        m.set_scenario('rain', True)
        self.assertEqual(m.wet_stock, 3210)
        self.assertEqual((m.stock, m.mined, m.feed, m.shipped), before)
        m.step(30)
        a = m.audit()
        self.assertAlmostEqual(a['wet_scale_reading'] / 1.03, a['book_stock'], delta=.1)
        self.assertAlmostEqual(a['moisture_erp_offset'], 3000 * (1.07 / 1.03 - 1), delta=.01)
        self.assertEqual(a['audit_reason'], 'MOISTURE_ERP_MISMATCH')
        self.assertEqual(a['recommendation'], 'Требуется пересчет влажности / ошибка ERP')
        self.assertEqual(a['anomaly'], 1)
        self.assertEqual(m.telemetry()['ERP_Register']['registered_moisture_pct']['properties']['value'], 3)
        self.assertEqual(m.telemetry()['Moisture_Sensor_01']['moisture_pct']['properties']['value'], 7)
        self.assertAlmostEqual(a['physical_residual'], 0, places=6)
        self.assertAlmostEqual(a['fe_residual'], 0, places=6)

    def test_audit_infers_moisture_error_from_readings_without_weather_flag(self):
        result = reconcile({'wet_value': 3210, 'moisture_pct': 7, 'time': 10},
                           {'value': 3000 / 2.7, 'time': 10}, 3210/1.03, 3210/1.03, .5,
                           erp_moisture_pct=3)
        self.assertEqual(result['audit_reason'], 'MOISTURE_ERP_MISMATCH')
        self.assertEqual(result['measured_stock'], 3000)

    def test_rain_reset_duplicate_commands_checkpoint_and_plant_erp_regression(self):
        m = self.stationary()
        m.set_scenario('rain', True)
        m.step(40)
        volume = m.audit()['lidar_volume_reading']
        m.set_scenario('rain', True)
        self.assertEqual(m.audit()['lidar_volume_reading'], volume)
        restored = Model.restore(json.loads(json.dumps(m.dump())))
        m.step(60);restored.step(60)
        self.assertEqual(m.dump(), restored.dump())
        m.set_scenario('rain', False)
        m.step(30)
        self.assertEqual(m.wet_stock, 3090)
        self.assertEqual(m.audit()['status'], OK)
        m.set_scenario('plant_erp', True)
        m.step(3600)
        self.assertAlmostEqual(m.erp_phantom_stock, 400.8)
        self.assertEqual(m.audit()['audit_reason'], 'ERP_DURING_DOWNTIME')
