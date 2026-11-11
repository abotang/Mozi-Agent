"""Current-contact distance rule and strike-phase review tests."""

import unittest

from rules.air_strike_review import review_focus, strike_opportunities


FIGHTER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OCCUPIED = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
AWACS = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
JAMMER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
AIR_CONTACT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
SEA_CONTACT = "ffffffff-ffff-4fff-8fff-ffffffffffff"
LAND_CONTACT = "11111111-1111-4111-8111-111111111111"
SKILLS = [{"skill_id": key} for key in
          ("air.air_strike", "air.sea_strike", "air.land_strike")]


def situation():
    return {
        "mission_assignments": {OCCUPIED: "22222222-2222-4222-8222-222222222222"},
        "contacts": {
            "air": [{"guid": AIR_CONTACT, "lat": 0.0, "lon": 4.0}],
            "sea": [{"guid": SEA_CONTACT, "lat": 0.0, "lon": 8.0}],
            "land": [{"guid": LAND_CONTACT, "lat": 0.0, "lon": 9.0}],
        },
        "situation_memory": {"historical_contact": LAND_CONTACT},
    }


def aircraft():
    return [
        {"guid": FIGHTER, "name": "F-16A", "unit_type": "Aircraft",
         "lat": 0.0, "lon": 0.0},
        {"guid": OCCUPIED, "name": "另一架战斗机", "unit_type": "Aircraft",
         "lat": 0.0, "lon": 3.99},
        {"guid": AWACS, "name": "E-2D 预警机", "unit_type": "Aircraft",
         "lat": 0.0, "lon": 3.99},
        {"guid": JAMMER, "name": "EC-130H 电子干扰机", "unit_type": "Aircraft",
         "lat": 0.0, "lon": 3.99},
    ]


class AirStrikeReviewTests(unittest.TestCase):
    def test_current_contacts_within_1000_km_and_free_fighter_only(self):
        data = strike_opportunities(situation(), SKILLS, aircraft())
        self.assertEqual(data["radius_km"], 1000.0)
        eligible = data["eligible_by_skill"]
        self.assertEqual(set(eligible), {"air.air_strike", "air.sea_strike",
                                         "air.land_strike"})
        self.assertEqual([x["contact_guid"] for x in eligible["air.air_strike"]],
                         [AIR_CONTACT])
        self.assertGreater(eligible["air.air_strike"][0]["nearest_distance_km"], 440)
        self.assertEqual([x["contact_guid"] for x in eligible["air.sea_strike"]],
                         [SEA_CONTACT])
        self.assertEqual(eligible["air.land_strike"], [])  # 9° > 1000 km

    def test_occupancy_and_special_aircraft_do_not_create_opportunity(self):
        data = situation()
        data["mission_assignments"][FIGHTER] = "33333333-3333-4333-8333-333333333333"
        opportunities = strike_opportunities(data, SKILLS, aircraft())
        self.assertTrue(all(not rows for rows in
                            opportunities["eligible_by_skill"].values()))

    def test_invalid_or_historical_contacts_and_unregistered_skills_do_not_count(self):
        data = situation()
        data["contacts"]["air"] = [
            {"guid": AIR_CONTACT, "lat": float("nan"), "lon": 0.1},
            {"guid": AIR_CONTACT, "lat": 0.0, "lon": 0.1},
        ]
        data["contacts"]["sea"] = []
        data["contacts"]["land"] = []
        result = strike_opportunities(data, [{"skill_id": "air.sea_strike"}], aircraft())
        self.assertTrue(all(not rows for rows in result["eligible_by_skill"].values()))
        data["contacts"] = {"air": [], "sea": [], "land": []}
        self.assertTrue(all(not rows for rows in
                            strike_opportunities(data, SKILLS, aircraft())
                            ["eligible_by_skill"].values()))

    def test_duplicate_current_contact_uses_later_valid_position(self):
        data = situation()
        data["contacts"]["air"].insert(0, {
            "guid": AIR_CONTACT, "lat": None, "lon": None})
        result = strike_opportunities(data, SKILLS, aircraft())
        self.assertEqual(
            result["eligible_by_skill"]["air.air_strike"][0]["contact_guid"],
            AIR_CONTACT)

    def test_phase_review_prioritizes_air_then_sea_and_never_writes_task(self):
        opportunities = strike_opportunities(situation(), SKILLS, aircraft())
        proposal = {"skill_calls": [{"skill_id": "air.combat_patrol"}]}
        self.assertIsNone(review_focus(opportunities, proposal, "侦察部署"))
        self.assertEqual(review_focus(opportunities, proposal, "打击优先"),
                         "air.air_strike")
        proposal["skill_calls"].append({"skill_id": "air.air_strike"})
        self.assertIsNone(review_focus(opportunities, proposal, "抓紧打击"))
        opportunities["eligible_by_skill"]["air.air_strike"] = []
        self.assertEqual(review_focus(opportunities, proposal, "临近结束"),
                         "air.sea_strike")


if __name__ == "__main__":
    unittest.main()
