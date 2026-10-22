import unittest

from booking.calendar import first_free_slot


class PublicTests(unittest.TestCase):
    def test_slot_right_after_booking(self):
        self.assertEqual(first_free_slot([(540, 600)], 540, 720, 60), 600)

    def test_empty_day(self):
        self.assertEqual(first_free_slot([], 540, 720, 30), 540)


if __name__ == "__main__":
    unittest.main()
