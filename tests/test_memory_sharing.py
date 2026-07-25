import unittest

import numpy as np
from qt_bootstrap import configure_qt_plugin_path

configure_qt_plugin_path()

from refactored_app import My3DAnalyzer


class CanonicalMemoryTests(unittest.TestCase):
    def test_loaded_data_has_one_read_only_canonical_array(self):
        data = np.arange(8 * 7 * 6 * 3, dtype=np.float32).reshape(8, 7, 6, 3)
        core_data, original_data, base_data = My3DAnalyzer._canonical_data_aliases(data)
        self.assertIs(core_data, original_data)
        self.assertIs(core_data, base_data)
        self.assertTrue(np.shares_memory(core_data, original_data))
        self.assertFalse(core_data.flags.writeable)


if __name__ == "__main__":
    unittest.main()
