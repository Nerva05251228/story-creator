import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import db_compat


class DbCompatTests(unittest.TestCase):
    def test_datetime_sql_for_postgresql(self):
        self.assertEqual(db_compat.datetime_sql_for_dialect("postgresql"), "TIMESTAMP")

    def test_datetime_sql_for_sqlite(self):
        self.assertEqual(db_compat.datetime_sql_for_dialect("sqlite"), "DATETIME")


if __name__ == "__main__":
    unittest.main()
