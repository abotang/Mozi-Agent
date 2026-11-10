from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.mozi.live_situation import collect_live_situation


class LiveSituationRoleTests(unittest.TestCase):
    def test_ship_and_submarine_are_separate(self):
        units = [
            {"id": "air-1", "name": "飞机", "UnitType": "Aircraft"},
            {"id": "ship-1", "name": "水面舰", "UnitType": "Ship"},
            {"id": "sub-1", "name": "潜艇", "UnitType": "Submarine"},
            {"id": "ground-1", "name": "陆上设施", "UnitType": "Facility"},
            {"id": "missile-1", "name": "导弹", "UnitType": "Missile"},
        ]
        state = {"side": [{"id": "blue", "name": "蓝方", "activeUnits": units,
                           "contacts": []}], "FightTree": []}
        with patch("runtime.mozi.live_situation._frame", return_value=state), \
                patch("runtime.mozi.live_situation._times", return_value=("start", "end", "initial")), \
                patch("runtime.mozi.live_situation.read_blue_mission_assignments", return_value={}):
            situation = collect_live_situation()
        self.assertEqual([row["guid"] for row in situation["units"]["air"]], ["air-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["navy"]], ["ship-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["submarine"]], ["sub-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["army"]], ["ground-1"])
        self.assertEqual(situation["mission_assignments"], {})


if __name__ == "__main__":
    unittest.main()
