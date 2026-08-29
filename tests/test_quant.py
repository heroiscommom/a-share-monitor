#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化因子引擎（quant.py）离线单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quant  # noqa: E402


def make_history(n=120, start=10.0, end=6.0):
    """构造 n 根线性下降的日K（超跌场景），含日期/高低收量"""
    bars = []
    for i in range(n):
        t = i / max(n - 1, 1)
        close = start + (end - start) * t
        bars.append({
            "date": f"2026-01-{i % 28 + 1:02d}",
            "open": close * 0.998,
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": 100000 + i * 100,
        })
    return bars


class TestQuantFactors(unittest.TestCase):
    def test_compute_factors_bounds(self):
        ind, fac = quant.compute_factors(make_history())
        self.assertEqual(set(fac.keys()), {"rsi", "drawdown", "deviation", "position", "volume", "volatility"})
        for k, v in fac.items():
            self.assertTrue(0 <= v <= 100, f"{k}={v} 超出 [0,100]")

    def test_compute_score_range(self):
        _, fac = quant.compute_factors(make_history())
        score = quant.compute_score(fac)
        self.assertTrue(0 <= score <= 100)
        self.assertEqual(score, quant.compute_score(fac))  # 确定性

    def test_downtrend_scores_oversold(self):
        # 深度超跌 → 均值回归评分应较高（接近 BUY_THRESHOLD 或以上）
        _, fac = quant.compute_factors(make_history(120, 20.0, 5.0))
        score = quant.compute_score(fac)
        self.assertGreaterEqual(score, quant.BUY_THRESHOLD - 15)

    def test_signal_from_score(self):
        self.assertEqual(quant.signal_from_score(90)[0], "超跌机会")
        self.assertEqual(quant.signal_from_score(90)[1], "strong")
        self.assertEqual(quant.signal_from_score(10)[0], "高位风险")
        self.assertEqual(quant.signal_from_score(10)[1], "weak")

    def test_momentum_factors_bounds(self):
        ind, fac = quant.momentum_factors(make_history())
        for k in ("mom20", "mom60", "rsi", "pos", "vol", "act"):
            self.assertIn(k, fac)
            self.assertTrue(0 <= fac[k] <= 100, f"{k}={fac[k]}")

    def test_market_regime(self):
        import random
        random.seed(42)
        # 单边上涨序列 → 上涨市
        up = [100 * (1.002 ** i) for i in range(40)]
        self.assertEqual(quant.market_regime(up)["regime"], "上涨")
        # 单边下跌序列 → 下跌市
        down = [100 * (0.998 ** i) for i in range(40)]
        self.assertEqual(quant.market_regime(down)["regime"], "下跌")
        # 数据不足 → 未知
        self.assertEqual(quant.market_regime([])["regime"], "未知")

    def test_ma_golden_cross_and_shrink(self):
        hist = make_history(120)
        maf = quant.ma_golden_cross_factors(hist)
        self.assertIn("cross", maf)
        self.assertTrue(0 <= quant.ma_golden_cross_score(maf) <= 100)
        sf = quant.shrink_pullback_factors(hist)
        self.assertIn("trend", sf)
        self.assertTrue(0 <= quant.shrink_pullback_score(sf) <= 100)


if __name__ == "__main__":
    unittest.main()
