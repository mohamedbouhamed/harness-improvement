import copy
import unittest

from candidate import dedupe_events


def ev(id_, ts, data=None):
    return {"id": id_, "ts": ts, "data": data}


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_keeps_latest_and_sorts(self):
        events = [ev("a", 1), ev("b", 2), ev("a", 3)]
        self.assertEqual(dedupe_events(events), [ev("b", 2), ev("a", 3)])

    def test_out_of_order_duplicate(self):
        events = [ev("a", 5, "new"), ev("a", 2, "old")]
        self.assertEqual(dedupe_events(events), [ev("a", 5, "new")])

    def test_equal_timestamps_keep_first(self):
        events = [ev("x", 4, "first"), ev("x", 4, "second")]
        self.assertEqual(dedupe_events(events), [ev("x", 4, "first")])

    def test_ties_ordered_by_input_position_not_id(self):
        events = [ev("b", 1), ev("a", 1)]
        self.assertEqual(dedupe_events(events), [ev("b", 1), ev("a", 1)])

    def test_tie_position_is_that_of_kept_event(self):
        events = [ev("z", 1, "old"), ev("y", 7), ev("z", 7, "new")]
        self.assertEqual(dedupe_events(events), [ev("y", 7), ev("z", 7, "new")])

    def test_returns_original_objects(self):
        events = [ev("a", 1), ev("a", 2)]
        result = dedupe_events(events)
        self.assertIs(result[0], events[1])

    def test_input_not_modified(self):
        events = [ev("b", 2), ev("a", 1), ev("b", 1)]
        snapshot = copy.deepcopy(events)
        dedupe_events(events)
        self.assertEqual(events, snapshot)

    def test_empty(self):
        self.assertEqual(dedupe_events([]), [])

    def test_many_ids_mixed(self):
        events = [ev("c", 3), ev("a", 9), ev("b", 3), ev("a", 2), ev("c", 1), ev("d", 3)]
        self.assertEqual(dedupe_events(events), [ev("c", 3), ev("b", 3), ev("d", 3), ev("a", 9)])


if __name__ == "__main__":
    unittest.main()
