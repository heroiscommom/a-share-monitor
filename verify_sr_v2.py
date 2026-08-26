#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
支撑压力 v2 验证脚本（2026-08-26）
================================================
在 data/pool 300只 × 750天 上验证两件事：

A. 信号有效性（新旧算法对比）
   滚动采样（非重叠10日）：「当前价接近最近支撑位（上方0~3%）」时的未来10日收益。
   旧算法 = volume_profile top1 档；新算法 = density_zones 主区间。
   对比胜率/平均收益，并给全体样本基准。

B. 守住率的预测力（决策闭环验证）
   每个采样点用「此前250天」统计最近支撑位的历史守住率，
   按守住率高低分组看未来10日收益——守住率高的支撑是否更值得买。

方法对齐 backtest.py 严谨口径：非重叠 + 双边成本0.25% + 剔除一字涨停买入/跌停卖出。
"""

import os
import json
import sys

import support_resistance as sr
import backtest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(BASE_DIR, "data", "pool")

FORWARD_DAYS = 10
MIN_HISTORY = 60
STEP = FORWARD_DAYS
COST = backtest.COST_PCT
SUPPORT_BAND = 3.0      # 接近支撑：上方 0~3%
HISTORY_FOR_HOLD = 250  # 守住率统计窗口


def load_pool():
    out = {}
    for fn in os.listdir(POOL_DIR):
        if not fn.endswith(".json"):
            continue
        code = fn[:-5]
        try:
            with open(os.path.join(POOL_DIR, fn), "r", encoding="utf-8") as f:
                hist = json.load(f)
            if len(hist) >= MIN_HISTORY + FORWARD_DAYS + 10:
                out[code] = hist
        except Exception:
            continue
    return out


def hold_rate_of_support(hist, price, upto, tol=0.005):
    """用 hist[:upto] 统计支撑位 price 的历史守住率（与 zone_history 同口径）"""
    held = broke = 0
    start = max(20, upto - HISTORY_FOR_HOLD)
    for i in range(start, upto - 5):
        h = hist[i]
        if h["low"] <= price * (1 + tol) and h["close"] >= price * (1 - tol):
            future = [x["close"] for x in hist[i + 1:i + 6]]
            if len(future) < 3:
                continue
            if min(future) >= price * 0.98:
                held += 1
            else:
                broke += 1
    total = held + broke
    return (round(held / total * 100, 1) if total else None, total)


def main():
    pool = load_pool()
    print(f"股票池: {len(pool)} 只\n")

    samples_old = []   # 旧算法接近支撑样本
    samples_new = []   # 新算法接近支撑样本
    samples_all = []   # 全体基准（非重叠全样本）

    for code, hist in pool.items():
        n = len(hist)
        lp = backtest.limit_pct(code)
        i = MIN_HISTORY
        while i <= n - FORWARD_DAYS - 1:
            base = hist[i]["close"]
            fwd = hist[i + FORWARD_DAYS]["close"]
            prev_close = hist[i - 1]["close"] if i > 0 else None
            if backtest.is_limit_up(hist[i], prev_close, lp):
                i += STEP
                continue
            if backtest.is_limit_down(hist[i + FORWARD_DAYS], hist[i + FORWARD_DAYS - 1]["close"], lp):
                i += STEP
                continue
            fwd_ret = (fwd / base - 1) * 100 - COST
            samples_all.append(fwd_ret)

            window = hist[max(0, i - 60):i]
            if len(window) < 30:
                i += STEP
                continue

            # ---- 新算法：密度剖面主支撑 ----
            try:
                zones = sr.density_zones(window, days=60, bins=50, n=3)
                supports = [z for z in zones if z["zone_type"] == "support"]
                if supports:
                    sup = max(supports, key=lambda z: z["strength"])
                    dist = (base - sup["center"]) / base * 100
                    if 0 <= dist <= SUPPORT_BAND:
                        hr, tc = hold_rate_of_support(hist, sup["center"], i)
                        samples_new.append({
                            "ret": fwd_ret, "dist": dist, "strength": sup["strength"],
                            "in_zone": sup["low"] <= base <= sup["high"],
                            "held_rate": hr, "touch": tc,
                        })
            except Exception:
                pass

            # ---- 旧算法：volume_profile top1 ----
            try:
                vp = sr.volume_profile(window, days=60, bins=20)
                if vp:
                    old_price = vp[0][0]
                    if old_price < base:
                        dist = (base - old_price) / base * 100
                        if 0 <= dist <= SUPPORT_BAND:
                            samples_old.append({"ret": fwd_ret, "dist": dist})
            except Exception:
                pass

            i += STEP

    def stats(name, samples, extra=""):
        if not samples:
            print(f"{name}: 无样本")
            return
        n = len(samples)
        win = sum(1 for s in samples if s["ret"] > 0) / n * 100
        avg = sum(s["ret"] for s in samples) / n
        print(f"{name}: n={n} 胜率={win:.1f}% 平均收益={avg:+.2f}% {extra}")

    print("=" * 60)
    print("A. 信号有效性：「接近最近支撑位(上方0~3%)」未来10日收益")
    print("=" * 60)
    bench_win = sum(1 for r in samples_all if r > 0) / len(samples_all) * 100
    bench_avg = sum(samples_all) / len(samples_all)
    print(f"全体基准: n={len(samples_all)} 胜率={bench_win:.1f}% 平均收益={bench_avg:+.2f}%")
    stats("旧算法(volume_profile top1)", samples_old)
    stats("新算法(密度剖面主支撑)", samples_new)
    if samples_old and samples_new:
        print(f"\n新 vs 旧: 胜率差 {sum(1 for s in samples_new if s['ret']>0)/len(samples_new)*100 - sum(1 for s in samples_old if s['ret']>0)/len(samples_old)*100:+.1f}pp, 收益差 {sum(s['ret'] for s in samples_new)/len(samples_new) - sum(s['ret'] for s in samples_old)/len(samples_old):+.2f}pp")

    print()
    print("=" * 60)
    print("B. 守住率预测力（新算法样本按历史守住率分组）")
    print("=" * 60)
    with_hr = [s for s in samples_new if s.get("held_rate") is not None]
    if with_hr:
        buckets = [
            ("守住率≥70%", [s for s in with_hr if s["held_rate"] >= 70]),
            ("守住率50~70%", [s for s in with_hr if 50 <= s["held_rate"] < 70]),
            ("守住率<50%", [s for s in with_hr if s["held_rate"] < 50]),
        ]
        for name, g in buckets:
            if len(g) >= 10:
                win = sum(1 for s in g if s["ret"] > 0) / len(g) * 100
                avg = sum(s["ret"] for s in g) / len(g)
                print(f"{name}: n={len(g)} 胜率={win:.1f}% 平均收益={avg:+.2f}%")
        # 区间内 vs 区间外
        inz = [s for s in samples_new if s.get("in_zone")]
        outz = [s for s in samples_new if not s.get("in_zone")]
        if len(inz) >= 10:
            win = sum(1 for s in inz if s["ret"] > 0) / len(inz) * 100
            avg = sum(s["ret"] for s in inz) / len(inz)
            print(f"价格在支撑区间内: n={len(inz)} 胜率={win:.1f}% 平均收益={avg:+.2f}%")
        if len(outz) >= 10:
            win = sum(1 for s in outz if s["ret"] > 0) / len(outz) * 100
            avg = sum(s["ret"] for s in outz) / len(outz)
            print(f"价格在支撑区间外: n={len(outz)} 胜率={win:.1f}% 平均收益={avg:+.2f}%")
    else:
        print("无守住率样本")


if __name__ == "__main__":
    main()
