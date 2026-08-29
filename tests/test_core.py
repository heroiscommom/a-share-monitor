#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支撑压力 / 买卖点 / 公共工具（support_resistance / signals / common）离线测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common  # noqa: E402
import datafeed  # noqa: E402
import support_resistance as sr  # noqa: E402
import signals  # noqa: E402
import strategy_index  # noqa: E402
import quant  # noqa: E402


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

    def test_signal_rules_nested_and_factors_dicts(self):
        """2026-08 重构：signal_rules 应为嵌套 dict，factors 应为 dict 列表"""
        strategies = {s["name"]: s for s in strategy_index.build_index()}
        mr = strategies["mean_reversion"]
        self.assertEqual(mr["signal_rules"]["strong"], 82)   # 不再被提升到顶层
        self.assertNotIn("buy", mr)
        # factors 是 dict 列表
        self.assertIsInstance(mr["factors"], list)
        self.assertIsInstance(mr["factors"][0], dict)
        self.assertIn("weight", mr["factors"][0])
        # 各策略 signal_rules 均为完整分级
        for name in ("mean_reversion", "momentum", "ma_golden_cross", "shrink_pullback"):
            rules = strategies[name]["signal_rules"]
            for key in ("strong", "bullish", "neutral", "weak"):
                self.assertIn(key, rules, f"{name} 缺 {key}")


class TestQuantSignalRules(unittest.TestCase):
    """D1：yaml 单一事实源 —— yaml 阈值与默认一致，行为不变"""

    def test_yaml_rules_match_defaults(self):
        self.assertEqual(quant.signal_rules("mean_reversion")["strong"], quant.BUY_THRESHOLD)
        self.assertEqual(quant.signal_rules("mean_reversion")["bearish"], quant.RISK_THRESHOLD)
        self.assertEqual(quant.signal_rules("momentum")["strong"], quant.MOM_STRONG_THRESHOLD)

    def test_signal_behavior_unchanged(self):
        self.assertEqual(quant.signal_from_score(90)[0], "超跌机会")
        self.assertEqual(quant.signal_from_score(90)[1], "strong")
        self.assertEqual(quant.signal_from_score(80)[0], "偏多")
        self.assertEqual(quant.signal_from_score(50)[0], "中性")
        self.assertEqual(quant.signal_from_score(35)[0], "偏空")
        self.assertEqual(quant.signal_from_score(10)[0], "高位风险")


class TestDatafeedParsing(unittest.TestCase):
    """D3：保护脆弱字段索引 —— 腾讯行情负载解析"""

    def _payload(self):
        parts = [""] * 50
        parts[1] = "招商银行"
        parts[2] = "600036"
        parts[3] = "36.60"    # 最新价
        parts[4] = "36.48"    # 昨收
        parts[5] = "36.50"    # 今开
        parts[6] = "123456"   # 成交量(手)
        parts[31] = "0.12"    # 涨跌额
        parts[32] = "0.33"    # 涨跌幅%
        parts[33] = "36.90"   # 最高
        parts[34] = "36.10"   # 最低
        parts[37] = "12345"   # 成交额(万)
        parts[38] = "0.55"    # 换手率
        parts[39] = "6.5"     # PE
        parts[44] = "8000"    # 流通市值(亿)
        parts[45] = "9000"    # 总市值(亿)
        parts[46] = "0.9"     # PB
        return "~".join(parts)

    def test_parse_quote_payload(self):
        q = datafeed.parse_quote_payload(self._payload())
        self.assertIsNotNone(q)
        self.assertEqual(q["code"], "600036")
        self.assertEqual(q["name"], "招商银行")
        self.assertEqual(q["price"], 36.60)
        self.assertEqual(q["prev_close"], 36.48)
        self.assertEqual(q["change_pct"], 0.33)
        self.assertEqual(q["amount"], 12345 * 10000)
        self.assertEqual(q["turnover_rate"], 0.55)
        self.assertEqual(q["pe"], 6.5)
        self.assertEqual(q["float_mktcap"], 8000)
        self.assertEqual(q["pb"], 0.9)

    def test_parse_quote_payload_short(self):
        self.assertIsNone(datafeed.parse_quote_payload("a~b~c"))

    def test_parse_fqkline(self):
        node = {"qfqday": [
            ["2026-01-05", "10", "10.5", "10.8", "9.9", "1000"],
            ["bad-row"],  # 长度不足应被跳过
        ]}
        bars = datafeed.parse_fqkline(node)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["date"], "2026-01-05")
        self.assertEqual(bars[0]["close"], 10.5)
        self.assertEqual(bars[0]["volume"], 1000)
        self.assertEqual(datafeed.parse_fqkline({"day": []}), [])


class TestMonitorTiers(unittest.TestCase):
    """信号分级（tier_for / effective_tier）"""

    def test_tier_for(self):
        import monitor
        self.assertEqual(monitor.tier_for("quant_strong"), "S")
        self.assertEqual(monitor.tier_for("quant_weak"), "S")
        self.assertEqual(monitor.tier_for("break_support"), "A")
        self.assertEqual(monitor.tier_for("rsi_overbought"), "B")
        self.assertEqual(monitor.tier_for("change_pct_up"), "C")

    def test_effective_tier_by_regime(self):
        import monitor
        # 超跌信号：下跌市 S / 上涨市 B（回测门控）
        self.assertEqual(monitor.effective_tier("quant_strong", "下跌"), "S")
        self.assertEqual(monitor.effective_tier("quant_strong", "震荡"), "A")
        self.assertEqual(monitor.effective_tier("quant_strong", "上涨"), "B")
        # 动量信号：上涨市 S
        self.assertEqual(monitor.effective_tier("momentum_strong", "上涨"), "S")
        self.assertEqual(monitor.effective_tier("momentum_strong", "下跌"), "B")


class TestPortfolioAdvice(unittest.TestCase):
    """portfolio.advice_one 规则引擎"""

    def _position(self, cost, shares=100):
        return {"code": "600036", "market": "sh", "name": "测试股", "shares": shares, "cost": cost}

    def test_no_data_returns_hold(self):
        import portfolio as pf
        a = pf.advice_one(self._position(10), None, [])
        self.assertEqual(a["advice"], "持有")
        self.assertEqual(a["reason"], "暂无数据")

    def test_stop_loss_when_breaking_support(self):
        import portfolio as pf
        hist = make_history(100)
        current = hist[-1]["close"]
        support = sr.compute_levels(hist)["supports"][0]["price"]
        # 浮亏≥8% 且 价格已跌破最近支撑
        a = pf.advice_one(self._position(round(current * 1.15, 3)), {"price": support - 0.5}, hist)
        self.assertEqual(a["advice"], "止损")

    def test_take_profit_near_resistance(self):
        import portfolio as pf
        hist = make_history(100)
        current = hist[-1]["close"]
        resistance = sr.compute_levels(hist)["resistances"][0]["price"]
        # 浮盈≥20% 且 价格贴近压力位（≤1.5%）
        a = pf.advice_one(self._position(round(current * 0.75, 3)), {"price": round(resistance * 0.99, 3)}, hist)
        self.assertEqual(a["advice"], "分批止盈")

    def test_hold_neutral(self):
        import portfolio as pf
        hist = make_history(100)
        current = hist[-1]["close"]
        a = pf.advice_one(self._position(round(current, 3)), {"price": current}, hist)
        self.assertEqual(a["advice"], "持有")


class TestMonitorProcessStocks(unittest.TestCase):
    """monitor.process_stocks 并发执行（2026-08：线程池 + 修复 today/竞态）"""

    def test_process_stocks_parallel(self):
        import datetime
        import tempfile
        import datafeed
        import monitor

        hist = make_history(80)
        orig = (datafeed.fetch_history, datafeed.fetch_intraday, datafeed.fetch_moneyflow)
        # 重定向落盘到临时目录，避免测试污染真实 data/history
        with tempfile.TemporaryDirectory() as td:
            orig_dirs = (monitor.HISTORY_DIR, monitor.INTRADAY_DIR)
            monitor.HISTORY_DIR = td
            monitor.INTRADAY_DIR = td
            try:
                datafeed.fetch_history = lambda code, market, days=60: hist
                datafeed.fetch_intraday = lambda code, market: {
                    "date": "2026-01-28", "prev_close": 20.0,
                    "minutes": [{"t": "09:30", "p": 19.9, "avg": 19.95, "v": 100}],
                }
                datafeed.fetch_moneyflow = lambda code, market: {
                    "date": "2026-01-28", "netamount": 1000000, "r0_net": 0, "change_pct": 1.0,
                }
                rules = {"moneyflow_threshold": 50000000, "change_pct": 3.0}
                state, triggered, qr, sr_r, sig_r, mf_r = {}, [], [], [], [], []
                watchlist = [{"code": "600036", "market": "sh", "name": "测试A"},
                             {"code": "000725", "market": "sz", "name": "测试B"}]
                quotes = {"600036": {"code": "600036", "price": 20.0, "change_pct": 1.0},
                          "000725": {"code": "000725", "price": 5.0, "change_pct": -1.0}}
                now = datetime.datetime(2026, 1, 28, 10, 0, 0)
                monitor.process_stocks(watchlist, quotes, rules, 60, False, now, {"regime": "震荡"}, False,
                                       state, triggered, qr, sr_r, sig_r, mf_r)
                self.assertEqual(len(qr), 2)
                self.assertEqual({q["code"] for q in qr}, {"600036", "000725"})
                for q in qr:
                    self.assertIn("ma_score", q)          # 新策略填充（原 quant_results[-1] 竞态已修复）
                    self.assertIn("shrink_score", q)
                    self.assertIn("resonance", q)         # 策略共振
                self.assertEqual(len(sr_r), 2)
                self.assertEqual(len(sig_r), 2)
            finally:
                datafeed.fetch_history, datafeed.fetch_intraday, datafeed.fetch_moneyflow = orig
                monitor.HISTORY_DIR, monitor.INTRADAY_DIR = orig_dirs


if __name__ == "__main__":
    unittest.main()
