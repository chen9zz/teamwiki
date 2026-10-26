"""Checks that the benchmark cannot silently count failed requests as throughput."""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_concurrency.py"


class BenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("benchmark_concurrency", SCRIPT)
        cls.benchmark = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.benchmark
        spec.loader.exec_module(cls.benchmark)

    def test_report_distinguishes_expected_conflicts_from_failed_requests(self):
        samples = [(200, 10.0), (409, 20.0), (500, 30.0), ("ReadTimeout", 40.0)]
        report = self.benchmark.summarize(samples, 2.0, {200, 409})
        self.assertEqual(report["requests"], 4)
        self.assertEqual(report["status_counts"], {"200": 1, "409": 1, "500": 1, "ReadTimeout": 1})
        self.assertEqual(report["unexpected_requests"], 2)
        self.assertEqual(report["successful_rps"], 1.0)
        self.assertEqual(report["rps"], 2.0)
        self.assertEqual(report["latency_ms"]["p50"], 25.0)
        self.assertEqual(report["latency_ms"]["p95"], 38.5)

    def test_empty_report_has_no_fabricated_percentiles(self):
        report = self.benchmark.summarize([], 1.0, {200})
        self.assertEqual(report["rps"], 0)
        self.assertIsNone(report["latency_ms"]["p99"])

    def test_hot_page_requires_one_winner_and_only_expected_conflicts(self):
        self.assertTrue(self.benchmark.hot_batch_valid([200, 409, 409], 3))
        self.assertFalse(self.benchmark.hot_batch_valid([200, 200, 409], 3))
        self.assertFalse(self.benchmark.hot_batch_valid([200, 409, 500], 3))
        self.assertFalse(self.benchmark.hot_batch_valid([200, 409], 3))


if __name__ == "__main__":
    unittest.main()
