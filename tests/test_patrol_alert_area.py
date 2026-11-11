"""The 100 km warning polygon is separate from the task patrol polygon."""

import json
import math
from pathlib import Path
import unittest

from rules.patrol_alert_area import (_center, _project, buffer_patrol_points,
                                     patrol_alert_area)
from runtime.contracts import SkillCall
from runtime.skills.compiler import PlanCompiler
from runtime.skills.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]
SQUARE = [{"lat": 22.0, "lon": 120.0},
          {"lat": 22.0, "lon": 120.3},
          {"lat": 22.2, "lon": 120.3},
          {"lat": 22.2, "lon": 120.0}]


def _inside(point, polygon):
    lat0, lon0 = _center(SQUARE)
    x, y = _project(point, lat0, lon0)
    vertices = [_project(p, lat0, lon0) for p in polygon]
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if (a[1] > y) != (b[1] > y):
            crossing = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < crossing:
                inside = not inside
    return inside


class PatrolAlertAreaTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.compiler = PlanCompiler(self.registry, ROOT / "scripts/powershell", ROOT / "runs")
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        source = json.loads((ROOT / "examples/plan.multiple.example.json")
                            .read_text(encoding="utf-8"))
        self.cap = source["skill_calls"][0]
        self.cap["area"] = {"name": "测试巡逻区", "points": SQUARE}

    def test_buffer_covers_points_just_under_100_km_from_every_side(self):
        warning = buffer_patrol_points(SQUARE)
        self.assertGreaterEqual(len(warning), 8)
        self.assertLessEqual(len(warning), 50)
        for point in SQUARE:
            self.assertTrue(_inside(point, warning))
        lat0, lon0 = _center(SQUARE)
        # Test 99 km outward from each midpoint in the same local projection.
        xy = [_project(p, lat0, lon0) for p in SQUARE]
        boundary = [
            ((xy[0][0]+xy[1][0])/2, min(p[1] for p in xy)-99),
            (max(p[0] for p in xy)+99, (xy[1][1]+xy[2][1])/2),
            ((xy[2][0]+xy[3][0])/2, max(p[1] for p in xy)+99),
            (min(p[0] for p in xy)-99, (xy[3][1]+xy[0][1])/2),
        ]
        vertices = [_project(p, lat0, lon0) for p in warning]
        def inside_xy(x, y):
            hits = False
            for a, b in zip(vertices, vertices[1:]+vertices[:1]):
                if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                    hits = not hits
            return hits
        self.assertTrue(all(inside_xy(*point) for point in boundary))
        self.assertEqual(SQUARE[0], {"lat": 22.0, "lon": 120.0})

    def test_concave_input_is_conservatively_enclosed(self):
        polygon = SQUARE[:3] + [{"lat": 22.1, "lon": 120.15}, SQUARE[3]]
        warning = buffer_patrol_points(polygon)
        self.assertTrue(all(_inside(p, warning) for p in polygon))

    def test_invalid_geometry_rejected(self):
        with self.assertRaisesRegex(ValueError, "至少需要三个"):
            buffer_patrol_points(SQUARE[:2])
        with self.assertRaisesRegex(ValueError, "几乎共线"):
            buffer_patrol_points([{"lat": 22.0, "lon": 120.0},
                                  {"lat": 22.1, "lon": 120.0},
                                  {"lat": 22.2, "lon": 120.0}])

    def test_compile_separate_type_106_area_for_patrol_only(self):
        cap = SkillCall.from_dict(self.cap)
        task = self.compiler.compile([cap], self.situation, "alert-test")[0].config["tasks"]["airCombatPatrol"]
        self.assertEqual(task["area"]["typeId"], "405")
        self.assertEqual(task["area"]["points"], [{"lon": p["lon"], "lat": p["lat"]} for p in SQUARE])
        self.assertEqual(task["alertArea"]["typeId"], "106")
        self.assertEqual(task["alertArea"]["name"], "测试巡逻区-警戒区")
        self.assertEqual(task["alertArea"]["existingAreaGuid"], "")
        self.assertTrue(_inside(SQUARE[0], task["alertArea"]["points"]))
        awacs = SkillCall.from_dict({
            "call_id": "awacs-alert-exclusion", "skill_id": "air.early_warning",
            "mission_name": "预警", "unit_guids": ["aaaaaaaa-0000-0000-0000-000000000004"],
            "area": {"points": SQUARE}})
        support = self.compiler.compile([awacs], self.situation, "support-alert-test")[0].config["tasks"]["airEarlyWarning"]
        self.assertIsNone(support["alertArea"])

    def test_existing_area_guid_resolves_live_polygon(self):
        guid = "11111111-2222-3333-4444-555555555555"
        self.cap["area"] = {"existing_area_guid": guid, "name": "复用巡逻区"}
        self.situation["existing_areas"] = [{"guid": guid, "points": SQUARE}]
        task = self.compiler.compile([SkillCall.from_dict(self.cap)], self.situation,
                                     "reused-alert-test")[0].config["tasks"]["airCombatPatrol"]
        self.assertEqual(task["area"]["existingAreaGuid"], guid)
        self.assertGreater(len(task["alertArea"]["points"]), 8)
        self.situation["existing_areas"] = []
        with self.assertRaisesRegex(ValueError, "缺少可回读的几何点"):
            self.compiler.compile([SkillCall.from_dict(self.cap)], self.situation,
                                  "unresolved-alert-test")
