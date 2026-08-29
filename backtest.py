#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测引擎 —— 检验量化评分是否对未来收益有预测力。

方法：滚动回放历史。在每个历史交易日，只用"当时已知"的数据重算因子评分，
记录该评分与"未来 N 日收益"的对应关系，最后按评分分组统计收益与胜率，
并计算评分与未来收益的相关系数（IC）。

注意：这是"因子有效性"检验，不是交易系统回测（未考虑手续费、滑点、T+1 等）。
"""

import os
import json
import math
import datetime

import quant

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "data", "history")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUT_PATH = os.path.join(BASE_DIR, "data", "backtest.json")

FORWARD_DAYS = 10      # 未来持有天数
MIN_HISTORY = 60       # 因子计算所需最少历史

# ===== 严谨性设置（2026-08-17 优化）=====
# 交易成本拆分为可配置组件（2026-08 重构）：
#   A股实盘参考：佣金万2.5×2=0.05% + 印花税0.05%(卖出) + 过户费0.002% + 滑点0.1~0.2%
#   → 实际双边约 0.15%~0.25%。默认 0.25% 偏保守（低估策略收益，结论更可信）。
# 敏感性分析：BACKTEST_COST_PCT=0.15 python3 pool_backtest.py
COMMISSION_PCT = 0.05      # 佣金 双边 %
STAMP_PCT = 0.05           # 印花税（卖出）%
SLIPPAGE_PCT = 0.15        # 滑点 %
COST_PCT = float(os.environ.get("BACKTEST_COST_PCT", COMMISSION_PCT + STAMP_PCT + SLIPPAGE_PCT))
NON_OVERLAP = True     # 非重叠抽样：每 FORWARD_DAYS 日取 1 个样本（消除自相关）


def limit_pct(code):
    """涨跌停幅度：创业板/科创板 20%，北交所 30%，主板 10%"""
    if code.startswith(("688", "300", "301", "302")):
        return 0.20
    if code.startswith(("43", "83", "87", "92")):
        return 0.30
    return 0.10


def is_limit_up(k, prev_close, pct):
    """一字涨停：收盘=最高 且 触及涨停价 → 买不进"""
    if not prev_close:
        return False
    return k["close"] == k["high"] and k["close"] >= prev_close * (1 + pct - 0.005)


def is_limit_down(k, prev_close, pct):
    """一字跌停：收盘=最低 且 触及跌停价 → 卖不出"""
    if not prev_close:
        return False
    return k["close"] == k["low"] and k["close"] <= prev_close * (1 - pct + 0.005)


def load_history(code):
    path = os.path.join(HISTORY_DIR, f"{code}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backtest_stock(history, forward_days, code=""):
    """
    滚动回测单只股票，返回样本 [{score, fwd_return}]
    严谨版：
      1. 非重叠抽样（每 forward_days 日取 1 个样本）
      2. 收益扣双边成本 COST_PCT
      3. 买入日一字涨停（买不进）→ 剔除；卖出日一字跌停（卖不出）→ 剔除
    """
    samples = []
    n = len(history)
    lp = limit_pct(code)
    i = MIN_HISTORY
    while i <= n - forward_days - 1:
        try:
            _, fac = quant.compute_factors(history[: i + 1])
            score = quant.compute_score(fac)
        except Exception:
            i += forward_days
            continue
        try:
            _, mfac = quant.momentum_factors(history[: i + 1])
            mscore = quant.momentum_score(mfac)
        except Exception:
            mscore = None
        base = history[i].get("close")
        fwd = history[i + forward_days].get("close")
        prev_close = history[i - 1].get("close") if i > 0 else None
        # 可交易性过滤（T+1 的实操约束）
        if is_limit_up(history[i], prev_close, lp):
            i += forward_days
            continue
        if is_limit_down(history[i + forward_days], history[i + forward_days - 1].get("close"), lp):
            i += forward_days
            continue
        if base and fwd:
            samples.append({
                "score": score,
                "mscore": mscore,
                "date": history[i].get("date", ""),
                "fwd_return": round((fwd / base - 1) * 100 - COST_PCT, 2),
            })
        i += forward_days
    return samples


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def group_stats(samples):
    bins = [
        ("<45", lambda s: s < 45),
        ("45-55", lambda s: 45 <= s < 55),
        ("55-65", lambda s: 55 <= s < 65),
        ("65-75", lambda s: 65 <= s < 75),
        ("≥75", lambda s: s >= 75),
    ]
    groups = []
    for label, cond in bins:
        g = [s for s in samples if cond(s["score"])]
        if not g:
            groups.append({"label": label, "count": 0, "avg_return": None, "win_rate": None})
            continue
        avg = sum(s["fwd_return"] for s in g) / len(g)
        win = sum(1 for s in g if s["fwd_return"] > 0) / len(g) * 100
        groups.append({
            "label": label,
            "count": len(g),
            "avg_return": round(avg, 2),
            "win_rate": round(win, 1),
        })
    return groups


def make_conclusion(groups, ic):
    def avg(label):
        g = next((x for x in groups if x["label"] == label), None)
        return g["avg_return"] if g and g["avg_return"] is not None else None

    low = avg("<45")
    mid = avg("55-65")
    high = avg("≥75")

    parts = []
    if low is not None and high is not None and mid is not None:
        # U型：两端都明显跑赢中间
        if high > mid + 0.5 and low > mid + 0.5:
            parts.append(f"U型关系：超跌股（{high:.2f}%）和强势股（{low:.2f}%）都跑赢中间档（{mid:.2f}%）")
            if high >= low:
                parts.append("其中「超跌」信号最强、胜率最高")
            else:
                parts.append("其中「强势」信号更强")
        elif high > low + 0.5:
            parts.append(f"超跌股（{high:.2f}%）跑赢强势股（{low:.2f}%），均值回归有效")
        elif low > high + 0.5:
            parts.append(f"强势股（{low:.2f}%）跑赢超跌股（{high:.2f}%），动量有效")
        else:
            parts.append(f"各组收益差异不大（{low:.2f}% ~ {high:.2f}%），评分区分度低")
    if ic is not None:
        parts.append(f"线性 IC={ic:.3f}")
    return "；".join(parts) if parts else "样本不足，暂无法判断"


def run():
    cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
    watchlist = cfg.get("watchlist", [])

    all_samples = []
    per_stock = []
    for s in watchlist:
        history = load_history(s["code"])
        if not history or len(history) < MIN_HISTORY + FORWARD_DAYS:
            continue
        samples = backtest_stock(history, FORWARD_DAYS, s["code"])
        all_samples.extend(samples)
        avg = sum(x["fwd_return"] for x in samples) / len(samples) if samples else 0.0
        per_stock.append({
            "code": s["code"], "name": s["name"],
            "samples": len(samples), "avg_fwd_return": round(avg, 2),
        })

    if not all_samples:
        result = {
            "forward_days": FORWARD_DAYS,
            "total_samples": 0,
            "groups": [],
            "ic": None,
            "per_stock": [],
            "conclusion": "样本不足，无法回测（需要每只股票 ≥ 70 天历史）",
        }
    else:
        groups = group_stats(all_samples)
        scores = [s["score"] for s in all_samples]
        rets = [s["fwd_return"] for s in all_samples]
        ic = round(pearson(scores, rets), 3)
        result = {
            "forward_days": FORWARD_DAYS,
            "total_samples": len(all_samples),
            "groups": groups,
            "ic": ic,
            "per_stock": per_stock,
            "conclusion": make_conclusion(groups, ic),
        }

    result["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["methodology"] = (
        f"非重叠抽样(每{FORWARD_DAYS}日1样本) · 双边成本{COST_PCT}% · "
        f"剔除一字涨停买入/一字跌停卖出"
    )
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main():
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
