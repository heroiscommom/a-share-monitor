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


def load_history(code):
    path = os.path.join(HISTORY_DIR, f"{code}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backtest_stock(history, forward_days):
    """滚动回测单只股票，返回样本 [{score, fwd_return}]"""
    samples = []
    n = len(history)
    for i in range(MIN_HISTORY, n - forward_days):
        try:
            _, fac = quant.compute_factors(history[: i + 1])
            score = quant.compute_score(fac)
        except Exception:
            continue
        base = history[i].get("close")
        fwd = history[i + forward_days].get("close")
        if base and fwd:
            samples.append({
                "score": score,
                "fwd_return": round((fwd / base - 1) * 100, 2),
            })
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
    if ic is None:
        return "样本不足，暂无法判断"
    if ic > 0.05:
        verdict = "✅ 评分对未来收益有正预测力"
    elif ic < -0.05:
        verdict = "⚠️ 评分呈负相关（当前因子方向可能反了，追高不利）"
    else:
        verdict = "➖ 评分几乎无预测力"
    lows = [g for g in groups if g["label"] in ("<45", "45-55") and g["avg_return"] is not None]
    highs = [g for g in groups if g["label"] in ("65-75", "≥75") and g["avg_return"] is not None]
    extra = ""
    if lows and highs:
        low_avg = sum(g["avg_return"] for g in lows) / len(lows)
        high_avg = sum(g["avg_return"] for g in highs) / len(highs)
        extra = f"；低分组未来{FORWARD_DAYS}日均收益 {low_avg:.2f}%，高分组 {high_avg:.2f}%"
    return f"{verdict}：IC={ic:.3f}{extra}"


def run():
    cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
    watchlist = cfg.get("watchlist", [])

    all_samples = []
    per_stock = []
    for s in watchlist:
        history = load_history(s["code"])
        if not history or len(history) < MIN_HISTORY + FORWARD_DAYS:
            continue
        samples = backtest_stock(history, FORWARD_DAYS)
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
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main():
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
