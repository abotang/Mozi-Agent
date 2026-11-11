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
                patch("runtime.mozi.live_situation.read_blue_mission_assignments", return_value={}), \
                patch("runtime.mozi.live_situation.read_blue_patrol_areas", return_value=[]), \
                patch("runtime.mozi.live_situation.read_current_loadout",
                      return_value={"weapon_info_status": "unknown",
                                    "loadout_name": "", "strike_capabilities": []}):
            situation = collect_live_situation()
        self.assertEqual([row["guid"] for row in situation["units"]["air"]], ["air-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["navy"]], ["ship-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["submarine"]], ["sub-1"])
        self.assertEqual([row["guid"] for row in situation["units"]["army"]], ["ground-1"])
        self.assertEqual(situation["mission_assignments"], {})


    def test_active_air_sea_targets_only_include_current_sea_contacts(self):
        sea = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        air = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        lost = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        state = {"side": [{"id": "blue", "name": "蓝方", "activeUnits": [],
            "contacts": [{"id": "{" + sea.upper() + "}", "UnitType": "Ship", "SideColor": "#ff0000"},
                         {"id": air, "UnitType": "Aircraft", "SideColor": "#ff0000"}]}]}

        def assignments(base, side, aircraft, *, active_strike_target_guids):
            active_strike_target_guids.update((sea, air, lost))
            return {}

        with patch("runtime.mozi.live_situation._frame", return_value=state), \
                patch("runtime.mozi.live_situation._times", return_value=("start", "end", "initial")), \
                patch("runtime.mozi.live_situation.read_blue_mission_assignments", side_effect=assignments), \
                patch("runtime.mozi.live_situation.read_blue_patrol_areas", return_value=[]):
            result = collect_live_situation()
        self.assertEqual(result["active_air_sea_target_guids"], [sea])


if __name__ == "__main__":
    unittest.main()
