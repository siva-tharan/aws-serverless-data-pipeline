"""Unit tests for the CSV validator. Run with: python -m unittest discover -s tests -v"""

import os
import sys
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from csv_validator import CsvFormatError, parse_orders  # noqa: E402

HEADER = "order_id,customer_name,product,quantity,unit_price,order_date\n"


class ParseOrdersTest(unittest.TestCase):
    def test_valid_rows_are_parsed(self):
        orders, errors = parse_orders(
            HEADER
            + "ORD-1,Priya,Laptop,1,55999.00,2026-11-01\n"
            + "ORD-2,Arun,Mouse,3,499.5,2026-11-02\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[1].unit_price, Decimal("499.50"))
        self.assertEqual(orders[1].order_date, date(2026, 11, 2))

    def test_header_is_case_and_space_insensitive(self):
        orders, errors = parse_orders(
            " Order_ID , Customer_Name,PRODUCT,Quantity,Unit_Price,Order_Date\n"
            "ORD-1,Priya,Laptop,1,10,2026-11-01\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(orders[0].order_id, "ORD-1")

    def test_bad_rows_are_reported_with_line_numbers(self):
        orders, errors = parse_orders(
            HEADER
            + "ORD-1,Priya,Laptop,1,10,2026-11-01\n"
            + "ORD-2,Arun,Mouse,zero,10,2026-11-01\n"
            + "ORD-3,Meena,Desk,1,-5,2026-11-01\n"
            + "ORD-4,Karthik,Chair,1,10,01/11/2026\n"
            + "ORD-5,,Pen,1,10,2026-11-01\n"
        )
        self.assertEqual([o.order_id for o in orders], ["ORD-1"])
        self.assertEqual(len(errors), 4)
        self.assertTrue(errors[0].startswith("line 3:"))
        self.assertIn("quantity", errors[0])
        self.assertIn("unit_price", errors[1])
        self.assertIn("order_date", errors[2])
        self.assertIn("customer_name is empty", errors[3])

    def test_duplicate_order_ids_keep_first(self):
        orders, errors = parse_orders(
            HEADER
            + "ORD-1,Priya,Laptop,1,10,2026-11-01\n"
            + "ORD-1,Priya,Laptop,2,10,2026-11-01\n"
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].quantity, 1)
        self.assertIn("duplicate order_id", errors[0])

    def test_nan_and_infinite_prices_are_rejected(self):
        _, errors = parse_orders(
            HEADER + "ORD-1,Priya,Laptop,1,NaN,2026-11-01\nORD-2,Arun,Mouse,1,Infinity,2026-11-01\n"
        )
        self.assertEqual(len(errors), 2)

    def test_missing_column_rejects_file(self):
        with self.assertRaises(CsvFormatError):
            parse_orders("order_id,customer_name,product\nORD-1,Priya,Laptop\n")

    def test_empty_file_rejects_file(self):
        with self.assertRaises(CsvFormatError):
            parse_orders("")


if __name__ == "__main__":
    unittest.main()
