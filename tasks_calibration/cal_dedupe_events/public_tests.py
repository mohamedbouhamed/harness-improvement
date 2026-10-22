import unittest

from candidate import dedupe_events


def ev(id_, ts, data=None):
    return {"id": id_, "ts": ts, "data": data}


class PublicTests(unittest.TestCase):
    def test_keeps_latest_and_sorts(self):
        events = [ev("a", 1), ev("b", 2), ev("a", 3)]
        self.assertEqual(dedupe_events(events), [ev("b", 2), ev("a", 3)])

    def test_out_of_order_duplicate(self):
        events = [ev("a", 5, "new"), ev("a", 2, "old")]
        self.assertEqual(dedupe_events(events), [ev("a", 5, "new")])

    def test_equal_timestamps_keep_first(self):
        events = [ev("x", 4, "first"), ev("x", 4, "second")]
        self.assertEqual(dedupe_events(events), [ev("x", 4, "first")])


if __name__ == "__main__":
    unittest.main()
