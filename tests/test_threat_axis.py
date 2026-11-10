"""A lone current enemy AEW contact supplies a suspected bearing, not new targets."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.threat_axis import suspected_air_attack_axis


class ThreatAxisTests(unittest.TestCase):
    def setUp(self):
        self.situation = {
            "units": {"navy": [{"guid": "ship-1", "lat": 20, "lon": 130}], "army": []},
            "contacts": {"air": [{"guid": "contact-1", "name": "卡-29RLD预警直升机",
                                  "lat": 20, "lon": 134.5}]},
        }

    def test_single_early_warning_contact_is_suspected_eastward_axis(self):
        axis = suspected_air_attack_axis(self.situation)
        self.assertEqual(axis["status"], "suspected")
        self.assertEqual(axis["contact_guid"], "contact-1")
        self.assertEqual(axis["reference_guid"], "ship-1")
        self.assertAlmostEqual(axis["bearing_deg"], 90, delta=2)
        self.assertFalse(axis["other_aircraft_confirmed"])

    def test_other_contacts_do_not_become_invented_targets(self):
        self.situation["contacts"]["air"].append({"guid": "contact-2", "name": "米格-29",
                                                   "lat": 21, "lon": 134})
        self.assertIsNone(suspected_air_attack_axis(self.situation))
        self.situation["contacts"]["air"] = [{"guid": "contact-2", "name": "米格-29",
                                               "lat": 21, "lon": 134}]
        self.assertIsNone(suspected_air_attack_axis(self.situation))


if __name__ == "__main__":
    unittest.main()
