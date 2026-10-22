import unittest

from booking.calendar import first_free_slot, is_free
from booking.intervals import overlaps


class HiddenTests(unittest.TestCase):
    def test_slot_right_after_booking(self):
        self.assertEqual(first_free_slot([(540, 600)], 540, 720, 60), 600)

    def test_empty_day(self):
        self.assertEqual(first_free_slot([], 540, 720, 30), 540)

    def test_last_slot_ending_at_day_end(self):
        self.assertEqual(first_free_slot([(540, 660)], 540, 720, 60), 660)

    def test_no_slot(self):
        self.assertIsNone(first_free_slot([(540, 700)], 540, 720, 30))

    def test_slot_before_booking(self):
        self.assertEqual(first_free_slot([(600, 660)], 540, 720, 60, step=60), 540)

    def test_overlaps_half_open(self):
        self.assertFalse(overlaps((0, 10), (10, 20)))
        self.assertTrue(overlaps((0, 11), (10, 20)))
        self.assertTrue(overlaps((12, 13), (10, 20)))

    def test_is_free_touching(self):
        self.assertTrue(is_free([(0, 10), (20, 30)], 10, 20))


if __name__ == "__main__":
    unittest.main()
