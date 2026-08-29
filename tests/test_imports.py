#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归守卫：所有 Python 模块应能离线导入。
2026-08 重构后新增 —— 防止 common/datafeed/notify 抽取时漏改某个脚本。
"""

import importlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODULES = [
    "common", "datafeed", "notify", "quant", "support_resistance", "signals",
    "zone_history", "backtest", "sector", "scanner", "dragon_head",
    "strategy_index", "portfolio", "pool_backtest", "portfolio_backtest",
    "picks", "trade", "trade_command", "digest", "monitor", "morning_report",
    "weekly_review", "advice_history", "auto_report", "ai_report",
    "signal_history", "manage_watchlist", "push_api", "verify_dragon",
    "verify_sr_v2", "pool_backtest",
]


class TestImports(unittest.TestCase):
    def test_all_modules_import(self):
        failures = []
        for name in MODULES:
            try:
                importlib.import_module(name)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{name}: {type(e).__name__}: {e}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_sentiment_package_imports(self):
        import sentiment.config
        import sentiment.fetchers
        import sentiment.notifier
        import sentiment.runner
        import sentiment.scoring
        import sentiment.storage
        self.assertTrue(sentiment.config.PUSH_TOP_N > 0)


if __name__ == "__main__":
    unittest.main()
