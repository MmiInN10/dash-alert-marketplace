import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import arrivals  # noqa: E402


class DecideTest(unittest.TestCase):
    # 도보 5분(300초), 여유 1분(60초) 기준
    def d(self, arr):
        return arrivals.decide(arr, 5, 1, "T")["verdict"]

    def test_empty(self):
        self.assertEqual(self.d([]), "none")

    def test_wait_when_plenty_of_time(self):
        self.assertEqual(self.d([361]), "wait")

    def test_run_at_buffer_boundary(self):
        self.assertEqual(self.d([360]), "run")

    def test_run_at_exact_walk_time(self):
        self.assertEqual(self.d([300]), "run")

    def test_too_late(self):
        self.assertEqual(self.d([299]), "none")

    def test_skips_missed_first_and_takes_second(self):
        r = arrivals.decide([100, 330], 5, 1, "T")
        self.assertEqual(r["verdict"], "run")
        self.assertIn("앞 차는 놓침", r["message"])

    def test_unsorted_input(self):
        self.assertEqual(self.d([800, 330]), "run")


class MsgToSecTest(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(arrivals.msg_to_sec("3분20초후[2번째 전]"), 200)
        self.assertEqual(arrivals.msg_to_sec("5분후"), 300)
        self.assertEqual(arrivals.msg_to_sec("곧 도착"), 0)
        self.assertIsNone(arrivals.msg_to_sec("운행종료"))
        self.assertIsNone(arrivals.msg_to_sec(None))


class FindKeyTest(unittest.TestCase):
    def test_nested(self):
        d = {"response": {"msgBody": {"busArrivalItem": {"predictTimeSec1": "90"}}}}
        self.assertEqual(arrivals.find_key(d, "busArrivalItem"), {"predictTimeSec1": "90"})
        self.assertIsNone(arrivals.find_key(d, "nope"))


if __name__ == "__main__":
    unittest.main()
