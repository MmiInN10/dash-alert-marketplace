import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import arrivals  # noqa: E402
import route  # noqa: E402

# ODsay 가이드(trafficType 1 지하철 / 2 버스 / 3 도보, lane.busNo / lane.name)의 필드만 흉내 낸 입력
PATH = {
    "info": {"totalTime": 40},
    "subPath": [
        {"trafficType": 3, "sectionTime": 4},
        {"trafficType": 2, "sectionTime": 15, "startName": "A정류장", "endName": "B정류장", "lane": [{"busNo": "146"}]},
        {"trafficType": 3, "sectionTime": 0},
        {"trafficType": 1, "sectionTime": 18, "startName": "B역", "endName": "C역", "lane": [{"name": "수도권 2호선"}]},
        {"trafficType": 3, "sectionTime": 3},
    ],
}
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=arrivals.KST)


class SummarizeTest(unittest.TestCase):
    def test_steps(self):
        s = route.summarize(PATH)
        self.assertEqual(s["total_min"], 40)
        self.assertEqual([x["kind"] for x in s["steps"]], ["walk", "bus", "subway", "walk"])  # 0분 도보는 제외
        self.assertEqual(s["steps"][1]["name"], "146번 버스")
        self.assertEqual(route.first_walk_min(s["steps"]), 4)


class PlanMessageTest(unittest.TestCase):
    def plan(self, arrive_min_from_now):
        steps = route.summarize(PATH)["steps"]
        return route.plan_message("강남역", NOW + timedelta(minutes=arrive_min_from_now), NOW, 40, steps, 5)

    def test_wait(self):
        r = self.plan(90)  # 출발 시각 = 90-45 = 45분 뒤
        self.assertEqual(r["verdict"], "wait")
        self.assertEqual(r["depart_at"], "08:45")

    def test_soon_boundary(self):
        self.assertEqual(self.plan(48)["verdict"], "soon")  # 3분 뒤
        self.assertEqual(self.plan(49)["verdict"], "wait")  # 4분 뒤

    def test_run_when_late(self):
        r = self.plan(45)  # 출발 시각 = 지금
        self.assertEqual(r["verdict"], "run")
        self.assertIn("[뛰어라] 지금 바로 출발하기!!", r["message"])
        self.assertIn("승차지점까지 4분", r["message"])

    def test_already_late_uses_now_not_past_departure(self):
        # 목표 30분 뒤인데 총 40분(+여유 5분) 걸리면 지금 출발해도 10분 늦는다. 도착·탑승 시각은 지금 기준이어야 한다
        r = self.plan(30)
        self.assertEqual(r["verdict"], "run")
        self.assertEqual(r["depart_at"], "08:00")
        self.assertIn("[08:40 도착] ⚠10분 늦음", r["message"])
        self.assertIn("🚌146번 ~08:04", r["message"])

    def test_on_time_has_no_late_warning(self):
        self.assertNotIn("늦음", self.plan(90)["message"])
        self.assertNotIn("늦음", self.plan(45)["message"])  # 출발 시각이 정확히 지금

    def test_wait_message_shape(self):
        m = self.plan(90)["message"]
        self.assertIn("[뛰어라] 45분 뒤(08:45) 출발하기", m)
        # 출발 08:45 + 도보 4분 = 08:49 버스 승차, + 15분 = 09:04 지하철 승차. 도보 구간과 '수도권 ' 접두어는 생략
        self.assertIn("🚌146번 ~08:49 → 🚇2호선 ~09:04", m)  # ~ 는 예상 시각
        self.assertIn("📍 강남역 [09:25 도착]", m)

    def test_go_now_without_arrive(self):
        steps = route.summarize(PATH)["steps"]
        r = route.plan_message("회사", None, NOW, 40, steps)
        self.assertEqual(r["verdict"], "go")
        self.assertIn("[뛰어라] 지금 출발하기", r["message"])
        self.assertIn("📍 회사 [08:40 도착]", r["message"])
        self.assertIn("🚌146번 ~08:04 → 🚇2호선 ~08:19", r["message"])

    def test_walk_only_route(self):
        r = route.plan_message("회사", None, NOW, 7, [{"kind": "walk", "minutes": 7}])
        self.assertIn("도보 7분", r["message"])


class RealtimeBusTest(unittest.TestCase):
    """첫 버스 실시간 도착(초)으로 판정. 도보 4분(240초), 여유 1분(60초) 기준."""

    def plan(self, arrivals, steps=None):
        steps = steps or route.summarize(PATH)["steps"]
        return route.plan_message("회사", None, NOW, 40, steps, 5, arrivals, 1)

    def test_run_when_just_enough_time(self):
        r = self.plan([300])  # 여유 60초 = 경계
        self.assertEqual(r["verdict"], "run")
        self.assertIn("[뛰어라] 지금 바로 출발하기!!", r["message"])
        self.assertIn("🚌146번 08:05 →", r["message"])  # 실시간 값이라 ~ 없음

    def test_wait_shows_minutes_and_shifts_later_legs(self):
        r = self.plan([420])  # 여유 180초 = 3분
        self.assertEqual(r["verdict"], "wait")
        self.assertIn("[뛰어라] 3분 뒤 출발하기", r["message"])
        self.assertIn("🚌146번 08:07 → 🚇2호선 ~08:22", r["message"])  # 지하철은 3분 밀린 예상
        self.assertIn("[08:43 도착]", r["message"])

    def test_skips_uncatchable_first_bus(self):
        r = self.plan([100, 400])  # 첫 차는 걷는 시간(240초)보다 빨리 와서 놓침
        self.assertEqual(r["verdict"], "wait")
        self.assertIn("🚌146번 08:06 →", r["message"])

    def test_miss_when_all_too_early(self):
        r = self.plan([60, 200])
        self.assertEqual(r["verdict"], "miss")
        self.assertIn("놓쳤어요", r["message"])

    def test_ignored_when_first_leg_is_subway(self):
        steps = [{"kind": "subway", "name": "수도권 1호선", "from": "A", "to": "B", "minutes": 20}]
        r = route.plan_message("집", None, NOW, 20, steps, 5, [10], 1)
        self.assertEqual(r["verdict"], "go")

    def test_first_bus_helper(self):
        self.assertEqual(route.first_bus(route.summarize(PATH)["steps"])["number"], "146")
        self.assertIsNone(route.first_bus([{"kind": "subway", "name": "x", "minutes": 1}]))


class GeocodeTest(unittest.TestCase):
    def setUp(self):
        self._orig_docs, self._orig_key = route._kakao_docs, route.require_key
        route.require_key = lambda name: "dummy"
        self.calls = []

    def tearDown(self):
        route._kakao_docs, route.require_key = self._orig_docs, self._orig_key

    def fake(self, address, keyword):
        def f(url, query, key):
            self.calls.append(url)
            return address if url == route.KAKAO_ADDRESS_URL else keyword
        route._kakao_docs = f

    def test_address_hit_uses_saved_name_not_shop_name(self):
        self.fake([{"x": "126.78", "y": "37.48"}], [{"x": "1", "y": "2", "place_name": "예시 상점"}])
        x, y, name = route.geocode("회사", {"회사": {"query": "경기도 예시시 예시로 1"}})
        self.assertEqual((x, y, name), (126.78, 37.48, "회사"))
        self.assertEqual(self.calls, [route.KAKAO_ADDRESS_URL])  # 키워드 검색까지 가지 않는다

    def test_falls_back_to_keyword_and_uses_place_name_for_adhoc_query(self):
        self.fake([], [{"x": "127.0", "y": "37.5", "place_name": "강남역"}])
        self.assertEqual(route.geocode("강남역", {}), (127.0, 37.5, "강남역"))
        self.assertEqual(self.calls, [route.KAKAO_ADDRESS_URL, route.KAKAO_KEYWORD_URL])

    def test_not_found(self):
        self.fake([], [])
        with self.assertRaises(RuntimeError):
            route.geocode("없는곳", {})

    def test_stored_coordinates_skip_api(self):
        self.fake([], [])
        self.assertEqual(route.geocode("집", {"집": {"x": 1, "y": 2}}), (1.0, 2.0, "집"))
        self.assertEqual(self.calls, [])


class FmtMinutesTest(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(route.fmt_minutes(45), "45분")
        self.assertEqual(route.fmt_minutes(60), "1시간")
        self.assertEqual(route.fmt_minutes(1003), "16시간 43분")


class MemoLimitTest(unittest.TestCase):
    def test_truncated_to_200_for_very_long_route(self):
        long_steps = [{"kind": "bus", "name": "%d번 버스" % (1000 + i), "from": "a", "to": "b", "minutes": 10}
                      for i in range(30)]
        r = route.plan_message("강남역", NOW + timedelta(minutes=300), NOW, 300, long_steps, 5)
        self.assertEqual(len(r["message"]), route.MEMO_LIMIT)
        self.assertTrue(r["message"].endswith("…"))

    def test_normal_route_not_truncated(self):
        steps = route.summarize(PATH)["steps"]
        r = route.plan_message("강남역", NOW + timedelta(minutes=45), NOW, 40, steps, 5)
        self.assertLess(len(r["message"]), route.MEMO_LIMIT)
        self.assertFalse(r["message"].endswith("…"))


class ParseArriveTest(unittest.TestCase):
    def test_rolls_to_next_day_when_past(self):
        self.assertEqual(route.parse_arrive("07:00", NOW).day, 27)
        self.assertEqual(route.parse_arrive("09:00", NOW).day, 26)


if __name__ == "__main__":
    unittest.main()
