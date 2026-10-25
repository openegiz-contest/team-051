import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model

ROOT = Path(__file__).resolve().parents[2]


class InstrumentTwinTests(unittest.TestCase):
    def test_instruments_are_things_with_state_calibration_delay_and_parents(self):
        twins = json.loads((ROOT/'team/twins.json').read_text(encoding='utf-8'))
        for name, parent in [('Weighbridge_01', 'Processing_Plant'), ('LiDAR_01', 'Stockpile_01'),
                             ('ERP_Register', 'Processing_Plant'), ('Moisture_Sensor_01', 'Stockpile_01')]:
            thing = twins['org.stepnoe:' + name]
            self.assertEqual(thing['attributes']['_parents'], 'org.stepnoe:' + parent)
            self.assertTrue(thing['attributes']['independent_instrument'])
            for key in ('status', 'sample_timestamp', 'delay', 'calibration_factor'):
                self.assertIn(key, thing['features'])
                self.assertIn(key, thing['attributes']['feature_contracts'])

    def test_scale_calibration_fault_is_visible_on_its_twin(self):
        m = Model()
        self.assertEqual(m.telemetry()['Weighbridge_01']['status']['properties']['value'], 'OK')
        m.set_scenario('sensor_bias', True)
        scale = m.telemetry()['Weighbridge_01']
        self.assertEqual(scale['status']['properties']['value'], 'FAULT')
        self.assertEqual(scale['calibration_factor']['properties']['value'], 1.02)

    def test_ditto_source_subscription_uses_qos_one(self):
        source = json.loads((ROOT/'deploy/compose/init/mosquitto-source-connection.json').read_text())
        self.assertEqual(source['sources'][0]['qos'], 1)
        self.assertIn('telemetry/+', source['sources'][0]['addresses'])
