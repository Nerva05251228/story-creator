import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import test2


class Test2SeedanceScriptTests(unittest.TestCase):
    def test_payload_is_plain_literal_without_reference_items_helper(self):
        self.assertFalse(hasattr(test2, "REFERENCE_ITEMS"))
        self.assertTrue(hasattr(test2, "PAYLOAD"))
        self.assertIsInstance(test2.PAYLOAD, dict)
        self.assertEqual(test2.PAYLOAD["model"], "doubao-seedance-2-0-260128-fast")
        self.assertEqual(test2.PAYLOAD["ratio"], "16:9")
        self.assertEqual(test2.PAYLOAD["duration"], 6)
        self.assertIsInstance(test2.PAYLOAD["content"], list)
        self.assertEqual(test2.PAYLOAD["content"][0]["type"], "text")
        self.assertEqual(test2.PAYLOAD["content"][1]["type"], "image_url")
        self.assertEqual(test2.PAYLOAD["content"][2]["type"], "image_url")
        self.assertEqual(test2.PAYLOAD["content"][3]["type"], "image_url")


if __name__ == "__main__":
    unittest.main()
