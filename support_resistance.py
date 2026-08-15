#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
支撑位/压力位计算引擎 —— 基于日K历史自动识别关键价位。

算法（三种方法交叉验证，来源越多价位越「强」）：
  1. N日高低点（20/60日最高=压力、最低=支撑）
  2. 摆动高低点（局部极值，左右各3天）
  3. 成交密集区（筹码分布：60天成交量按价格分档，成交量最大的档）
"""


def volume_profile(history, days=60, bins=20):
    """成交量价格分布，返回 [(价位, 成交量)] 按成交量降序"""
    data = history[-days:]
    lo = min(h["low"] for h in data)
    hi = max(h["high"] for h in data)
    if hi <= lo:
        return []
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for h in data:
        idx = int((h["close"] - lo) / step)
        idx = max(0, min(bins - 1, idx))
        vols[idx] += h["volume"]
    result = []
    for i, v in enumerate(vols):
        result.append((round(lo + (i + 0.5) * step, 2), v))
    result.sort(key=lambda x: -x[1])
    return result


def _strength(n):
    return "强" if n >= 3 else ("中" if n == 2 else "弱")


def compute_levels(history):
    """返回 {supports: [...], resistances: [...]}，各取最近3个"""
    if len(history) < 30:
        return {"supports": [], "resistances": []}

    current = history[-1]["close"]
    levels = {}  # price -> {"sources": [], "volume": 0}

    def add(price, source, vol=0.0):
        if price is None or price <= 0:
            return
        price = round(price, 2)
        if price not in levels:
            levels[price] = {"sources": [], "volume": 0.0}
        if source not in levels[price]["sources"]:
            levels[price]["sources"].append(source)
        levels[price]["volume"] += vol

    # 1. N日高低点
    for n in (20, 60):
        if len(history) >= n:
            seg = history[-n:]
            add(max(h["high"] for h in seg), f"{n}日高点")
            add(min(h["low"] for h in seg), f"{n}日低点")

    # 2. 摆动高低点（左右各3天）
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    k = 3
    for i in range(k, len(history) - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            add(highs[i], "前高")
        if lows[i] == min(lows[i - k:i + k + 1]):
            add(lows[i], "前低")

    # 3. 成交密集区（top3）
    for center, vol in volume_profile(history)[:3]:
        add(center, "筹码密集", vol)

    # 分类
    supports = []
    resistances = []
    for price, info in levels.items():
        pct = round((price - current) / current * 100, 2)
        entry = {
            "price": price,
            "strength": _strength(len(info["sources"])),
            "sources": info["sources"],
            "volume": round(info["volume"]),
            "distance_pct": pct,
        }
        if price < current:
            supports.append(entry)
        elif price > current:
            resistances.append(entry)

    # 合并距离1.2%内的价位，按距离排序
    supports = _merge(supports, current, reverse=True)
    resistances = _merge(resistances, current, reverse=False)
    return {"supports": supports[:3], "resistances": resistances[:3]}


def _merge(entries, current, tol=0.012, reverse=False):
    """合并相近价位（<1.2%），保留更近的一个并合并来源"""
    entries.sort(key=lambda x: -x["price"] if reverse else x["price"])
    out = []
    for e in entries:
        if out and abs(e["price"] - out[-1]["price"]) / out[-1]["price"] < tol:
            merged = out[-1]
            merged["sources"] = list(dict.fromkeys(merged["sources"] + e["sources"]))
            merged["strength"] = _strength(len(merged["sources"]))
            merged["volume"] = round(merged["volume"] + e["volume"])
            # 保留离当前价更近的
            if abs(e["price"] - current) < abs(merged["price"] - current):
                merged["price"] = e["price"]
            merged["distance_pct"] = round((merged["price"] - current) / current * 100, 2)
        else:
            out.append(e)
    return out


if __name__ == "__main__":
    import json
    hist = json.load(open("data/history/600036.json", "r", encoding="utf-8"))
    r = compute_levels(hist)
    print(f"当前价: {hist[-1]['close']}")
    print(f"压力位(上方):")
    for l in r["resistances"]:
        print(f"  {l['price']} ({l['strength']}) [{'+' if l['distance_pct']>0 else ''}{l['distance_pct']}%] {l['sources']}")
    print(f"支撑位(下方):")
    for l in r["supports"]:
        print(f"  {l['price']} ({l['strength']}) [{l['distance_pct']}%] {l['sources']}")
