#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
断板低吸策略回测 v2（2026-08-26）
================================================
v1 用东财涨停池历史,但接口只保留近 15 个交易日,样本不足。
v2 改用 data/pool 300只×750天 日K:直接检测涨停/连板/断板事件,
   在断板日用支撑压力 v2 验证「回调至支撑+守住率」是否提升胜率。

涨停判定(与 backtest.py 口径一致):
  涨幅 ≥ 板块涨跌幅限制(主板10%/创业科创20%/北交所30%)的 98%
  (即 close >= prev_close * (1 + limit - 0.005))

流程:
  1. 每只股票逐日检测涨停 → 连续涨停天数(连板)
  2. 断板事件: t日连板≥2, t+1日非涨停
  3. 买入: 断板次日开盘,持有10日卖出(扣双边成本0.25%)
  4. 分组: 全断板 / 接近支撑0~3% / 接近支撑+守住率≥60%,按连板高度分层
"""

import os
import json

import support_resistance as sr
import backtest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(BASE_DIR, "data", "pool")

FORWARD_DAYS = 10
COST = backtest.COST_PCT
SUPPORT_BAND = 3.0


def is_zt(k, prev_close, limit):
    """涨停: 涨幅达到限制的98%以上（允许差0.5%以内）"""
    if not prev_close or prev_close <= 0:
        return False
    return k["close"] >= prev_close * (1 + limit - 0.005)


def main():
    print("[1/3] 加载股票池...")
    pool = {}
    for fn in os.listdir(POOL_DIR):
        if not fn.endswith(".json"):
            continue
        code = fn[:-5]
        try:
            hist = json.load(open(os.path.join(POOL_DIR, fn), encoding="utf-8"))
            if len(hist) >= 100:
                pool[code] = hist
        except Exception:
            continue
    print(f"  {len(pool)} 只股票")

    print("[2/3] 检测涨停/连板/断板事件...")
    events = []  # {code, idx(断板日), lbc, name}
    total_zt = 0
    for code, hist in pool.items():
        limit = backtest.limit_pct(code)
        n = len(hist)
        streak = 0
        for i in range(1, n):
            if is_zt(hist[i], hist[i - 1]["close"], limit):
                streak += 1
                total_zt += 1
            else:
                if streak >= 2 and i + FORWARD_DAYS < n:
                    events.append({"code": code, "idx": i, "lbc": streak})
                streak = 0
    print(f"  涨停日 {total_zt} 个, 断板事件(连板≥2) {len(events)} 个")

    print("[3/3] 回测...")
    # 断板低吸: 断板后 D1~D5 天内, 任一天收盘进入支撑 0~3% 区间 → 次日开盘买入
    samples_all, samples_sup, samples_sup_hold, samples_late = [], [], [], []
    n_signal = 0
    for e in events:
        hist = pool[e["code"]]
        i = e["idx"]

        # 全断板组对照: 断板次日无脑买
        buy_price = hist[i + 1]["open"]
        sell_price = hist[i + FORWARD_DAYS]["close"]
        ret = (sell_price / buy_price - 1) * 100 - COST
        samples_all.append({"ret": ret, "code": e["code"], "lbc": e["lbc"]})

        # 对照2: 断板后第3日收盘买入（等几天但不看支撑）
        if i + 3 + FORWARD_DAYS < len(hist):
            ret3 = (hist[i + 3 + FORWARD_DAYS]["close"] / hist[i + 3]["close"] - 1) * 100 - COST
            samples_late.append({"ret": ret3, "code": e["code"], "lbc": e["lbc"]})

        # 断板日之前的数据算支撑（防未来信息）
        window = hist[:i + 1]
        if len(window) < 60:
            continue
        zones = sr.density_zones(window, days=60, bins=50, n=3)
        sups = [z for z in zones if z["zone_type"] == "support"]
        if not sups:
            continue
        sup = max(sups, key=lambda z: z["strength"])

        # 断板后 D1~D5 找回调至支撑的时点（取第一次触发）
        buy_idx = None
        for j in range(i + 1, min(i + 6, len(hist) - FORWARD_DAYS - 1)):
            close_j = hist[j]["close"]
            dist = (close_j - sup["center"]) / close_j * 100
            if 0 <= dist <= SUPPORT_BAND:
                buy_idx = j
                break
        if buy_idx is None:
            continue
        n_signal += 1
        buy_price = hist[buy_idx + 1]["open"]
        sell_price = hist[buy_idx + FORWARD_DAYS]["close"]
        ret = (sell_price / buy_price - 1) * 100 - COST
        sample = {"ret": ret, "code": e["code"], "lbc": e["lbc"],
                  "dist": round((hist[buy_idx]["close"] - sup["center"]) / hist[buy_idx]["close"] * 100, 2),
                  "wait_days": buy_idx - i}
        samples_sup.append(sample)

        # 守住率（断板日前最多250天）
        st = max(20, i - 250)
        held = broke = 0
        for j in range(st, i - 5):
            h = hist[j]
            if h["low"] <= sup["center"] * 1.005 and h["close"] >= sup["center"] * 0.995:
                fut = [x["close"] for x in hist[j + 1:j + 6]]
                if len(fut) < 3:
                    continue
                if min(fut) >= sup["center"] * 0.98:
                    held += 1
                else:
                    broke += 1
        tc = held + broke
        hr = round(held / tc * 100, 1) if tc else None
        sample["held_rate"] = hr
        if hr is not None and hr >= 60:
            samples_sup_hold.append(sample)
    print(f"  断板后5日内回调至支撑: {n_signal} 个触发")

    def stats(name, samples):
        if len(samples) < 10:
            print(f"{name}: 样本不足({len(samples)})")
            return
        win = sum(1 for s in samples if s["ret"] > 0) / len(samples) * 100
        avg = sum(s["ret"] for s in samples) / len(samples)
        lbc3 = [s for s in samples if s["lbc"] >= 3]
        extra = ""
        if len(lbc3) >= 10:
            w3 = sum(1 for s in lbc3 if s["ret"] > 0) / len(lbc3) * 100
            a3 = sum(s["ret"] for s in lbc3) / len(lbc3)
            extra += f" | ≥3板: n={len(lbc3)} 胜率{w3:.1f}% 收益{a3:+.2f}%"
        print(f"{name}: n={len(samples)} 胜率={win:.1f}% 平均收益={avg:+.2f}%{extra}")

    print("\n" + "=" * 70)
    print("断板低吸回测(断板次日开盘买入,持有10日,双边成本0.25%,Top300池3年)")
    print("=" * 70)
    stats("全断板组(连板≥2断板)", samples_all)
    stats("对照: 断板后第3日无脑买", samples_late)
    stats("断板+5日内回调至支撑0~3%", samples_sup)
    stats("断板+接近支撑+守住率≥60%", samples_sup_hold)

    if len(samples_sup_hold) >= 5:
        print("\n--- 最优组明细(守住率≥60%) ---")
        for s in sorted(samples_sup_hold, key=lambda x: -x["ret"])[:12]:
            print(f"  {s['code']} 昨{s['lbc']}板 支撑距{s.get('dist')}% 守住率{s.get('held_rate')}% 10日{s['ret']:+.2f}%")


if __name__ == "__main__":
    main()
