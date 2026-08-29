#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支撑压力 / 买卖点 / 公共工具（support_resistance / signals / common）离线测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common  # noqa: E402
import support_resistance as sr  # noqa: E402
import signals  # noqa: E402
import strategy_index  # noqa: E402


def make_history(n=100, base=20.0, amplitude=2.0):
    """构造震荡行情：围绕 base 上下波动，确保既有支撑也有压力"""
    bars = []
    import math
    for i in range(n):
        close = base + amplitude * math.sin(i / 5.0)
        bars.append({
            "date": f"2026-01-{i % 28 + 1:02d}",
            "open": close * 0.999,
            "close": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "volume": 100000 + (i % 7) * 1000,
        })
    return bars


class TestCommon(unittest.TestCase):
    def test_market_of(self):
        self.assertEqual(common.market_of("600036"), "sh")
        self.assertEqual(common.market_of("688036"), "sh")
        self.assertEqual(common.market_of("000725"), "sz")
        self.assertEqual(common.market_of("300750"), "sz")
        self.assertEqual(common.market_of("159582"), "sz")
        self.assertEqual(common.market_of("430047"), "bj")
        self.assertEqual(common.market_of("830001"), "bj")

    def test_to_float(self):
        self.assertEqual(common.to_float("12.34"), 12.34)
        self.assertEqual(common.to_float("-"), None)
        self.assertEqual(common.to_float(""), None)
        self.assertEqual(common.to_float(None), None)
        self.assertEqual(common.to_float("abc"), None)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "a", "b.json")  # 目录不存在 → 应自动创建
            common.save_json(p, {"x": [1, 2, "中文"]})
            self.assertEqual(common.load_json(p), {"x": [1, 2, "中文"]})
            self.assertIsNone(common.load_json(os.path.join(td, "missing.json")))
            self.assertEqual(common.load_json(os.path.join(td, "missing.json"), {}), {})


class TestSupportResistance(unittest.TestCase):
    def test_compute_levels_classification(self):
        hist = make_history()
        current = hist[-1]["close"]
        lv = sr.compute_levels(hist)
        for s in lv["supports"]:
            self.assertLess(s["price"], current)
        for r in lv["resistances"]:
            self.assertGreater(r["price"], current)
        self.assertTrue(len(lv["supports"]) <= 3)
        self.assertTrue(len(lv["resistances"]) <= 3)

    def test_strength_tags(self):
        lv = sr.compute_levels(make_history())
        for item in lv["supports"] + lv["resistances"]:
            self.assertIn(item["strength"], ("强", "中", "弱"))
            self.assertIn("sources", item)

    def test_density_profile(self):
        levels, dens, lo, hi = sr.density_profile(make_history(), days=60, bins=50)
        self.assertEqual(len(levels), len(dens))
        self.assertEqual(len(levels), 50)
        self.assertTrue(all(0 <= d <= 1 for d in dens))
        self.assertLess(lo, hi)

    def test_short_history_returns_empty(self):
        self.assertEqual(sr.compute_levels(make_history(10))["supports"], [])


class TestSignals(unittest.TestCase):
    def test_compute_signals(self):
        hist = make_history()
        intraday = {
            "date": "2026-01-28",
            "prev_close": 20.0,
            "minutes": [
                {"t": "09:30", "p": 19.8, "avg": 19.85, "v": 100},
                {"t": "09:31", "p": 19.9, "avg": 19.87, "v": 150},
                {"t": "09:32", "p": 20.1, "avg": 19.9, "v": 200},
            ],
        }
        s = signals.compute_signals(hist, intraday)
        self.assertIn("daily_buy", s)
        self.assertIn("intraday_buy", s)
        # 日线买点=支撑位
        self.assertEqual(len(s["daily_buy"]), len(sr.compute_levels(hist)["supports"]))
        # 分时卖点含日内高点
        self.assertTrue(any(p["price"] == 20.1 for p in s["intraday_sell"]))

    def test_intraday_points_require_data(self):
        s = signals.compute_signals(make_history(), None)
        self.assertEqual(s["intraday_buy"], [])
        self.assertEqual(s["intraday_sell"], [])


class TestStrategyIndex(unittest.TestCase):
    def test_build_index_reads_yaml(self):
        strategies = strategy_index.build_index()
        names = {s.get("name") for s in strategies}
        self.assertIn("dragon_head", names)
        self.assertIn("mean_reversion", names)
        for s in strategies:
            self.assertIn("display_name", s)


if __name__ == "__main__":
    unittest.main()
