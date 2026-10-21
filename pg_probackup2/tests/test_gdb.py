import unittest
from unittest import mock

from parameterized import parameterized

from pg_probackup2 import gdb


class NeedsGdbTest(unittest.TestCase):
    def test_parameterized_args(self):
        with mock.patch.object(
            gdb, '_check_gdb_flag_or_skip_test', return_value=None
        ):
            class Sample(unittest.TestCase):
                @parameterized.expand([
                    [1],
                    [0],
                ])
                @gdb.needs_gdb
                def test_needs_gdb(self, flag):
                    self.assertTrue(self._gdb_decorated)
                    self.assertIn(flag, (0, 1))

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        result = unittest.TestResult()
        suite.run(result)

        self.assertEqual(result.testsRun, 2)
        self.assertFalse(result.errors)
        self.assertFalse(result.failures)
